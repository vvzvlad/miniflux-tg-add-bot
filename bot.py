import logging
import os
import json
import urllib.parse
import miniflux
import requests
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, CallbackContext
from telegram.ext import filters
from miniflux import ClientError, ServerError
import time

# Configure logging with detailed messages
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logging.getLogger("telegram.utils.request").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

# Global Miniflux client initialized from environment variables
MINIFLUX_BASE_URL = os.environ.get("MINIFLUX_BASE_URL")
MINIFLUX_USERNAME = os.environ.get("MINIFLUX_USERNAME")
MINIFLUX_PASSWORD = os.environ.get("MINIFLUX_PASSWORD")
MINIFLUX_API_KEY = os.environ.get("MINIFLUX_API_KEY")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")  # Get bot token from environment variable
RSS_BRIDGE_URL = os.environ.get("RSS_BRIDGE_URL")  # RSS bridge URL 
ADMIN_USERNAME = os.environ.get("ADMIN")
ACCEPT_CHANNELS_WITOUT_USERNAME = os.environ.get("ACCEPT_CHANNELS_WITOUT_USERNAME", "false")

miniflux_client = miniflux.Client(MINIFLUX_BASE_URL, username=MINIFLUX_USERNAME, password=MINIFLUX_PASSWORD)

def parse_telegram_link(text: str) -> str | None:
    """
    Parses a string to find and extract the channel username/ID from a t.me link.
    Handles formats like https://t.me/channel_name/123 or t.me/channel_name/123
    """
    if not text:
        return None

    # Regex to find t.me URLs
    # Handles optional https://, t.me domain, channel name (alphanumeric/underscore), message ID (numeric)
    match = re.search(r"(?:https?://)?t\\.me/([a-zA-Z0-9_]+)/(\\d+)", text) # Escaped . and d

    if match:
        channel_name = match.group(1)
        message_id = match.group(2) # We don't use message_id, but capture it
        logging.info(f"Parsed Telegram link: channel='{channel_name}', message_id='{message_id}'")
        return channel_name
    else:
        logging.debug(f"No valid t.me link found in text: '{text}'")
        return None

def fetch_categories(client):
    """
    Fetch categories from the Miniflux API using the miniflux client.
    This function accesses the API endpoint '/categories' via the client's methods.
    """
    try:
        logging.info("Requesting categories from API endpoint '/categories'")
        categories = client.get_categories()
        logging.info("Successfully fetched categories from the API")
        return categories
    except Exception as error:
        response_content = getattr(error, "text", "No response content available")
        logging.error(f"Error in fetch_categories at endpoint '/categories': {error}. Response content: {response_content}")
        raise

def check_feed_exists(client, feed_url):
    """
    Check if feed already exists in subscriptions
    """
    try:
        feeds = client.get_feeds()
        return any(feed["feed_url"] == feed_url for feed in feeds)
    except Exception as error:
        logging.error(f"Failed to check existing feeds: {error}")
        raise

def extract_channel_from_feed_url(feed_url):
    """
    Extract channel username or ID from feed URL
    """
    if not RSS_BRIDGE_URL or not feed_url.startswith(RSS_BRIDGE_URL.split("{channel}")[0]):
        return None
    
    # Handle URLs with {channel} placeholder
    if "{channel}" in RSS_BRIDGE_URL:
        base_part = RSS_BRIDGE_URL.split("{channel}")[0]
        if feed_url.startswith(base_part):
            remaining = feed_url[len(base_part):]
            # Extract until next slash or end of string
            channel = remaining.split("/")[0] if "/" in remaining else remaining
            return urllib.parse.unquote(channel)
    # Handle URLs with channel at the end
    else:
        channel = feed_url[len(RSS_BRIDGE_URL):].strip("/")
        if channel:
            return urllib.parse.unquote(channel)
    
    return None

async def start(update: Update, context: CallbackContext):
    """
    Handle the /start command.
    Only processes commands from admin user.
    """
    user = update.message.from_user
    if not user or user.username != ADMIN_USERNAME:
        logging.warning(f"Unauthorized access attempt from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return

    await update.message.reply_text("Forward me a message from a channel to subscribe to its RSS feed.")

async def list_channels(update: Update, context: CallbackContext):
    """
    Handle the /list command.
    Lists all channels subscribed through RSS Bridge.
    """
    user = update.message.from_user
    if not user or user.username != ADMIN_USERNAME:
        logging.warning(f"Unauthorized access attempt from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return

    await update.message.chat.send_action("typing")

    try:
        feeds = miniflux_client.get_feeds()
        bridge_feeds = []

        for feed in feeds:
            feed_url = feed.get("feed_url", "")
            channel = extract_channel_from_feed_url(feed_url)

            if channel:
                # Extract flags and excluded text from URL
                flags = []
                excluded_text = "" # Initialize excluded_text

                parsed_url = urllib.parse.urlparse(feed_url)
                query_params = urllib.parse.parse_qs(parsed_url.query)

                if 'exclude_flags' in query_params:
                    flags = query_params['exclude_flags'][0].split(',')

                if 'exclude_text' in query_params:
                    # Decode the URL-encoded regex
                    excluded_text = query_params['exclude_text'][0] # Already decoded by parse_qs


                bridge_feeds.append({
                    "title": feed.get("title", "Unknown"),
                    "channel": channel,
                    "feed_url": feed_url,
                    "flags": flags,
                    "excluded_text": excluded_text, # Store the decoded text
                    "category_id": feed.get("category", {}).get("id"),
                    "category_title": feed.get("category", {}).get("title", "Unknown")
                })

        if not bridge_feeds:
            await update.message.reply_text("No channels subscribed through RSS Bridge found.")
            return

        # Sort by category and then by title
        bridge_feeds.sort(key=lambda x: (x["category_title"], x["title"]))

        # Group by category
        categories = {}
        for feed in bridge_feeds:
            cat_title = feed["category_title"]
            if cat_title not in categories:
                categories[cat_title] = []
            categories[cat_title].append(feed)

        # Send messages by category to avoid message length limit
        await update.message.reply_text("Subscribed channels by category:")

        for cat_title, feeds_in_cat in categories.items(): # Renamed 'feeds' to avoid conflict
            # Build category message
            cat_message = f"📁 {cat_title}\\n" # Escaped newline
            for feed_item in feeds_in_cat: # Renamed 'feed'
                channel_name = feed_item["title"]

                feed_line = f"  • {channel_name}"

                # Add flags if present
                if feed_item["flags"]:
                    feed_line += f", flags: {' '.join(feed_item['flags'])}"

                # Add excluded text if present
                if feed_item["excluded_text"]:
                    # Ensure we display the raw decoded string
                    # Escape characters for MarkdownV2
                    safe_text = feed_item['excluded_text'].replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('`', '\\`').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('=', '\\=').replace('|', '\\|').replace('{', '\\{').replace('}', '\\}').replace('.', '\\.').replace('!', '\\!')
                    feed_line += f", regex: `{safe_text}`" # Use backticks for visibility

                cat_message += feed_line + "\\n" # Escaped newline

            # Check if message is too long (Telegram limit is 4096 chars)
            if len(cat_message) > 4000:
                 # Split into multiple messages (Refined splitting logic)
                 chunks = []
                 current_chunk = f"📁 {cat_title}\\n" # Start with category title, Escaped newline

                 for feed_item in feeds_in_cat:
                     channel_name = feed_item["title"]
                     feed_line = f"  • {channel_name}"
                     if feed_item["flags"]:
                         feed_line += f", flags: {' '.join(feed_item['flags'])}"
                     if feed_item["excluded_text"]:
                          # Escape characters for MarkdownV2
                          safe_text = feed_item['excluded_text'].replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)').replace('~', '\\~').replace('`', '\\`').replace('>', '\\>').replace('#', '\\#').replace('+', '\\+').replace('-', '\\-').replace('=', '\\=').replace('|', '\\|').replace('{', '\\{').replace('}', '\\}').replace('.', '\\.').replace('!', '\\!')
                          feed_line += f", regex: `{safe_text}`"

                     feed_text = feed_line + "\\n" # Escaped newline

                     # If adding this feed would make the chunk too long, send it and start a new one
                     # Ensure the new chunk also starts with the category title (or continued indication)
                     if len(current_chunk) + len(feed_text) > 4000:
                         chunks.append(current_chunk)
                         # Use a "continued" marker for subsequent chunks of the same category
                         current_chunk = f"📁 {cat_title} \\(continued\\)\\n" # Escaped newline and parenthesis

                     current_chunk += feed_text

                 # Add the last chunk if it has content
                 # Check if more than just the header
                 if len(current_chunk.strip()) > len(f"📁 {cat_title} \\(continued\\)\\n".strip()): # Escaped newline and parenthesis
                      chunks.append(current_chunk)

                 # Send all chunks
                 for chunk in chunks:
                     # Use markdown parse mode for backticks
                     await update.message.reply_text(chunk, parse_mode='MarkdownV2')
            else:
                # Send as a single message
                # Use markdown parse mode for backticks
                await update.message.reply_text(cat_message, parse_mode='MarkdownV2') # Use MarkdownV2 for backticks

    except Exception as error:
        logging.error(f"Failed to list channels: {error}", exc_info=True)
        await update.message.reply_text(f"Failed to list channels: {str(error)}")

async def handle_message(update: Update, context: CallbackContext):
    """
    Handle incoming messages in private chat.
    If the message is forwarded from a channel OR contains a link to a channel message,
    fetch categories and ask user to select one.
    If the state is 'awaiting_regex', update the regex for the specified channel.
    Only processes messages from admin user.
    """
    msg = update.message
    if not msg:
        return

    user = msg.from_user
    if not user or user.username != ADMIN_USERNAME:
        logging.warning(f"Unauthorized access attempt from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return

    # Check if this message is part of a media group we've already processed
    media_group_id = msg.media_group_id
    if media_group_id and context.user_data.get("processed_media_group_id") == media_group_id:
        logging.info(f"Skipping duplicate message from media group {media_group_id}")
        return

    msg_dict = msg.to_dict()
    logging.info(f"Message details:\\n{json.dumps(msg_dict, indent=4)}") # Escaped newline

    channel_username = None
    channel_source_type = None # 'forward' or 'link'

    # 1. Check for forward
    forward_chat = getattr(msg, 'forward_from_chat', None)
    if forward_chat and forward_chat.type == "channel":
        logging.info(f"Processing forwarded message from channel: {forward_chat.username or forward_chat.id}") # Use attributes
        accept_no_username = ACCEPT_CHANNELS_WITOUT_USERNAME.lower() == "true"
        if not forward_chat.username and not accept_no_username: # Use attribute
             logging.error(f"Channel {forward_chat.title} has no username") # Use attribute
             await update.message.reply_text("Error: channel must have a public username to subscribe. \\nUse env ACCEPT_CHANNELS_WITOUT_USERNAME=true to accept channels without username (need support from RSS bridge).") # Escaped newline
             return
        channel_username = forward_chat.username or str(forward_chat.id) # Use attributes
        channel_source_type = 'forward'
        # If this is part of a media group from a forward, mark it as processed
        if media_group_id:
            context.user_data["processed_media_group_id"] = media_group_id
            logging.info(f"Processing first forwarded message from media group {media_group_id}")

    # 2. If not a forward, check for link in message text
    elif msg.text:
        parsed_channel = parse_telegram_link(msg.text)
        if parsed_channel:
            logging.info(f"Processing link to message from channel: {parsed_channel}")
            # Here we assume public channels linked will have usernames discoverable by RSS Bridge.
            # Handling private channels or those without usernames via links might require different logic or fail at the bridge level.
            channel_username = parsed_channel
            channel_source_type = 'link'
             # If this is part of a media group from a link, mark it as processed
            if media_group_id:
                 context.user_data["processed_media_group_id"] = media_group_id
                 logging.info(f"Processing first linked message from media group {media_group_id}")


    # 3. If neither forward nor valid link
    if not channel_username:
        logging.info("Message is not a forward from a channel or a valid channel link.")
        # Updated prompt
        await update.message.reply_text("Please forward a message from a channel or send a link to a message from a public channel (e.g., https://t.me/channel_name/123).")
        return

    # Store channel title/username for later use
    context.user_data["channel_title"] = channel_username
    logging.info(f"Processing channel identified as: {channel_username} (Source: {channel_source_type})")

    await update.message.chat.send_action("typing")

    # --- Check for state: awaiting_regex ---
    if context.user_data.get('state') == 'awaiting_regex':
        channel_name = context.user_data.get('editing_regex_for_channel')
        feed_id = context.user_data.get('editing_feed_id')
        new_regex_raw = msg.text.strip() if msg.text else ""

        # Clean up state regardless of success/failure below
        if 'state' in context.user_data: del context.user_data['state']
        if 'editing_regex_for_channel' in context.user_data: del context.user_data['editing_regex_for_channel']
        if 'editing_feed_id' in context.user_data: del context.user_data['editing_feed_id']
        logging.info(f"Processing new regex for channel {channel_name} (feed ID: {feed_id}). State cleared.")

        if not channel_name or not feed_id:
            logging.error("State 'awaiting_regex' was set, but channel_name or feed_id missing from context.")
            await update.message.reply_text("Error: Missing context for regex update. Please try editing again.")
            return # Stop processing this message

        await update.message.chat.send_action("typing")

        try:
            # Fetch the current feed URL again to build the new one
            current_feed_data = miniflux_client.get_feed(feed_id)
            current_url = current_feed_data.get("feed_url", "")
            if not current_url:
                 logging.error(f"Could not retrieve current URL for feed {feed_id} ({channel_name}) before updating regex.")
                 await update.message.reply_text("Error: Could not retrieve current feed URL. Cannot update regex.")
                 return

            logging.info(f"Current URL for {channel_name} (feed ID: {feed_id}): {current_url}")

            # Determine if removing or updating the regex
            remove_regex = new_regex_raw.lower() in ['none', '-']
            new_regex_encoded = "" if remove_regex else urllib.parse.quote(new_regex_raw)

            # Construct the new URL
            parsed_url = urllib.parse.urlparse(current_url)
            query_params = urllib.parse.parse_qs(parsed_url.query, keep_blank_values=True) # Keep blank values

            if remove_regex:
                if 'exclude_text' in query_params:
                    del query_params['exclude_text']
                    logging.info(f"Removing exclude_text parameter for {channel_name}.")
            elif new_regex_encoded: # Only add/update if new regex is not empty
                query_params['exclude_text'] = [new_regex_encoded] # Set or overwrite
                logging.info(f"Setting/updating exclude_text parameter for {channel_name} to encoded value: {new_regex_encoded}")
            else:
                # User sent empty message but not 'none' or '-', maybe ignore or treat as remove?
                # Let's treat empty as remove for simplicity. If user wants empty regex, it is strange anyway.
                if 'exclude_text' in query_params:
                    del query_params['exclude_text']
                    logging.info(f"Removing exclude_text parameter for {channel_name} due to empty input.")

            # Rebuild the URL
            new_query_string = urllib.parse.urlencode(query_params, doseq=True)
            new_url = urllib.parse.urlunparse((
                parsed_url.scheme,
                parsed_url.netloc,
                parsed_url.path,
                parsed_url.params,
                new_query_string,
                parsed_url.fragment
            ))

            logging.info(f"Constructed new URL for {channel_name} (feed ID: {feed_id}): {new_url}")

            # Update the feed URL using the existing function
            success, updated_url_from_miniflux, error_message = update_feed_url(feed_id, new_url)

            if success:
                if remove_regex or not new_regex_encoded:
                     final_message = f"Regex filter removed for channel @{channel_name}."
                else:
                     # Escape for display
                     safe_new_regex = new_regex_raw.replace('_', '\\\\_').replace('*', '\\\\*').replace('[', '\\\\[').replace(']', '\\\\]').replace('(', '\\\\(').replace(')', '\\\\)').replace('~', '\\\\~').replace('`', '\\\\`').replace('>', '\\\\>').replace('#', '\\\\#').replace('+', '\\\\+').replace('-', '\\\\-').replace('=', '\\\\=').replace('|', '\\\\|').replace('{', '\\\\{').replace('}', '\\\\}').replace('.', '\\\\.').replace('!', '\\\\!')
                     final_message = f"Regex for channel @{channel_name} updated to:\\n`{safe_new_regex}`"
                await update.message.reply_text(final_message, parse_mode='MarkdownV2')

                # --- Optional: Show the flag keyboard again ---
                # Fetch current flags to display the keyboard correctly after update
                try:
                    updated_feed_after_regex = miniflux_client.get_feed(feed_id)
                    feed_url_after_regex = updated_feed_after_regex.get("feed_url", "")
                    current_flags_after_regex = []
                    parsed_url_after = urllib.parse.urlparse(feed_url_after_regex)
                    query_params_after = urllib.parse.parse_qs(parsed_url_after.query)
                    if 'exclude_flags' in query_params_after:
                        current_flags_after_regex = query_params_after['exclude_flags'][0].split(',')

                    keyboard = create_flag_keyboard(channel_name, current_flags_after_regex)
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.message.reply_text(f"Updated options for @{channel_name}:", reply_markup=reply_markup)
                    logging.info(f"Displayed updated options keyboard for {channel_name} after regex update.")

                except Exception as e_flags:
                    logging.error(f"Failed to fetch flags/show keyboard after regex update for {channel_name}: {e_flags}")
                    # Non-critical error, just log it. The main update succeeded.

            else:
                logging.error(f"Failed to update feed URL for {channel_name} (feed ID: {feed_id}) with new regex. Error: {error_message}. Attempted URL: {new_url}")
                await update.message.reply_text(f"Failed to update regex for channel @{channel_name}. Miniflux error: {error_message}")

        except Exception as e:
            logging.error(f"Error processing new regex for {channel_name}: {e}", exc_info=True)
            await update.message.reply_text(f"An unexpected error occurred while updating the regex: {str(e)}")

        return # Important: Stop processing after handling the state

    # --- Existing logic for forwards/links starts here ---
    # (Make sure the code above is placed *before* this section)

    # Check if this message is part of a media group we've already processed
    media_group_id = msg.media_group_id
    if media_group_id:
        context.user_data["processed_media_group_id"] = media_group_id
        logging.info(f"Processing first message from media group {media_group_id}")

    # Store channel title/username for later use
    context.user_data["channel_title"] = channel_username
    logging.info(f"Processing channel identified as: {channel_username} (Source: {channel_source_type})")

    await update.message.chat.send_action("typing")
    try:
        feeds = miniflux_client.get_feeds()
        target_feed = None

        # --- Simplified Check --- 
        # Compare the target channel name with the channel name extracted from existing feed URLs
        for feed in feeds:
            feed_url_check = feed.get("feed_url", "")
            existing_channel_name = extract_channel_from_feed_url(feed_url_check)
            
            # Compare case-insensitively
            if existing_channel_name and channel_username.lower() == existing_channel_name.lower():
                target_feed = feed
                logging.info(f"Found existing feed for channel '{channel_username}': ID={feed.get('id')}, URL={feed_url_check}")
                break # Found a match

        if target_feed:
            logging.info(f"Channel @{channel_username} is already in subscriptions (matched channel name)")

            # Fetch current flags for the existing feed
            feed_id = target_feed.get("id")
            try:
                # Get the most up-to-date feed data to ensure flags are current
                updated_target_feed = miniflux_client.get_feed(feed_id)
                feed_url_current = updated_target_feed.get("feed_url", "")
                current_flags = []
                if "exclude_flags=" in feed_url_current:
                    flags_part = feed_url_current.split("exclude_flags=")[1].split("&")[0]
                    current_flags = flags_part.split(",")
                logging.info(f"Current flags for @{channel_username}: {current_flags}")
            except Exception as e:
                logging.error(f"Failed to fetch current flags for feed {feed_id}: {e}")
                await update.message.reply_text("Error fetching current flag status. Proceeding without status.")
                current_flags = [] # Proceed without flag status if fetch fails

            # Create keyboard with current flag statuses
            keyboard = create_flag_keyboard(channel_username, current_flags)
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"Channel @{channel_username} is already in subscriptions. Choose an action:",
                reply_markup=reply_markup
            )
            return

    except Exception as error:
        logging.error(f"Failed to check subscriptions or get existing feed details: {error}", exc_info=True)
        await update.message.reply_text("Failed to check existing subscriptions.")
        return

    # --- Feed does not exist, proceed with category selection ---
    try:
        categories = fetch_categories(miniflux_client)
    except Exception as error:
        logging.error(f"Failed to fetch categories: {error}")
        await update.message.reply_text("Failed to fetch categories from RSS reader.")
        return

    # Build inline keyboard with categories
    keyboard = []
    categories_dict = {}  # Store category information
    for category in categories:
        cat_title = category.get("title", "Unknown")
        cat_id = category.get("id")
        categories_dict[cat_id] = cat_title  # Store id -> title mapping
        keyboard.append([InlineKeyboardButton(cat_title, callback_data=f"cat_{cat_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)

    context.user_data["categories"] = categories_dict  # Save to context

    await update.message.reply_text(
        f"Select category for channel @{channel_username}:", reply_markup=reply_markup
    )

async def button_callback(update: Update, context: CallbackContext):
    """
    Handle callback query when user selects a category or flag action.
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("cat_"):
        cat_id_str = data.split("_", 1)[1]
        try:
            cat_id = int(cat_id_str)
        except ValueError:
            await query.edit_message_text("Invalid category ID.")
            return

        channel_title = context.user_data.get("channel_title")
        if not channel_title:
            await query.edit_message_text("Channel information is missing.")
            return

        encoded_channel_title = urllib.parse.quote(channel_title)
        feed_url = RSS_BRIDGE_URL.replace("{channel}", encoded_channel_title) if "{channel}" in RSS_BRIDGE_URL else f"{RSS_BRIDGE_URL}/{encoded_channel_title}"
        
        await query.message.chat.send_action("typing")
        try:
            logging.info(f"Subscribing to feed '{feed_url}' in category {cat_id}")
            miniflux_client.create_feed(feed_url, category_id=cat_id)
            category_title = context.user_data.get("categories", {}).get(cat_id, "Unknown")
            await query.edit_message_text(
                f"Channel @{channel_title} has been successfully subscribed on {MINIFLUX_BASE_URL} reader instance, added to category '{category_title.strip()}', used RSS bridge {feed_url}"
            )
        except (ClientError, ServerError) as error:
            status_code = getattr(error, 'status_code', 'unknown')
            try:
                error_reason = error.get_error_reason()
            except AttributeError:
                error_reason = str(error)
            
            error_message = f"Status: {status_code}, Error: {error_reason}"
            logging.error(f"Miniflux API error while subscribing to feed '{feed_url}': {error_message}")
            await query.edit_message_text(f"Failed to subscribe to RSS feed '{feed_url}': {error_message}")
        except Exception as error:
            logging.error(f"Unexpected error while subscribing to feed '{feed_url}': {str(error)}", exc_info=True)
            await query.edit_message_text(f"Unexpected error while subscribing to RSS feed: {str(error)}")
    
    elif data.startswith("add_flag|"):
        # Handle add flag button
        parts = data.split("|")
        if len(parts) < 3:
            await query.edit_message_text("Invalid flag data.")
            return

        channel_name = parts[1]
        flag_name = parts[2]

        # Call the shared function for adding flags
        success, message, updated_flags = await add_flag_to_channel(channel_name, flag_name)

        if success:
            # Create keyboard with updated flag statuses
            keyboard = create_flag_keyboard(channel_name, updated_flags)
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"{message}\n\nChoose an action:",
                reply_markup=reply_markup
            )
        else:
            # If adding failed (e.g., already exists), still show the current state
            # Fetch current flags again in case of race conditions or errors
            try:
                feeds = miniflux_client.get_feeds()
                target_feed = None
                for feed in feeds:
                    feed_url = feed.get("feed_url", "")
                    channel = extract_channel_from_feed_url(feed_url)
                    if channel and channel.lower() == channel_name.lower():
                        target_feed = feed
                        break
                
                current_flags = []
                if target_feed:
                    feed_id = target_feed.get("id")
                    updated_target_feed = miniflux_client.get_feed(feed_id)
                    feed_url = updated_target_feed.get("feed_url", "")
                    if "exclude_flags=" in feed_url:
                        flags_part = feed_url.split("exclude_flags=")[1].split("&")[0]
                        current_flags = flags_part.split(",")
                
                keyboard = create_flag_keyboard(channel_name, current_flags)
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"{message}\n\nChoose an action:", 
                    reply_markup=reply_markup
                )
            except Exception as e:
                logging.error(f"Error refreshing keyboard after failed flag add: {e}")
                await query.edit_message_text(message) # Show original error message

    elif data.startswith("remove_flag|"):
        # Handle remove flag button
        parts = data.split("|")
        if len(parts) < 3:
            await query.edit_message_text("Invalid flag data.")
            return

        channel_name = parts[1]
        flag_name = parts[2]

        # Call the shared function for removing flags
        success, message, updated_flags = await remove_flag_from_channel(channel_name, flag_name)

        if success:
            # Create keyboard with updated flag statuses
            keyboard = create_flag_keyboard(channel_name, updated_flags)
            reply_markup = InlineKeyboardMarkup(keyboard)

            await query.edit_message_text(
                f"{message}\n\nChoose an action:",
                reply_markup=reply_markup
            )
        else:
            # If removing failed (e.g., doesn't exist), still show the current state
            # Fetch current flags again
            try:
                feeds = miniflux_client.get_feeds()
                target_feed = None
                for feed in feeds:
                    feed_url = feed.get("feed_url", "")
                    channel = extract_channel_from_feed_url(feed_url)
                    if channel and channel.lower() == channel_name.lower():
                        target_feed = feed
                        break
                
                current_flags = []
                if target_feed:
                    feed_id = target_feed.get("id")
                    updated_target_feed = miniflux_client.get_feed(feed_id)
                    feed_url = updated_target_feed.get("feed_url", "")
                    if "exclude_flags=" in feed_url:
                        flags_part = feed_url.split("exclude_flags=")[1].split("&")[0]
                        current_flags = flags_part.split(",")
                
                keyboard = create_flag_keyboard(channel_name, current_flags)
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(
                    f"{message}\n\nChoose an action:", 
                    reply_markup=reply_markup
                )
            except Exception as e:
                logging.error(f"Error refreshing keyboard after failed flag remove: {e}")
                await query.edit_message_text(message) # Show original error message

    elif data.startswith("delete|"):
        # Handle delete channel button
        channel_name = data.split("|", 1)[1]
        
        await query.message.chat.send_action("typing")
        try:
            # Get all feeds
            feeds = miniflux_client.get_feeds()
            target_feed = None
            
            # Find the feed for the specified channel
            for feed in feeds:
                feed_url = feed.get("feed_url", "")
                channel = extract_channel_from_feed_url(feed_url)
                
                if channel and channel.lower() == channel_name.lower():
                    target_feed = feed
                    break
            
            if not target_feed:
                await query.edit_message_text(f"Channel @{channel_name} not found in subscriptions.")
                return
            
            # Get the feed ID
            feed_id = target_feed.get("id")
            
            # Delete the feed
            miniflux_client.delete_feed(feed_id)
            
            await query.edit_message_text(f"Channel @{channel_name} has been deleted from subscriptions.")
            
        except Exception as e:
            logging.error(f"Failed to delete feed for {channel_name}: {e}", exc_info=True)
            await query.edit_message_text(f"Failed to delete channel: {str(e)}")

    elif data.startswith("edit_regex|"):
        # Handle edit regex button
        channel_name = data.split("|", 1)[1]

        await query.message.chat.send_action("typing")
        try:
            # Find the feed for the channel
            feeds = miniflux_client.get_feeds()
            target_feed = None
            feed_url = ""
            feed_id = None
            current_regex = ""

            for feed in feeds:
                feed_url_check = feed.get("feed_url", "")
                channel = extract_channel_from_feed_url(feed_url_check)
                if channel and channel.lower() == channel_name.lower():
                    target_feed = feed
                    feed_id = feed.get("id")
                    # Fetch the most up-to-date feed data to get the current URL accurately
                    if not feed_id: # Basic check if feed_id was found
                        logging.warning(f"Feed ID not found for channel {channel_name} during regex edit prep.")
                        continue # Should ideally not happen if target_feed is set, but good practice
                    try:
                        updated_target_feed = miniflux_client.get_feed(feed_id)
                        feed_url = updated_target_feed.get("feed_url", "")
                        logging.info(f"Fetched current feed URL for {channel_name} (ID: {feed_id}): {feed_url}")
                    except Exception as fetch_error:
                         logging.error(f"Failed to fetch feed details for {feed_id} ({channel_name}) during regex edit prep: {fetch_error}")
                         await query.edit_message_text(f"Error fetching current feed details for @{channel_name}.")
                         return # Stop if we can't get the current URL
                    break # Found and fetched feed, exit loop

            if not target_feed or not feed_id:
                logging.warning(f"Target feed or feed_id not found for {channel_name} after searching feeds.")
                await query.edit_message_text(f"Channel @{channel_name} not found in subscriptions or feed ID missing.")
                return

            # Extract current regex from the feed URL
            parsed_url = urllib.parse.urlparse(feed_url)
            query_params = urllib.parse.parse_qs(parsed_url.query)
            if 'exclude_text' in query_params:
                current_regex = query_params['exclude_text'][0] # Already decoded by parse_qs
                logging.info(f"Found current regex for {channel_name}: '{current_regex}'")
            else:
                 logging.info(f"No current exclude_text regex found for {channel_name}")


            # Store necessary info and set state for the next message handler
            context.user_data['state'] = 'awaiting_regex'
            context.user_data['editing_regex_for_channel'] = channel_name
            context.user_data['editing_feed_id'] = feed_id # Store feed_id too
            logging.info(f"Set state to 'awaiting_regex' for channel {channel_name} (feed ID: {feed_id})")

            # Prepare message asking for new regex
            prompt_message = ""
            if current_regex:
                 # Escape characters for MarkdownV2
                 safe_regex = current_regex.replace('_', '\\\\_').replace('*', '\\\\*').replace('[', '\\\\[').replace(']', '\\\\]').replace('(', '\\\\(').replace(')', '\\\\)').replace('~', '\\\\~').replace('`', '\\\\`').replace('>', '\\\\>').replace('#', '\\\\#').replace('+', '\\\\+').replace('-', '\\\\-').replace('=', '\\\\=').replace('|', '\\\\|').replace('{', '\\\\{').replace('}', '\\\\}').replace('.', '\\\\.').replace('!', '\\\\!')
                 prompt_message = f"Current regex for @{channel_name} is:\\n`{safe_regex}`\\n\\nPlease send the new regex. Send 'none' or '-' to remove the regex filter."
            else:
                 prompt_message = f"No current regex set for @{channel_name}.\\nPlease send the new regex. Send 'none' or '-' to remove the regex filter."

            # Edit the original message with the prompt
            # Important: We edit the message from the *button callback* context (query.message)
            await query.edit_message_text(prompt_message, parse_mode='MarkdownV2') # Use MarkdownV2

        except Exception as e:
            logging.error(f"Failed during edit_regex preparation for {channel_name}: {e}", exc_info=True)
            # Reset state if error occurs during preparation
            if 'state' in context.user_data:
                del context.user_data['state']
            if 'editing_regex_for_channel' in context.user_data:
                del context.user_data['editing_regex_for_channel']
            if 'editing_feed_id' in context.user_data:
                del context.user_data['editing_feed_id']
            await query.edit_message_text(f"Failed to start regex edit: {str(e)}")

async def add_flag_to_channel(channel_name, flag_to_add):
    """
    Add a flag to a channel subscription.
    
    Args:
        channel_name: Channel username or ID
        flag_to_add: Flag to add
        
    Returns:
        tuple: (success, message, updated_flags)
    """
    try:
        # Get all feeds
        feeds = miniflux_client.get_feeds()
        target_feed = None
        
        # Find the feed for the specified channel
        for feed in feeds:
            feed_url = feed.get("feed_url", "")
            channel = extract_channel_from_feed_url(feed_url)
            
            if channel and channel.lower() == channel_name.lower():
                target_feed = feed
                break
        
        if not target_feed:
            return False, f"Channel @{channel_name} not found in subscriptions.", []
        
        # Get the most up-to-date feed data
        feed_id = target_feed.get("id")
        updated_target_feed = miniflux_client.get_feed(feed_id)
        feed_url = updated_target_feed.get("feed_url", "")
        
        # Parse current flags
        current_flags = []
        if "exclude_flags=" in feed_url:
            flags_part = feed_url.split("exclude_flags=")[1].split("&")[0]
            current_flags = flags_part.split(",")
        
        # Check if flag already exists
        if flag_to_add in current_flags:
            return False, f"Flag '{flag_to_add}' is already set for channel @{channel_name}.", current_flags
        
        # Add new flag
        current_flags.append(flag_to_add)
        
        # Create new URL with updated flags
        new_url = feed_url
        if "exclude_flags=" in feed_url:
            # Replace existing flags
            flags_str = ",".join(current_flags)
            parts = feed_url.split("exclude_flags=")
            rest = parts[1].split("&", 1)
            if len(rest) > 1:
                new_url = f"{parts[0]}exclude_flags={flags_str}&{rest[1]}"
            else:
                new_url = f"{parts[0]}exclude_flags={flags_str}"
        else:
            # Add flags parameter
            flags_str = ",".join(current_flags)
            if "?" in feed_url:
                new_url = f"{feed_url}&exclude_flags={flags_str}"
            else:
                new_url = f"{feed_url}?exclude_flags={flags_str}"
        
        # Add logging before and after updating URL
        logging.info(f"Original feed URL: {feed_url}")
        logging.info(f"New feed URL: {new_url}")

        # Update feed URL
        success, updated_url, _ = update_feed_url(feed_id, new_url)
        
        if not success:
            return False, f"Failed to update feed URL. Miniflux may be ignoring URL parameters.\nPlease update the URL manually in the Miniflux interface:\n{new_url}", []
        
        # Extract updated flags from the updated URL
        updated_flags = []
        if "exclude_flags=" in updated_url:
            flags_part = updated_url.split("exclude_flags=")[1].split("&")[0]
            updated_flags = flags_part.split(",")
        
        # Display updated flags separated by spaces
        flags_display = " ".join(updated_flags)
        
        return True, f"Added flag '{flag_to_add}' to channel @{channel_name}.\nCurrent flags: {flags_display}", updated_flags
        
    except Exception as e:
        logging.error(f"Failed to update feed: {e}", exc_info=True)
        return False, f"Failed to add flag: {str(e)}", []

async def remove_flag_from_channel(channel_name, flag_to_remove):
    """
    Remove a flag from a channel subscription.
    
    Args:
        channel_name: Channel username or ID
        flag_to_remove: Flag to remove
        
    Returns:
        tuple: (success, message, updated_flags)
    """
    try:
        # Get all feeds
        feeds = miniflux_client.get_feeds()
        target_feed = None
        
        # Find the feed for the specified channel
        for feed in feeds:
            feed_url = feed.get("feed_url", "")
            channel = extract_channel_from_feed_url(feed_url)
            
            if channel and channel.lower() == channel_name.lower():
                target_feed = feed
                break
        
        if not target_feed:
            return False, f"Channel @{channel_name} not found in subscriptions.", []
        
        # Get the most up-to-date feed data
        feed_id = target_feed.get("id")
        updated_target_feed = miniflux_client.get_feed(feed_id)
        feed_url = updated_target_feed.get("feed_url", "")
        
        # Parse current flags
        current_flags = []
        if "exclude_flags=" in feed_url:
            flags_part = feed_url.split("exclude_flags=")[1].split("&")[0]
            current_flags = flags_part.split(",")
        
        # Check if flag exists
        if flag_to_remove not in current_flags:
            return False, f"Flag '{flag_to_remove}' is not set for channel @{channel_name}.", current_flags
        
        # Remove flag
        current_flags.remove(flag_to_remove)
        
        # Create new URL with updated flags
        new_url = feed_url
        if current_flags:
            # Replace existing flags
            flags_str = ",".join(current_flags)
            parts = feed_url.split("exclude_flags=")
            rest = parts[1].split("&", 1)
            if len(rest) > 1:
                new_url = f"{parts[0]}exclude_flags={flags_str}&{rest[1]}"
            else:
                new_url = f"{parts[0]}exclude_flags={flags_str}"
        else:
            # Remove flags parameter entirely
            parts = feed_url.split("exclude_flags=")
            rest = parts[1].split("&", 1)
            if len(rest) > 1:
                new_url = f"{parts[0]}{rest[1]}"
            else:
                # Remove the query parameter separator if it's the only parameter
                new_url = parts[0].rstrip("?&")
        
        # Add logging before and after updating URL
        logging.info(f"Original feed URL: {feed_url}")
        logging.info(f"New feed URL: {new_url}")

        # Update feed URL
        success, updated_url, _ = update_feed_url(feed_id, new_url)
        
        if not success:
            return False, f"Failed to update feed URL. Miniflux may be ignoring URL parameters.\nPlease update the URL manually in the Miniflux interface:\n{new_url}", []
        
        # Extract updated flags from the updated URL
        updated_flags = []
        if "exclude_flags=" in updated_url:
            flags_part = updated_url.split("exclude_flags=")[1].split("&")[0]
            updated_flags = flags_part.split(",")
        
        # Display updated flags separated by spaces
        flags_display = " ".join(updated_flags) if updated_flags else "none"
        
        return True, f"Removed flag '{flag_to_remove}' from channel @{channel_name}.\nCurrent flags: {flags_display}", updated_flags
        
    except Exception as e:
        logging.error(f"Failed to update feed: {e}", exc_info=True)
        return False, f"Failed to remove flag: {str(e)}", []

def create_flag_keyboard(channel_name, current_flags):
    """
    Create keyboard with flag options, showing current status (✅/❌),
    and an option to edit the exclude_text regex.

    Args:
        channel_name: Channel username or ID
        current_flags: List of currently set flags

    Returns:
        list: Keyboard buttons
    """
    all_flags = [
        "fwd", "video", "stream", "donat", "clown", "poo",
        "advert", "link", "mention", "hid_channel", "foreign_channel"
    ]

    keyboard = []
    row = []

    for i, flag in enumerate(all_flags):
        if flag in current_flags:
            # Flag is set, show ❌ and action to remove
            button_text = f"❌ {flag}"
            callback_action = f"remove_flag|{channel_name}|{flag}"
        else:
            # Flag is not set, show ✅ and action to add
            button_text = f"✅ {flag}"
            callback_action = f"add_flag|{channel_name}|{flag}"

        row.append(InlineKeyboardButton(button_text, callback_data=callback_action))

        # Add 2 buttons per row, or if it's the last flag
        if len(row) == 2 or i == len(all_flags) - 1:
            keyboard.append(row)
            row = []

    # Add Edit Regex button
    keyboard.append([InlineKeyboardButton("Edit Regex", callback_data=f"edit_regex|{channel_name}")])

    # Add delete button at the bottom
    keyboard.append([InlineKeyboardButton("Delete channel", callback_data=f"delete|{channel_name}")])

    return keyboard

def update_feed_url(feed_id, new_url):
    """
    Update feed URL in Miniflux.
    
    Args:
        feed_id: ID of the feed to update
        new_url: New URL to set for the feed
        
    Returns:
        tuple: (success, updated_url, response_text)
    """
    try:
        logging.info(f"Attempting to update feed ID {feed_id} URL to: {new_url}")
        # Update feed with new URL
        miniflux_client.update_feed(feed_id=feed_id, feed_url=new_url)
        logging.info(f"Update request sent for feed ID {feed_id}. Waiting 2 seconds before verification.")

        # Add a short delay to allow Miniflux to process the update
        time.sleep(2)

        # Get the updated feed to verify the change
        logging.info(f"Verifying URL update for feed ID {feed_id}")
        updated_feed = miniflux_client.get_feed(feed_id)
        updated_url = updated_feed.get("feed_url", "")
        logging.info(f"Verified feed URL for feed ID {feed_id}: {updated_url}")
        
        # Check if the URL was actually updated
        if updated_url == new_url:
            logging.info(f"URL for feed ID {feed_id} successfully updated to: {updated_url}")
            return True, updated_url, ""
        else:
            # Log the discrepancy clearly
            logging.error(f"URL update verification failed for feed ID {feed_id}! Expected URL: '{new_url}', but received URL: '{updated_url}'. Miniflux might not have processed the update in time or ignored URL parameters.")
            # Consider returning True but with a warning, or False depending on desired behavior
            # For now, returning False as the verification failed.
            return False, updated_url, f"Verification failed: Expected '{new_url}', got '{updated_url}'"
            
    except Exception as e:
        logging.error(f"Failed to update feed ID {feed_id}: {e}", exc_info=True)
        # It's better to re-raise the exception or return specific error info
        # Returning False might hide the actual error cause
        return False, "", f"Exception during update: {str(e)}"

async def add_flag(update: Update, context: CallbackContext):
    """
    Handle the /add_flag command.
    Adds a new flag to an existing channel subscription.
    Format: /add_flag channel_name flag_name
    """
    user = update.message.from_user
    if not user or user.username != ADMIN_USERNAME:
        logging.warning(f"Unauthorized access attempt from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return
    
    # Check if command has correct arguments
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /add_flag channel_name flag_name")
        return
    
    channel_name = context.args[0].lstrip('@')
    flag_to_add = context.args[1].strip()
    
    await update.message.chat.send_action("typing")
    
    # Call the shared function for adding flags
    _success, message, _ = await add_flag_to_channel(channel_name, flag_to_add)
    await update.message.reply_text(message)

async def remove_flag(update: Update, context: CallbackContext):
    """
    Handle the /remove_flag command.
    Removes a flag from an existing channel subscription.
    Format: /remove_flag channel_name flag_name
    """
    user = update.message.from_user
    if not user or user.username != ADMIN_USERNAME:
        logging.warning(f"Unauthorized access attempt from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return
    
    # Check if command has correct arguments
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /remove_flag channel_name flag_name")
        return
    
    channel_name = context.args[0].lstrip('@')
    flag_to_remove = context.args[1].strip()
    
    await update.message.chat.send_action("typing")
    
    # Call the shared function for removing flags
    _success, message, _ = await remove_flag_from_channel(channel_name, flag_to_remove)
    await update.message.reply_text(message)

def main():
    """
    Initialize the Telegram bot and register handlers.
    """
    # Define setup_commands function
    async def post_init(application):
        """Set up bot commands after initialization"""
        try:
            commands = [
                ("start", "Start working with the bot"),
                ("list", "Show list of subscribed channels"),
                ("add_flag", "Add flag to channel: /add_flag channel flag"),
                ("remove_flag", "Remove flag from channel: /remove_flag channel flag")
            ]
            await application.bot.set_my_commands(commands)
            logging.info("Bot commands have been set up successfully")
        except Exception as e:
            logging.error(f"Failed to set up bot commands: {e}")
    
    # Build application with post_init hook
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .post_init(post_init)
        .build()
    )
    
    # Register command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_channels))
    application.add_handler(CommandHandler("add_flag", add_flag))
    application.add_handler(CommandHandler("remove_flag", remove_flag))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()

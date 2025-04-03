import logging
import json
import urllib.parse
import sys # Import sys for exit
import re

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler, CallbackContext
from telegram.ext import filters
from miniflux import ClientError, ServerError

# Import config variables and client, handle potential import error state
from config import (
    MINIFLUX_BASE_URL, TELEGRAM_TOKEN, RSS_BRIDGE_URL,
    miniflux_client, is_admin,
    should_accept_channels_without_username
)

# Remove module-level check to allow mocking in tests
# Now it will be checked in main() instead

from miniflux_api import fetch_categories, check_feed_exists, update_feed_url as update_feed_url_api, get_channels_by_category

from url_utils import parse_telegram_link, is_valid_rss_url
from url_constructor import parse_feed_url, build_feed_url

async def start(update: Update, _context: CallbackContext):
    """
    Handle the /start command.
    Only processes commands from admin user.
    """
    user = update.message.from_user
    if not user or not is_admin(user.username):
        logging.warning(f"Unauthorized access attempt for /start from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return

    await update.message.reply_text("Forward me a message from any channel (public or private) or send a link to a message to subscribe to its RSS feed.")

async def list_channels(update: Update, _context: CallbackContext):
    """
    Handle the /list command.
    Fetches structured channel data and formats it for Telegram display.
    """
    user = update.message.from_user
    if not user or not is_admin(user.username):
        logging.warning(f"Unauthorized access attempt for /list from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return

    await update.message.chat.send_action("typing")

    try:
        # Call the function to get structured data
        channels_by_category = get_channels_by_category(miniflux_client)

        if not channels_by_category:
            await update.message.reply_text("No channels subscribed through RSS Bridge found.")
            return

        # --- Formatting logic moved here ---
        await update.message.reply_text("Subscribed channels by category:")

        for cat_title, feeds_in_cat in channels_by_category.items():
            # Build category message
            cat_message_base = f"📁 {cat_title}\n"
            cat_message_content = ""

            for feed_item in feeds_in_cat:
                channel_name = feed_item["title"]
                # Basic Markdown V2 escaping for channel title itself might be needed
                # but let's assume titles are generally safe for simplicity first.
                feed_line = f"  • {channel_name}"

                # Add flags if present
                if feed_item["flags"]:
                    feed_line += f", flags: {' '.join(feed_item['flags'])}"

                # Add excluded text if present, with MarkdownV2 escaping
                if feed_item["excluded_text"]:
                    # Define characters to escape for MarkdownV2
                    md_escape_chars = '_*[]()~`>#+-=|{}.!' # Corrected list
                    escaped_text = feed_item['excluded_text']
                    for char in md_escape_chars:
                        escaped_text = escaped_text.replace(char, f'\\{char}')
                    feed_line += f", regex: `{escaped_text}`"

                cat_message_content += feed_line + "\n"

            # Check if message is too long (Telegram limit is 4096 chars)
            full_cat_message = cat_message_base + cat_message_content
            if len(full_cat_message) > 4000:
                # Split into multiple messages
                chunks = []
                current_chunk = cat_message_base # Start with category title

                # Re-iterate over the items in this category to build chunks
                for feed_item in feeds_in_cat:
                    channel_name = feed_item["title"]
                    feed_line = f"  • {channel_name}"
                    if feed_item["flags"]:
                        feed_line += f", flags: {' '.join(feed_item['flags'])}"
                    if feed_item["excluded_text"]:
                        md_escape_chars = '_*[]()~`>#+-=|{}.!'
                        escaped_text = feed_item['excluded_text']
                        for char in md_escape_chars:
                            escaped_text = escaped_text.replace(char, f'\\{char}')
                        feed_line += f", regex: `{escaped_text}`"

                    feed_text_line = feed_line + "\n"

                    # Check if adding the next line exceeds the limit
                    if len(current_chunk) + len(feed_text_line) > 4000:
                        chunks.append(current_chunk)
                        # Start new chunk with continued title
                        current_chunk = f"📁 {cat_title} (continued)\n"

                    current_chunk += feed_text_line

                # Add the last remaining chunk
                if len(current_chunk.strip()) > len(f"📁 {cat_title} (continued)\n".strip()):
                    chunks.append(current_chunk)

                # Send all chunks for this category
                for chunk in chunks:
                    await update.message.reply_text(chunk, parse_mode='MarkdownV2')
            else:
                # Send the single message for this category
                await update.message.reply_text(full_cat_message, parse_mode='MarkdownV2')

    except Exception as error:
        # Catch potential errors from get_channels_by_category as well
        logging.error(f"Failed to list channels: {error}", exc_info=True)
        await update.message.reply_text(f"Failed to list channels: {str(error)}")

async def _handle_awaiting_regex(update: Update, context: CallbackContext):
    """Handles the logic when the bot is awaiting regex input."""
    msg = update.message
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
        return

    await update.message.chat.send_action("typing")

    try:
        # Fetch current feed data
        current_feed_data = miniflux_client.get_feed(feed_id)
        current_url = current_feed_data.get("feed_url", "")
        if not current_url:
            logging.error(f"Could not retrieve current URL for feed {feed_id} ({channel_name}) before updating regex.")
            await update.message.reply_text("Error: Could not retrieve current feed URL. Cannot update regex.")
            return

        logging.info(f"Current URL for {channel_name} (feed ID: {feed_id}): {current_url}")

        # Parse the current URL using the new function
        parsed_data = parse_feed_url(current_url)

        # Determine if removing or updating the regex
        remove_regex = new_regex_raw.lower() in ['-']
        regex_to_store = None if remove_regex or not new_regex_raw else new_regex_raw

        # Build the new URL using the constructor
        # We need the base URL part which might include the channel name in the path
        # Get base URL from parsed data
        base_url_for_build = parsed_data.get("base_url")
        if not base_url_for_build:
            logging.error(f"Could not extract base URL from {current_url}")
            await update.message.reply_text("Internal error: could not determine base URL.")
            return

        new_url = build_feed_url(
            base_url=base_url_for_build,
            channel_name=channel_name, # Pass channel name for context/verification if needed by build_feed_url
            flags=parsed_data.get("flags"), # Keep existing flags
            exclude_text=regex_to_store, # Set the new regex value (or None to remove)
            merge_seconds=parsed_data.get("merge_seconds") # Keep existing merge time
        )

        logging.info(f"Constructed new URL for {channel_name} (feed ID: {feed_id}): {new_url}")

        # Update the feed URL using the existing API function
        success, _updated_url_from_miniflux, error_message = update_feed_url_api(feed_id, new_url, miniflux_client)

        if success:
            if remove_regex or not regex_to_store:
                await update.message.reply_text(f"Regex filter removed for channel @{channel_name}.")
            else:
                await update.message.reply_text(f"Regex for channel @{channel_name} updated to: {regex_to_store}")

            # Show the flag keyboard again
            try:
                # Fetch flags from the *actually updated* URL returned by Miniflux
                # (or re-fetch feed if Miniflux doesn't return it reliably)
                feed_after_update = miniflux_client.get_feed(feed_id) # Re-fetch to be sure
                url_after_update = feed_after_update.get("feed_url", "")
                parsed_after_update = parse_feed_url(url_after_update)
                current_flags_after_update = parsed_after_update.get("flags") or []
                merge_seconds_after_update = parsed_after_update.get("merge_seconds") # Get merge time

                # Pass merge time to keyboard function
                keyboard = create_flag_keyboard(channel_name, current_flags_after_update, merge_seconds_after_update)
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(f"Updated options for @{channel_name}. Choose an action:", reply_markup=reply_markup)
                logging.info(f"Displayed updated options keyboard for {channel_name} after regex update.")

            except Exception as e_flags:
                logging.error(f"Failed to fetch flags/show keyboard after regex update for {channel_name}: {e_flags}")
        else:
            logging.error(f"Failed to update feed URL for {channel_name} (feed ID: {feed_id}) with new regex. Error: {error_message}. Attempted URL: {new_url}")
            await update.message.reply_text(f"Failed to update regex for channel @{channel_name}. Miniflux error: {error_message}")

    except Exception as e:
        logging.error(f"Error processing new regex for {channel_name}: {e}", exc_info=True)
        await update.message.reply_text(f"An unexpected error occurred while updating the regex: {str(e)}")

async def _handle_awaiting_merge_time(update: Update, context: CallbackContext):
    """Handles the logic when the bot is awaiting merge time input."""
    msg = update.message
    channel_name = context.user_data.get('editing_merge_time_for_channel')
    feed_id = context.user_data.get('editing_feed_id')
    new_merge_time_raw = msg.text.strip() if msg.text else ""

    # Clean up state
    if 'state' in context.user_data: del context.user_data['state']
    if 'editing_merge_time_for_channel' in context.user_data: del context.user_data['editing_merge_time_for_channel']
    if 'editing_feed_id' in context.user_data: del context.user_data['editing_feed_id']
    logging.info(f"Processing new merge time for channel {channel_name} (feed ID: {feed_id}). State cleared.")

    if not channel_name or not feed_id:
        logging.error("State 'awaiting_merge_time' was set, but channel_name or feed_id missing from context.")
        await update.message.reply_text("Error: Missing context for merge time update. Please try editing again.")
        return

    # Process merge time input
    new_merge_seconds_to_set = None
    try:
        if not new_merge_time_raw or int(new_merge_time_raw) == 0:
            new_merge_seconds_to_set = None # Treat empty or 0 as removal
            logging.info(f"Received input to remove merge time for {channel_name}.")
        else:
            new_merge_seconds_to_set = int(new_merge_time_raw)
            if new_merge_seconds_to_set < 0:
                await update.message.reply_text("Merge time must be a non-negative number (or 0 to disable). Please try again.")
                # Re-show keyboard logic... (consider putting this in a helper function)
                try:
                    feed_after_error = miniflux_client.get_feed(feed_id)
                    parsed_after_error = parse_feed_url(feed_after_error.get("feed_url", ""))
                    flags_after_error = parsed_after_error.get("flags") or []
                    merge_seconds_after_error = parsed_after_error.get("merge_seconds") # Get merge time
                    # Pass merge time to keyboard function
                    keyboard = create_flag_keyboard(channel_name, flags_after_error, merge_seconds_after_error)
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await update.message.reply_text(f"Options for @{channel_name}. Choose an action:", reply_markup=reply_markup)
                except Exception as e_flags_err:
                    logging.error(f"Failed to show keyboard after invalid merge time input for {channel_name}: {e_flags_err}")
                return # Stop processing this input
            else:
                logging.info(f"Received new merge time for {channel_name}: {new_merge_seconds_to_set} seconds.")
    except ValueError:
        await update.message.reply_text("Invalid input. Please send a number for merge time (seconds), or 0 to disable.")
        # Re-show keyboard logic...
        try:
            feed_after_error = miniflux_client.get_feed(feed_id)
            parsed_after_error = parse_feed_url(feed_after_error.get("feed_url", ""))
            flags_after_error = parsed_after_error.get("flags") or []
            merge_seconds_after_error = parsed_after_error.get("merge_seconds") # Get merge time
            # Pass merge time to keyboard function
            keyboard = create_flag_keyboard(channel_name, flags_after_error, merge_seconds_after_error)
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(f"Options for @{channel_name}. Choose an action:", reply_markup=reply_markup)
        except Exception as e_flags_err:
            logging.error(f"Failed to show keyboard after invalid merge time input for {channel_name}: {e_flags_err}")
        return # Stop processing this input

    await update.message.chat.send_action("typing")

    try:
        # Fetch current feed URL
        current_feed_data = miniflux_client.get_feed(feed_id)
        current_url = current_feed_data.get("feed_url", "")
        if not current_url:
            logging.error(f"Could not retrieve current URL for feed {feed_id} ({channel_name}) before updating merge time.")
            await update.message.reply_text("Error: Could not retrieve current feed URL. Cannot update merge time.")
            return

        logging.info(f"Current URL for {channel_name} (feed ID: {feed_id}): {current_url}")

        # Parse the current URL
        parsed_data = parse_feed_url(current_url)
        base_url_for_build = parsed_data.get("base_url")
        if not base_url_for_build:
            logging.error(f"Could not extract base URL from {current_url}")
            await update.message.reply_text("Internal error: could not determine base URL.")
            return

        # Build the new URL using the constructor
        new_url = build_feed_url(
            base_url=base_url_for_build,
            channel_name=channel_name,
            flags=parsed_data.get("flags"), # Keep existing flags
            exclude_text=parsed_data.get("exclude_text"), # Keep existing regex
            merge_seconds=new_merge_seconds_to_set # Set the new merge time (or None)
        )

        logging.info(f"Constructed new URL for {channel_name} (feed ID: {feed_id}): {new_url}")

        # Update the feed URL
        success, _updated_url_from_miniflux, error_message = update_feed_url_api(feed_id, new_url, miniflux_client)

        if success:
            if new_merge_seconds_to_set is None:
                await update.message.reply_text(f"Merge time filter removed for channel @{channel_name}.")
            else:
                await update.message.reply_text(f"Merge time for channel @{channel_name} updated to: {new_merge_seconds_to_set} seconds.")

            # Show the flag keyboard again
            try:
                feed_after_update = miniflux_client.get_feed(feed_id)
                url_after_update = feed_after_update.get("feed_url", "")
                parsed_after_update = parse_feed_url(url_after_update)
                current_flags_after_update = parsed_after_update.get("flags") or []
                merge_seconds_after_update = parsed_after_update.get("merge_seconds") # Get merge time

                # Pass merge time to keyboard function
                keyboard = create_flag_keyboard(channel_name, current_flags_after_update, merge_seconds_after_update)
                reply_markup = InlineKeyboardMarkup(keyboard)
                await update.message.reply_text(f"Updated options for @{channel_name}. Choose an action:", reply_markup=reply_markup)
                logging.info(f"Displayed updated options keyboard for {channel_name} after merge time update.")
            except Exception as e_flags:
                logging.error(f"Failed to fetch flags/show keyboard after merge time update for {channel_name}: {e_flags}")
        else:
            logging.error(f"Failed to update feed URL for {channel_name} (feed ID: {feed_id}) with new merge time. Error: {error_message}. Attempted URL: {new_url}")
            await update.message.reply_text(f"Failed to update merge time for channel @{channel_name}. Miniflux error: {error_message}")

    except Exception as e:
        logging.error(f"Error processing new merge time for {channel_name}: {e}", exc_info=True)
        await update.message.reply_text(f"An unexpected error occurred while updating the merge time: {str(e)}")

async def _parse_message_content(update: Update, context: CallbackContext):
    """Parses the message to identify channel links, forwards, RSS feeds, or HTML with RSS."""
    msg = update.message
    msg_dict = msg.to_dict()
    #logging.info(f"Message details:\n{json.dumps(msg_dict, indent=4)}") # Escaped newline

    channel_username = None
    channel_source_type = None # 'forward' or 'link' or 'link_or_username'
    direct_rss_url = None
    html_rss_links = None

    # 1. Check for forward
    forward_chat = msg_dict.get("forward_from_chat")
    if forward_chat:
        if forward_chat["type"] != "channel":
            logging.info(f"Forwarded message is from {forward_chat['type']}, not from channel")
            await update.message.reply_text("Please forward a message from a channel, not from other source.")
            return None, None, None, None # Indicate error or invalid input

        logging.info(f"Processing forwarded message from channel: {forward_chat.get('username') or forward_chat.get('id')}")
        # Use the imported function for channel acceptance check
        accept_no_username = should_accept_channels_without_username()
        if not forward_chat.get("username") and not accept_no_username:
            logging.error(f"Channel {forward_chat['title']} has no username and ACCEPT_CHANNELS_WITHOUT_USERNAME is false.")
            await update.message.reply_text("Error: channel must have a public username to subscribe. \nUse env ACCEPT_CHANNELS_WITHOUT_USERNAME=true to accept channels without username (needs support from RSS bridge).")
            return None, None, None, None # Indicate error

        channel_username = forward_chat.get("username") or str(forward_chat.get("id"))
        channel_source_type = 'forward'
        # If this is part of a media group from a forward, mark it as processed
        media_group_id = msg.media_group_id
        if media_group_id:
            context.user_data["processed_media_group_id"] = media_group_id
            logging.info(f"Processing first forwarded message from media group {media_group_id}")

    # 2. If not a forward, check for link or username in message text
    elif msg.text:
        text = msg.text.strip()

        # First, try parsing as a Telegram link or username
        parsed_channel = None
        if text.startswith('@'):
            # Handle direct username mention (e.g., @channelname)
            match_username = re.match(r"@([a-zA-Z0-9_]+)", text)
            if match_username:
                parsed_channel = match_username.group(1)
                logging.info(f"Processing direct username: {parsed_channel}")
        else:
            # Try parsing as a t.me link
            parsed_channel = parse_telegram_link(text)

        if parsed_channel:
            logging.info(f"Processing Telegram channel identified as: {parsed_channel}")
            channel_username = parsed_channel
            channel_source_type = 'link_or_username' # Changed source type for clarity
            # If this is part of a media group from a link, mark it as processed
            media_group_id = msg.media_group_id
            if media_group_id:
                context.user_data["processed_media_group_id"] = media_group_id
                logging.info(f"Processing first linked message from media group {media_group_id}")
        
        # If not a Telegram link, check if it's a direct RSS/HTML URL
        elif text.startswith('http://') or text.startswith('https://'):
            url = text
            logging.info(f"Checking if URL is a valid RSS feed or contains RSS links: {url}")
            await update.message.chat.send_action("typing")
            is_direct_rss, result = is_valid_rss_url(url)
            
            if is_direct_rss:
                direct_rss_url = result
                logging.info(f"URL is a direct RSS feed: {direct_rss_url}")
            elif isinstance(result, list) and result:
                html_rss_links = result
                logging.info(f"Found {len(html_rss_links)} RSS links in the webpage")
            # else: URL is neither direct RSS nor HTML with RSS links

    return channel_username, channel_source_type, direct_rss_url, html_rss_links

async def _handle_telegram_channel(update: Update, context: CallbackContext, channel_username: str, channel_source_type: str):
    """Handles logic for processing a detected Telegram channel."""
    context.user_data["channel_title"] = channel_username
    logging.info(f"Processing Telegram channel identified as: {channel_username} (Source: {channel_source_type})")
    await update.message.chat.send_action("typing")
    try:
        feeds = miniflux_client.get_feeds()
        target_feed = None
        feed_url_check = "" # Store URL if found
        for feed in feeds:
            feed_url_check = feed.get("feed_url", "")
            parsed_check = parse_feed_url(feed_url_check)
            existing_channel_name = parsed_check.get("channel_name")
            if existing_channel_name and channel_username.lower() == existing_channel_name.lower():
                target_feed = feed
                logging.info(f"Found existing feed for channel '{channel_username}': ID={feed.get('id')}, URL={feed_url_check}")
                break

        if target_feed:
            logging.info(f"Channel @{channel_username} is already in subscriptions (matched channel name)")
            feed_id = target_feed.get("id")
            current_merge_seconds = None # Default
            try:
                # Re-fetch feed for latest URL
                updated_target_feed = miniflux_client.get_feed(feed_id)
                feed_url_current = updated_target_feed.get("feed_url", "")
                # Use parser to get current flags and merge time
                parsed_current = parse_feed_url(feed_url_current)
                current_flags = parsed_current.get("flags") or []
                current_merge_seconds = parsed_current.get("merge_seconds") # Get merge time
                logging.info(f"Current flags for @{channel_username}: {current_flags}, merge_seconds: {current_merge_seconds}")
            except Exception as e:
                logging.error(f"Failed to fetch current feed details for feed {feed_id}: {e}")
                await update.message.reply_text("Error fetching current feed status. Proceeding without status.")
                current_flags = [] # Default to empty list on error
                current_merge_seconds = None # Reset on error

            # Pass merge time to keyboard function
            keyboard = create_flag_keyboard(channel_username, current_flags, current_merge_seconds)
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

    # --- Channel feed does not exist, proceed with category selection --- 
    try:
        # Use imported function
        categories = fetch_categories(miniflux_client)
    except Exception as error:
        logging.error(f"Failed to fetch categories: {error}")
        await update.message.reply_text("Failed to fetch categories from RSS reader.")
        return

    keyboard = []
    categories_dict = {}  # Store category information
    for category in categories:
        cat_title = category.get("title", "Unknown")
        cat_id = category.get("id")
        categories_dict[cat_id] = cat_title
        keyboard.append([InlineKeyboardButton(cat_title, callback_data=f"cat_{cat_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.user_data["categories"] = categories_dict
    await update.message.reply_text(
        f"Select category for channel @{channel_username}:", reply_markup=reply_markup
    )

async def _handle_direct_rss(update: Update, context: CallbackContext, direct_rss_url: str):
    """Handles logic for processing a direct RSS feed URL."""
    try:
        # Use imported function
        if check_feed_exists(miniflux_client, direct_rss_url):
            await update.message.reply_text(f"This RSS feed is already in your subscriptions.")
            return
    except Exception as error:
        logging.error(f"Failed to check if feed exists: {error}")
        await update.message.reply_text(f"Failed to check if feed exists: {str(error)}")
        return

    context.user_data["direct_rss_url"] = direct_rss_url
    try:
        # Use imported function
        categories = fetch_categories(miniflux_client)
    except Exception as error:
        logging.error(f"Failed to fetch categories: {error}")
        await update.message.reply_text("Failed to fetch categories from RSS reader.")
        return

    keyboard = []
    categories_dict = {}  # Store category information
    for category in categories:
        cat_title = category.get("title", "Unknown")
        cat_id = category.get("id")
        categories_dict[cat_id] = cat_title
        keyboard.append([InlineKeyboardButton(cat_title, callback_data=f"cat_{cat_id}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.user_data["categories"] = categories_dict
    await update.message.reply_text(
        f"URL is a valid RSS feed. Select category:", reply_markup=reply_markup
    )

async def _handle_html_rss_links(update: Update, context: CallbackContext, html_rss_links: list):
    """Handles logic when multiple RSS links are found on an HTML page."""
    keyboard = []
    for i, link in enumerate(html_rss_links):
        title = link.get('title', f"RSS Feed {i+1}")
        keyboard.append([InlineKeyboardButton(title, callback_data=f"rss_link_{i}")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    context.user_data["rss_links"] = html_rss_links
    await update.message.reply_text(
        f"Found multiple RSS feeds on the webpage. Select one to subscribe:",
        reply_markup=reply_markup
    )

async def _handle_unknown_message(update: Update, context: CallbackContext):
    """Handles messages that are not recognized as channels, RSS links, etc."""
    msg = update.message
    # Check if the URL check failed silently (URL looked like http but wasn't RSS/HTML)
    # This requires re-checking the message text as it wasn't passed down
    if msg and msg.text and (msg.text.startswith('http://') or msg.text.startswith('https://')):
        # We already know it wasn't handled as channel/direct/html from the main function logic
        await update.message.reply_text(
            "The URL does not appear to be a valid RSS feed and no RSS links were found on the webpage."
        )
    # Otherwise, show the default help message
    else:
        logging.info("Message is not a forward, channel link, RSS feed, or webpage with RSS links.")
        await update.message.reply_text("Please forward a message from any channel (public or private) or send a link to a message (e.g., https://t.me/channel_name/123 or https://t.me/-1002069358234/1951), or send a direct RSS feed URL.")

async def handle_message(update: Update, context: CallbackContext):
    """
    Handle incoming messages in private chat. Routes to specific handlers based on state or message content.
    Only processes messages from admin user.
    """
    msg = update.message
    if not msg:
        return

    user = msg.from_user
    if not user or not is_admin(user.username):
        logging.warning(f"Unauthorized access attempt via message from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return

    # --- State Handlers ---
    current_state = context.user_data.get('state')
    if current_state == 'awaiting_regex':
        await _handle_awaiting_regex(update, context)
        return
    elif current_state == 'awaiting_merge_time':
        await _handle_awaiting_merge_time(update, context)
        return

    # --- Media Group Handling ---
    # Check if this message is part of a media group we've already processed
    media_group_id = msg.media_group_id
    if media_group_id and context.user_data.get("processed_media_group_id") == media_group_id:
        logging.info(f"Skipping duplicate message from media group {media_group_id}")
        return

    # --- Content Parsing and Handling ---
    channel_username, channel_source_type, direct_rss_url, html_rss_links = await _parse_message_content(update, context)

    # Route to appropriate handler based on parsed content
    if channel_username:
        await _handle_telegram_channel(update, context, channel_username, channel_source_type)
    elif direct_rss_url:
        await _handle_direct_rss(update, context, direct_rss_url)
    elif html_rss_links:
        await _handle_html_rss_links(update, context, html_rss_links)
    else:
        # Handle cases where parsing returned nothing or indicated an error handled within _parse_message_content
        # Also handles messages that weren't forwards, links, or URLs.
        # We call _handle_unknown_message which includes logic for URLs that weren't valid RSS/HTML.
        await _handle_unknown_message(update, context)

async def button_callback(update: Update, context: CallbackContext):
    """
    Handle callback query when user selects a category or flag action.
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # Handle RSS link selection from webpage
    if data.startswith("rss_link_"):
        try:
            # Extract the index of selected RSS link
            link_index = int(data.split("_")[2])
            rss_links = context.user_data.get("rss_links", [])
            
            if not rss_links or link_index >= len(rss_links):
                await query.edit_message_text("Invalid RSS link selection or session expired.")
                return
            
            selected_link = rss_links[link_index]
            feed_url = selected_link.get("href")
            
            if not feed_url:
                await query.edit_message_text("Selected RSS link has no URL.")
                return
            
            # Check if feed already exists
            try:
                # Use imported function
                if check_feed_exists(miniflux_client, feed_url):
                    await query.edit_message_text(f"This RSS feed is already in your subscriptions.")
                    return
            except Exception as error:
                logging.error(f"Failed to check if feed exists: {error}")
                await query.edit_message_text(f"Failed to check if feed exists: {str(error)}")
                return
            
            # Store the selected RSS URL for category selection
            context.user_data["direct_rss_url"] = feed_url
            # Clear the RSS links list as we've made a selection
            if "rss_links" in context.user_data:
                del context.user_data["rss_links"]
            
            # Fetch categories for the selected RSS feed
            try:
                # Use imported function
                categories = fetch_categories(miniflux_client)
            except Exception as error:
                logging.error(f"Failed to fetch categories: {error}")
                await query.edit_message_text("Failed to fetch categories from RSS reader.")
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
            
            await query.edit_message_text(
                f"Selected RSS feed: {selected_link.get('title', 'RSS Feed')}\nChoose a category:",
                reply_markup=reply_markup
            )
            return
            
        except Exception as e:
            logging.error(f"Error processing RSS link selection: {e}", exc_info=True)
            await query.edit_message_text(f"Error processing RSS link selection: {str(e)}")
            return
    
    elif data.startswith("cat_"):
        cat_id_str = data.split("_", 1)[1]
        try:
            cat_id = int(cat_id_str)
        except ValueError:
            await query.edit_message_text("Invalid category ID.")
            return

        # Check if handling direct RSS URL
        direct_rss_url = context.user_data.get("direct_rss_url")
        
        if direct_rss_url:
            feed_url = direct_rss_url
            # Clear the stored URL after using it
            del context.user_data["direct_rss_url"]
            
            await query.message.chat.send_action("typing")
            try:
                logging.info(f"Subscribing to direct RSS feed '{feed_url}' in category {cat_id}")
                await miniflux_client.create_feed(feed_url, category_id=cat_id)
                category_title = context.user_data.get("categories", {}).get(cat_id, "Unknown")
                url_instance = MINIFLUX_BASE_URL.rstrip('/').replace('http://', '').replace('https://', '')
                await query.edit_message_text(
                    f"Direct RSS feed {feed_url} has been subscribed on {url_instance} instance, category '{category_title.strip()}'"
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
            return
        
        # Regular channel subscription logic
        channel_title = context.user_data.get("channel_title")
        if not channel_title:
            await query.edit_message_text("Channel information is missing.")
            return

        # --- Construct the feed URL for Telegram channels --- 
        # Assume RSS_BRIDGE_URL is a template like ".../rss/{channel}/token"
        if "{channel}" not in RSS_BRIDGE_URL:
            logging.error(f"RSS_BRIDGE_URL does not contain '{{channel}}' placeholder. URL: {RSS_BRIDGE_URL}")
            await query.edit_message_text("Configuration error: RSS_BRIDGE_URL is not a valid template.")
            return
        
        # Replace the placeholder with the actual channel title/ID
        # No need for extra quoting here if RSS bridge expects the raw name in the path
        feed_url = RSS_BRIDGE_URL.replace("{channel}", channel_title)

        # Clear channel title from context *after* constructing URL
        if "channel_title" in context.user_data:
            del context.user_data["channel_title"]

        await query.message.chat.send_action("typing")
        try:
            logging.info(f"Subscribing to feed '{feed_url}' in category {cat_id}")
            await miniflux_client.create_feed(feed_url, category_id=cat_id)
            category_title = context.user_data.get("categories", {}).get(cat_id, "Unknown")
            url_instance = MINIFLUX_BASE_URL.rstrip('/').replace('http://', '').replace('https://', '')
            await query.edit_message_text(
                f"Channel @{channel_title} has been subscribed on {url_instance} instance, added to category '{category_title.strip()}'"
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

        # Call the shared function for adding flags (which now uses url_constructor)
        # It now returns merge time as well
        success, message, updated_flags, updated_merge_seconds = await add_flag_to_channel(channel_name, flag_name)

        # Create keyboard with updated flag statuses and merge time
        keyboard = create_flag_keyboard(channel_name, updated_flags, updated_merge_seconds)
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"{message}\n\nChoose an action:",
            reply_markup=reply_markup
        )
        # No need for the 'else' block here anymore, add_flag_to_channel returns current flags on failure too

    elif data.startswith("remove_flag|"):
        # Handle remove flag button
        parts = data.split("|")
        if len(parts) < 3:
            await query.edit_message_text("Invalid flag data.")
            return

        channel_name = parts[1]
        flag_name = parts[2]

        # Call the shared function for removing flags (which now uses url_constructor)
        # It now returns merge time as well
        success, message, updated_flags, updated_merge_seconds = await remove_flag_from_channel(channel_name, flag_name)

        # Create keyboard with updated flag statuses and merge time
        keyboard = create_flag_keyboard(channel_name, updated_flags, updated_merge_seconds)
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"{message}\n\nChoose an action:",
            reply_markup=reply_markup
        )
        # No need for the 'else' block here anymore, remove_flag_from_channel returns current flags on failure too

    elif data.startswith("delete|"):
        # Handle delete channel button
        channel_name = data.split("|", 1)[1]
        
        await query.message.chat.send_action("typing")
        try:
            # Get all feeds
            feeds = miniflux_client.get_feeds()
            target_feed_id = None # Store ID directly

            # Find the feed for the specified channel
            for feed in feeds:
                feed_url = feed.get("feed_url", "")
                # Use parser to reliably get channel name
                parsed_data = parse_feed_url(feed_url)
                channel = parsed_data.get("channel_name")

                if channel and channel.lower() == channel_name.lower():
                    target_feed_id = feed.get("id")
                    break

            if not target_feed_id:
                await query.edit_message_text(f"Channel @{channel_name} not found in subscriptions.")
                return

            # Delete the feed using the found ID
            miniflux_client.delete_feed(target_feed_id)

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
            feed_id = None
            feed_url = ""

            for feed in feeds:
                feed_url_check = feed.get("feed_url", "")
                # Use parser
                parsed_check = parse_feed_url(feed_url_check)
                channel = parsed_check.get("channel_name")
                if channel and channel.lower() == channel_name.lower():
                    target_feed = feed
                    feed_id = feed.get("id")
                    # Fetch the most up-to-date feed data to get the current URL accurately
                    if not feed_id:
                        logging.warning(f"Feed ID not found for channel {channel_name} during regex edit prep.")
                        continue
                    try:
                        # Get URL from the initially found feed or re-fetch
                        feed_url = feed_url_check # Use the URL we already have
                        logging.info(f"Found current feed URL for {channel_name} (ID: {feed_id}): {feed_url}")
                        # Optional: re-fetch if staleness is a concern
                        # updated_target_feed = miniflux_client.get_feed(feed_id)
                        # feed_url = updated_target_feed.get("feed_url", "")
                    except Exception as fetch_error:
                        # This block might only be needed if re-fetching above
                        logging.error(f"Failed to fetch feed details for {feed_id} ({channel_name}) during regex edit prep: {fetch_error}")
                        await query.edit_message_text(f"Error fetching current feed details for @{channel_name}.")
                        return
                    break # Found feed, exit loop

            if not target_feed or not feed_id:
                logging.warning(f"Target feed or feed_id not found for {channel_name} after searching feeds.")
                await query.edit_message_text(f"Channel @{channel_name} not found in subscriptions or feed ID missing.")
                return

            # Extract current regex using the parser
            parsed_data = parse_feed_url(feed_url)
            current_regex = parsed_data.get("exclude_text") or "" # Default to empty string if None

            if current_regex:
                logging.info(f"Found current regex for {channel_name}: '{current_regex}'")
            else:
                logging.info(f"No current exclude_text regex found for {channel_name}")

            # ... (Store state and prompt user - logic remains the same) ...
            context.user_data['state'] = 'awaiting_regex'
            context.user_data['editing_regex_for_channel'] = channel_name
            context.user_data['editing_feed_id'] = feed_id
            logging.info(f"Set state to 'awaiting_regex' for channel {channel_name} (feed ID: {feed_id})")

            # Use the extracted current_regex
            if current_regex:
                # Corrected newline characters
                prompt_message = (
                    f"Current regex for @{channel_name} is:\n{current_regex}\n\n"
                    "Please send the new regex. Send '-' to remove the regex filter.\n"
                    "Example: реклама|спам|сбор|подписка"
                )
            else:
                # Corrected newline characters
                prompt_message = (
                    f"No current regex set for @{channel_name}.\n"
                    "Please send the new regex. Send '-' to remove the regex filter. \n"
                    "Example: реклама|спам|сбор|подписка"
                )

            # Removed parse_mode argument
            await query.edit_message_text(prompt_message)


        except Exception as e:
            # ... (Error handling and state cleanup remains the same) ...
            logging.error(f"Failed during edit_regex preparation for {channel_name}: {e}", exc_info=True)
            if 'state' in context.user_data: del context.user_data['state']
            if 'editing_regex_for_channel' in context.user_data: del context.user_data['editing_regex_for_channel']
            if 'editing_feed_id' in context.user_data: del context.user_data['editing_feed_id']
            error_msg = str(e)
            await query.edit_message_text(f"Failed to start regex edit: {error_msg}")


    elif data.startswith("edit_merge_time|"):
        # Handle edit merge time button
        channel_name = data.split("|", 1)[1]

        await query.message.chat.send_action("typing")
        try:
            # Find the feed for the channel
            feeds = miniflux_client.get_feeds()
            target_feed = None
            feed_id = None
            feed_url = ""

            for feed in feeds:
                feed_url_check = feed.get("feed_url", "")
                # Use parser
                parsed_check = parse_feed_url(feed_url_check)
                channel = parsed_check.get("channel_name")
                if channel and channel.lower() == channel_name.lower():
                    target_feed = feed
                    feed_id = feed.get("id")
                    if not feed_id:
                        logging.warning(f"Feed ID not found for channel {channel_name} during merge time edit prep.")
                        continue
                    try:
                        feed_url = feed_url_check # Use URL from initial find
                        logging.info(f"Found current feed URL for {channel_name} (ID: {feed_id}): {feed_url}")
                        # Optional re-fetch:
                        # updated_target_feed = miniflux_client.get_feed(feed_id)
                        # feed_url = updated_target_feed.get("feed_url", "")
                    except Exception as fetch_error:
                        # Only needed if re-fetching
                        logging.error(f"Failed to fetch feed details for {feed_id} ({channel_name}) during merge time edit prep: {fetch_error}")
                        await query.edit_message_text(f"Error fetching current feed details for @{channel_name}.")
                        return
                    break # Found feed, exit loop

            if not target_feed or not feed_id:
                # ... (error handling remains the same) ...
                logging.warning(f"Target feed or feed_id not found for {channel_name} after searching feeds.")
                await query.edit_message_text(f"Channel @{channel_name} not found in subscriptions or feed ID missing.")
                return

            # Extract current merge_seconds using the parser
            parsed_data = parse_feed_url(feed_url)
            current_merge_seconds = parsed_data.get("merge_seconds") # Returns None if not found or invalid

            if current_merge_seconds is not None:
                logging.info(f"Found current merge_seconds for {channel_name}: {current_merge_seconds}")
            else:
                logging.info(f"No current merge_seconds found for {channel_name}")

            # ... (Store state and prompt user - logic remains the same) ...
            context.user_data['state'] = 'awaiting_merge_time'
            context.user_data['editing_merge_time_for_channel'] = channel_name
            context.user_data['editing_feed_id'] = feed_id
            logging.info(f"Set state to 'awaiting_merge_time' for channel {channel_name} (feed ID: {feed_id})")

            # Corrected newline characters
            prompt_message = f"Editing merge time for @{channel_name}.\n"
            if current_merge_seconds is not None:
                prompt_message += f"Current merge time: {current_merge_seconds} seconds.\n\n"
            else:
                prompt_message += "Merge time is not currently set.\n\n"
            prompt_message += "Please send the new merge time in seconds (e.g., 300). Send 0 or empty message to disable merging (remove the parameter)."

            # Removed parse_mode argument
            await query.edit_message_text(prompt_message)

        except Exception as e:
            # ... (Error handling and state cleanup remains the same) ...
            logging.error(f"Failed during edit_merge_time preparation for {channel_name}: {e}", exc_info=True)
            if 'state' in context.user_data: del context.user_data['state']
            if 'editing_merge_time_for_channel' in context.user_data: del context.user_data['editing_merge_time_for_channel']
            if 'editing_feed_id' in context.user_data: del context.user_data['editing_feed_id']
            error_msg = str(e)
            await query.edit_message_text(f"Failed to start merge time edit: {error_msg}")


async def add_flag_to_channel(channel_name, flag_to_add):
    """
    Add a flag to a channel subscription using url_constructor.
    
    Args:
        channel_name: Channel username or ID
        flag_to_add: Flag to add
        
    Returns:
        tuple: (success, message, updated_flags list, updated_merge_seconds int | None)
    """
    current_flags_on_error = []
    current_merge_seconds_on_error = None
    try:
        # Find the feed
        feeds = miniflux_client.get_feeds()
        target_feed = None
        feed_id = None
        current_url = ""

        for feed in feeds:
            feed_url_check = feed.get("feed_url", "")
            parsed_check = parse_feed_url(feed_url_check)
            channel = parsed_check.get("channel_name")

            if channel and channel.lower() == channel_name.lower():
                target_feed = feed # Keep feed data if needed, e.g., title
                feed_id = feed.get("id")
                current_url = feed_url_check # Store the found URL
                # Store potentially needed values for error return
                current_flags_on_error = parsed_check.get("flags") or []
                current_merge_seconds_on_error = parsed_check.get("merge_seconds")
                break
        
        if not target_feed or not feed_id:
            return False, f"Channel @{channel_name} not found in subscriptions.", [], None
        
        # Optionally re-fetch feed for the absolute latest URL, though often unnecessary
        # try:
        #     updated_target_feed = miniflux_client.get_feed(feed_id)
        #     current_url = updated_target_feed.get("feed_url", "")
        # except Exception as fetch_err:
        #      logging.warning(f"Could not re-fetch feed {feed_id} before adding flag, proceeding with stored URL. Error: {fetch_err}")
        #      # Proceed with current_url found during the loop if fetch fails

        if not current_url: # Should not happen if target_feed was found, but safety check
            return False, f"Could not determine current URL for @{channel_name}.", current_flags_on_error, current_merge_seconds_on_error

        # Parse the current URL
        parsed_data = parse_feed_url(current_url)
        current_flags = parsed_data.get("flags") or []
        current_merge_seconds = parsed_data.get("merge_seconds") # Get current merge time for error case
        base_url_for_build = parsed_data.get("base_url")
        
        if not base_url_for_build:
            logging.error(f"Could not extract base URL from {current_url} for @{channel_name}")
            return False, "Internal error: could not determine base URL.", current_flags, current_merge_seconds

        # Check if flag already exists
        if flag_to_add in current_flags:
            return False, f"Flag '{flag_to_add}' is already set for channel @{channel_name}.", current_flags, current_merge_seconds
        
        # Add new flag
        new_flags = current_flags + [flag_to_add]
        
        # Build new URL
        new_url = build_feed_url(
            base_url=base_url_for_build,
            channel_name=channel_name,
            flags=new_flags,
            exclude_text=parsed_data.get("exclude_text"),
            merge_seconds=parsed_data.get("merge_seconds") # Keep existing merge time
        )
        
        logging.info(f"Original feed URL: {current_url}")
        logging.info(f"Attempting to update URL to: {new_url}")

        # Update feed URL via API
        success, _updated_url_from_miniflux, error_message = update_feed_url_api(feed_id, new_url, miniflux_client)
        
        if not success:
            # Use new_url in the message as that's what we tried to set
            return False, f"Failed to update feed URL via Miniflux API for @{channel_name}. Error: {error_message}. Target URL was: {new_url}", current_flags, current_merge_seconds
        
        # Parse the *actual* updated URL returned by miniflux or re-fetch
        # Re-fetching is safer to guarantee we have the canonical state
        final_flags = new_flags # Default in case re-fetch fails
        final_merge_seconds = parsed_data.get("merge_seconds") # Default in case re-fetch fails
        try:
            final_feed_data = miniflux_client.get_feed(feed_id)
            final_url = final_feed_data.get("feed_url", "")
            final_parsed_data = parse_feed_url(final_url)
            final_flags = final_parsed_data.get("flags") or []
            final_merge_seconds = final_parsed_data.get("merge_seconds") # Get final merge time
            logging.info(f"URL after update confirmation for @{channel_name}: {final_url}")
        except Exception as e_final:
            logging.error(f"Failed to fetch final feed state for @{channel_name} after adding flag: {e_final}. Using flags/merge_time from successful update attempt.")
            # Fallback: Assume the new_flags we intended are now set, keep existing merge time


        flags_display = " ".join(final_flags) if final_flags else "none"
        return True, f"Added flag '{flag_to_add}' to channel @{channel_name}.\nCurrent flags: {flags_display}", final_flags, final_merge_seconds
        
    except Exception as e:
        logging.error(f"Failed during add_flag_to_channel for {channel_name}: {e}", exc_info=True)
        # Attempt to return current flags/merge_time if possible, otherwise defaults
        # The values were captured at the start of the try block or during loop
        return False, f"Failed to add flag: {str(e)}", current_flags_on_error, current_merge_seconds_on_error


async def remove_flag_from_channel(channel_name, flag_to_remove):
    """
    Remove a flag from a channel subscription using url_constructor.
    
    Args:
        channel_name: Channel username or ID
        flag_to_remove: Flag to remove
        
    Returns:
        tuple: (success, message, updated_flags list, updated_merge_seconds int | None)
    """
    current_flags_on_error = []
    current_merge_seconds_on_error = None
    try:
        # Find the feed
        feeds = miniflux_client.get_feeds()
        target_feed = None
        feed_id = None
        current_url = ""

        for feed in feeds:
            feed_url_check = feed.get("feed_url", "")
            parsed_check = parse_feed_url(feed_url_check)
            channel = parsed_check.get("channel_name")

            if channel and channel.lower() == channel_name.lower():
                target_feed = feed
                feed_id = feed.get("id")
                current_url = feed_url_check
                # Store potentially needed values for error return
                current_flags_on_error = parsed_check.get("flags") or []
                current_merge_seconds_on_error = parsed_check.get("merge_seconds")
                break
        
        if not target_feed or not feed_id:
            return False, f"Channel @{channel_name} not found in subscriptions.", [], None

        # Optional: Re-fetch for latest URL
        # ... (similar logic as in add_flag_to_channel if needed) ...
        if not current_url: return False, f"Could not determine current URL for @{channel_name}.", current_flags_on_error, current_merge_seconds_on_error

        # Parse the current URL
        parsed_data = parse_feed_url(current_url)
        current_flags = parsed_data.get("flags") or []
        current_merge_seconds = parsed_data.get("merge_seconds") # Get current merge time
        base_url_for_build = parsed_data.get("base_url")

        if not base_url_for_build:
            logging.error(f"Could not extract base URL from {current_url} for @{channel_name}")
            return False, "Internal error: could not determine base URL.", current_flags, current_merge_seconds

        # Check if flag exists
        if flag_to_remove not in current_flags:
            return False, f"Flag '{flag_to_remove}' is not set for channel @{channel_name}.", current_flags, current_merge_seconds
        
        # Remove flag
        new_flags = [flag for flag in current_flags if flag != flag_to_remove]
        # If new_flags is empty, build_feed_url should handle omitting the parameter
        
        # Build new URL
        new_url = build_feed_url(
            base_url=base_url_for_build,
            channel_name=channel_name,
            flags=new_flags if new_flags else None, # Pass None if empty
            exclude_text=parsed_data.get("exclude_text"),
            merge_seconds=parsed_data.get("merge_seconds") # Keep existing merge time
        )
        
        logging.info(f"Original feed URL: {current_url}")
        logging.info(f"Attempting to update URL to: {new_url}")

        # Update feed URL via API
        success, _updated_url_from_miniflux, error_message = update_feed_url_api(feed_id, new_url, miniflux_client)
        
        if not success:
            return False, f"Failed to update feed URL via Miniflux API for @{channel_name}. Error: {error_message}. Target URL was: {new_url}", current_flags, current_merge_seconds
        
        # Parse the *actual* updated URL or re-fetch
        final_flags = new_flags # Default
        final_merge_seconds = parsed_data.get("merge_seconds") # Default
        try:
            final_feed_data = miniflux_client.get_feed(feed_id)
            final_url = final_feed_data.get("feed_url", "")
            final_parsed_data = parse_feed_url(final_url)
            final_flags = final_parsed_data.get("flags") or []
            final_merge_seconds = final_parsed_data.get("merge_seconds") # Get final merge time
            logging.info(f"URL after update confirmation for @{channel_name}: {final_url}")
        except Exception as e_final:
            logging.error(f"Failed to fetch final feed state for @{channel_name} after removing flag: {e_final}. Using flags/merge_time from successful update attempt.")
            # Fallback: Assume the new_flags we intended are now set, keep existing merge time

        flags_display = " ".join(final_flags) if final_flags else "none"
        return True, f"Removed flag '{flag_to_remove}' from channel @{channel_name}.\nCurrent flags: {flags_display}", final_flags, final_merge_seconds
        
    except Exception as e:
        logging.error(f"Failed during remove_flag_from_channel for {channel_name}: {e}", exc_info=True)
        # Attempt to return current flags/merge_time if possible
        return False, f"Failed to remove flag: {str(e)}", current_flags_on_error, current_merge_seconds_on_error

def create_flag_keyboard(channel_username, current_flags, current_merge_seconds=None):
    """
    Create keyboard with flag options, showing current status (✅/❌),
    edit regex, edit merge time (with current value), and delete buttons.

    Args:
        channel_username: Channel username or ID
        current_flags: List of currently set flags (should be [] if None)
        current_merge_seconds: The current merge time in seconds (or None)

    Returns:
        list: Keyboard buttons
    """
    # Ensure current_flags is a list
    current_flags = current_flags or []

    all_flags = [
        "fwd", "video", "stream", "donat", "clown", "poo",
        "advert", "link", "mention", "hid_channel", "foreign_channel"
    ]
    keyboard = []
    row = []

    for i, flag in enumerate(all_flags):
        if flag in current_flags:
            button_text = f"❌ Remove \"{flag}\""
            callback_action = f"remove_flag|{channel_username}|{flag}"
        else:
            button_text = f"✅ Add \"{flag}\""
            callback_action = f"add_flag|{channel_username}|{flag}"

        row.append(InlineKeyboardButton(button_text, callback_data=callback_action))

        if len(row) == 2 or i == len(all_flags) - 1:
            keyboard.append(row)
            row = []

    keyboard.append([InlineKeyboardButton("Edit Regex", callback_data=f"edit_regex|{channel_username}")])
    
    # Add merge time button with current value if available
    merge_time_text = "Edit Merge Time"
    if current_merge_seconds is not None:
        merge_time_text += f" ({current_merge_seconds}s)"
    keyboard.append([InlineKeyboardButton(merge_time_text, callback_data=f"edit_merge_time|{channel_username}")])
    
    keyboard.append([InlineKeyboardButton("Delete channel", callback_data=f"delete|{channel_username}")])

    return keyboard

# Restore add_flag command handler (uses the refactored add_flag_to_channel)
async def add_flag(update: Update, context: CallbackContext):
    """
    Handle the /add_flag command.
    Adds a new flag to an existing channel subscription.
    Format: /add_flag channel_name flag
    """
    user = update.message.from_user
    # Use the imported function for admin check
    if not user or not is_admin(user.username):
        logging.warning(f"Unauthorized access attempt for /add_flag from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return
    
    # Check if command has correct arguments
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /add_flag channel flag")
        return
    
    channel_name = context.args[0].lstrip('@')
    flag_to_add = context.args[1].strip()
    
    await update.message.chat.send_action("typing")
    
    # Call the shared function for adding flags
    # Note: add_flag_to_channel is already defined earlier in the file
    _success, message, _ = await add_flag_to_channel(channel_name, flag_to_add)
    await update.message.reply_text(message)

# Restore remove_flag command handler (uses the refactored remove_flag_from_channel)
async def remove_flag(update: Update, context: CallbackContext):
    """
    Handle the /remove_flag command.
    Removes a flag from an existing channel subscription.
    Format: /remove_flag channel flag
    """
    user = update.message.from_user
    # Use the imported function for admin check
    if not user or not is_admin(user.username):
        logging.warning(f"Unauthorized access attempt for /remove_flag from user: {user.username if user else 'Unknown'}")
        await update.message.reply_text("Access denied. Only admin can use this bot.")
        return
    
    # Check if command has correct arguments
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("Usage: /remove_flag channel flag")
        return
    
    channel_name = context.args[0].lstrip('@')
    flag_to_remove = context.args[1].strip()
    
    await update.message.chat.send_action("typing")
    
    # Call the shared function for removing flags
    # Note: remove_flag_from_channel is already defined earlier in the file
    _success, message, _ = await remove_flag_from_channel(channel_name, flag_to_remove)
    await update.message.reply_text(message)

def main():
    """
    Initialize the Telegram bot and register handlers.
    """
    # Check if config loading/client initialization failed - moved from module level
    if miniflux_client is None or TELEGRAM_TOKEN is None:
        logging.critical("Initialization failed (check config.py logs). Exiting.")
        sys.exit(1) # Exit if essential components failed
    
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
            # Remove print statement
            # Restore the set_my_commands call
            await application.bot.set_my_commands(commands)
            logging.info("Bot commands have been set up successfully") # Restore original log
            # Remove print statement
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

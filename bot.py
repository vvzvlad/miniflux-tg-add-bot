import logging
import json
import urllib.parse
import sys # Import sys for exit
import re
import asyncio

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
        channels_by_category = get_channels_by_category(miniflux_client, RSS_BRIDGE_URL)

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
        success, _updated_url_from_miniflux, error_message = await update_feed_url_api(feed_id, new_url, miniflux_client)

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
        success, _updated_url_from_miniflux, error_message = await update_feed_url_api(feed_id, new_url, miniflux_client)

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

            # FIX: Store feed_id in user_data for flag operations
            context.user_data[f'feed_id_for_{channel_username}'] = feed_id
            logging.debug(f"Stored feed_id {feed_id} in context for channel {channel_username}")

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
    try:
        channel_username, channel_source_type, direct_rss_url, html_rss_links = await _parse_message_content(update, context)

        # Route to appropriate handler based on parsed content
        if channel_username:
            try:
                await _handle_telegram_channel(update, context, channel_username, channel_source_type)
            except Exception as e:
                logging.error(f"Error processing telegram channel {channel_username}: {e}", exc_info=True)
                # Check if it's a TelegramError related to rate limits
                if hasattr(e, "__class__") and e.__class__.__name__ == "TelegramError" and "rate limit" in str(e).lower():
                    await update.message.reply_text(f"Telegram API rate limit exceeded. Please try again later.")
                else:
                    await update.message.reply_text(f"Error processing telegram channel @{channel_username}: {str(e)}")
        elif direct_rss_url:
            try:
                await _handle_direct_rss(update, context, direct_rss_url)
            except Exception as e:
                logging.error(f"Error processing RSS feed {direct_rss_url}: {e}", exc_info=True)
                await update.message.reply_text(f"Error processing RSS feed: {str(e)}")
        elif html_rss_links:
            try:
                await _handle_html_rss_links(update, context, html_rss_links)
            except Exception as e:
                logging.error(f"Error processing website with RSS links: {e}", exc_info=True)
                await update.message.reply_text(f"Error processing website with RSS links: {str(e)}")
        else:
            # Handle cases where parsing returned nothing or indicated an error handled within _parse_message_content
            # Also handles messages that weren't forwards, links, or URLs.
            # We call _handle_unknown_message which includes logic for URLs that weren't valid RSS/HTML.
            await _handle_unknown_message(update, context)
    except Exception as e:
        logging.error(f"Error parsing message content: {e}", exc_info=True)
        # Provide user-friendly error message
        await update.message.reply_text(f"Error processing your message: {str(e)}")

async def _handle_flag_toggle(query, context: CallbackContext, action: str, flag: str, channel_name: str):
    """Handles the logic for adding or removing a flag based on button press."""
    feed_id = context.user_data.get(f'feed_id_for_{channel_name}')
    if not feed_id:
        logging.error(f"Missing feed_id in context for flag operation on channel {channel_name}")
        await query.edit_message_text("Error: Session data lost. Please try selecting the channel again.")
        return

    logging.info(f"Processing flag toggle: Action='{action}', Flag='{flag}', Channel='{channel_name}', FeedID={feed_id}")
    current_flags_on_error = []
    current_merge_seconds_on_error = None

    try:
        # Fetch current feed data to get the URL
        current_feed_data = miniflux_client.get_feed(feed_id=feed_id)
        current_url = current_feed_data.get("feed_url", "")
        if not current_url:
            logging.error(f"Could not retrieve current URL for feed {feed_id} ({channel_name}). Cannot toggle flag.")
            await query.edit_message_text(f"Error: Could not get current feed details for @{channel_name}.")
            return

        # Parse the current URL
        parsed_data = parse_feed_url(current_url)
        current_flags = parsed_data.get("flags") or []
        current_merge_seconds = parsed_data.get("merge_seconds") # Store for keyboard regen
        current_flags_on_error = current_flags[:] # Store for error return
        current_merge_seconds_on_error = current_merge_seconds # Store for error return
        base_url_for_build = parsed_data.get("base_url")

        if not base_url_for_build:
            logging.error(f"Could not extract base URL from {current_url} for @{channel_name}")
            await query.edit_message_text("Internal error: could not determine base URL.")
            return

        # Calculate new flags
        new_flags = current_flags[:]
        success_message_part = ""
        if action == "add":
            if flag in new_flags:
                await query.edit_message_text(f"Flag '{flag}' is already set for channel @{channel_name}.")
                # Re-show keyboard with current state
                keyboard = create_flag_keyboard(channel_name, current_flags, current_merge_seconds)
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(f"Flag '{flag}' is already set for channel @{channel_name}. Choose an action:", reply_markup=reply_markup)
                return
            new_flags.append(flag)
            success_message_part = f"Flag {flag} added"
        elif action == "remove":
            if flag not in new_flags:
                await query.edit_message_text(f"Flag '{flag}' is not set for channel @{channel_name}.")
                # Re-show keyboard with current state
                keyboard = create_flag_keyboard(channel_name, current_flags, current_merge_seconds)
                reply_markup = InlineKeyboardMarkup(keyboard)
                await query.edit_message_text(f"Flag '{flag}' is not set for channel @{channel_name}. Choose an action:", reply_markup=reply_markup)
                return
            new_flags = [f for f in new_flags if f != flag]
            success_message_part = f"Flag {flag} removed"
        else:
            logging.error(f"Unknown flag action '{action}' requested.")
            await query.edit_message_text("Internal error: Unknown flag action.")
            return

        # Build new URL
        new_url = build_feed_url(
            base_url=base_url_for_build,
            channel_name=channel_name,
            flags=new_flags if new_flags else None,
            exclude_text=parsed_data.get("exclude_text"),
            merge_seconds=parsed_data.get("merge_seconds")
        )

        logging.info(f"Attempting flag update. Old flags: {current_flags}, New flags: {new_flags}. Target URL: {new_url}")

        # Update feed URL via API
        success, updated_url_from_api, error_message = await update_feed_url_api(feed_id, new_url, miniflux_client)

        if not success:
            # Восстанавливаем отправку клавиатуры при ошибке
            await query.edit_message_text(f"Failed to update feed URL via Miniflux API for @{channel_name}. Error: {error_message}")
            keyboard = create_flag_keyboard(channel_name, current_flags_on_error, current_merge_seconds_on_error)
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(f"Failed to update flags for @{channel_name}. Error: {error_message}. Choose an action:", reply_markup=reply_markup)
            return

        # Success
        final_flags = new_flags
        final_merge_seconds = parsed_data.get("merge_seconds")
        flags_display = " ".join(final_flags) if final_flags else "none"
        message = f"{success_message_part} for channel @{channel_name}.\nCurrent flags: {flags_display}"

        # Восстанавливаем отправку клавиатуры при успехе
        keyboard = create_flag_keyboard(channel_name, final_flags, final_merge_seconds)
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"{message}\n\nChoose an action:",
            reply_markup=reply_markup
        )

    except Exception as e:
        logging.error(f"Failed during _handle_flag_toggle for {channel_name}: {e}", exc_info=True)
        # Восстанавливаем отправку клавиатуры при общем исключении
        keyboard = create_flag_keyboard(channel_name, current_flags_on_error, current_merge_seconds_on_error)
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(f"Failed to process flag action: {str(e)}. Choose an action:", reply_markup=reply_markup)

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
                await asyncio.to_thread(miniflux_client.create_feed, feed_url, category_id=cat_id)
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
            await asyncio.to_thread(miniflux_client.create_feed, feed_url, category_id=cat_id)
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

    # Refactored flag handling - FIX: Adjust parsing logic
    elif data.startswith("add_flag|") or data.startswith("remove_flag|"):
        try:
            # FIX: Use '|' as separator and parse action, channel_name, flag
            action_part, channel_name, flag = data.split("|", 2) 
            # Extract action ('add' or 'remove') from action_part
            action = action_part.split("_")[0] # 'add_flag' -> 'add', 'remove_flag' -> 'remove'
            
            # Pass control to the dedicated handler
            await _handle_flag_toggle(query, context, action, flag, channel_name)
        except ValueError as e:
            logging.error(f"Could not parse flag callback data: {data}. Error: {e}")
            await query.edit_message_text("Invalid callback data format for flag action.")
        except Exception as e:
            logging.error(f"Unexpected error processing flag callback '{data}': {e}", exc_info=True)
            await query.edit_message_text("An unexpected error occurred processing the flag action.")

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
            await miniflux_client.delete_feed(target_feed_id)

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

    else:
        logging.warning(f"Received unknown callback query data: {data}")
        await query.edit_message_text("Unknown action.")

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
                # application.add_handler(CommandHandler("add_flag", add_flag)) # Comment out
                # application.add_handler(CommandHandler("remove_flag", remove_flag)) # Comment out
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
    # application.add_handler(CommandHandler("add_flag", add_flag)) # Comment out
    # application.add_handler(CommandHandler("remove_flag", remove_flag)) # Comment out
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()

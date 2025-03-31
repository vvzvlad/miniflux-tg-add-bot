import logging
import os
import json
import urllib.parse
import miniflux
import requests
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
                # Extract flags and excluded words from URL
                flags = []
                excluded_words = []
                
                if "exclude_flags=" in feed_url:
                    flags_part = feed_url.split("exclude_flags=")[1].split("&")[0]
                    flags = flags_part.split(",")
                
                if "exclude_text=" in feed_url:
                    words_part = feed_url.split("exclude_text=")[1].split("&")[0]
                    excluded_words = [urllib.parse.unquote(words_part)]
                
                bridge_feeds.append({
                    "title": feed.get("title", "Unknown"),
                    "channel": channel,
                    "feed_url": feed_url,
                    "flags": flags,
                    "excluded_words": excluded_words,
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
        
        for cat_title, feeds in categories.items():
            # Build category message
            cat_message = f"📁 {cat_title}\n"
            for feed in feeds:
                channel_name = feed["title"]
                
                feed_line = f"  • {channel_name}"
                
                # Add flags if present
                if feed["flags"]:
                    feed_line += f", flags: {' '.join(feed['flags'])}"
                
                # Add excluded words if present
                if feed["excluded_words"] and feed["excluded_words"][0]:
                    words = ', '.join([w.strip('"\'') for w in feed["excluded_words"]])
                    feed_line += f", words: {words}"
                
                cat_message += feed_line + "\n"
            
            # Check if message is too long (Telegram limit is 4096 chars)
            if len(cat_message) > 4000:
                # Split into multiple messages
                chunks = []
                current_chunk = f"📁 {cat_title} (continued)\n"
                
                for feed in feeds:
                    channel_name = feed["title"]
                    
                    feed_line = f"  • {channel_name}"
                    
                    # Add flags if present
                    if feed["flags"]:
                        feed_line += f", flags: {' '.join(feed['flags'])}"
                    
                    # Add excluded words if present
                    if feed["excluded_words"] and feed["excluded_words"][0]:
                        words = ', '.join([w.strip('"\'') for w in feed["excluded_words"]])
                        feed_line += f", words: {words}"
                    
                    feed_text = feed_line + "\n"
                    
                    # If adding this feed would make the chunk too long, send it and start a new one
                    if len(current_chunk) + len(feed_text) > 4000:
                        chunks.append(current_chunk)
                        current_chunk = f"📁 {cat_title} (continued)\n"
                    
                    current_chunk += feed_text
                
                # Add the last chunk if it has content
                if current_chunk != f"📁 {cat_title} (continued)\n":
                    chunks.append(current_chunk)
                
                # Send all chunks
                for chunk in chunks:
                    await update.message.reply_text(chunk)
            else:
                # Send as a single message
                await update.message.reply_text(cat_message)
        
    except Exception as error:
        logging.error(f"Failed to list channels: {error}", exc_info=True)
        await update.message.reply_text(f"Failed to list channels: {str(error)}")

async def handle_message(update: Update, context: CallbackContext):
    """
    Handle incoming messages in private chat.
    If the message is forwarded from a channel, fetch categories and ask user to select one.
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
    logging.info(f"Message details:\n{json.dumps(msg_dict, indent=4)}")

    forward_chat = msg_dict.get("forward_from_chat")
    if not forward_chat:
        logging.info("Message is not forwarded from channel")
        await update.message.reply_text("Please forward a message from a channel.")
        return

    if forward_chat["type"] != "channel":
        logging.info(f"Forwarded message is from {forward_chat['type']}, not from channel")
        await update.message.reply_text("Please forward a message from a channel, not from other source.")
        return

    # If this is part of a media group, mark it as processed
    if media_group_id:
        context.user_data["processed_media_group_id"] = media_group_id
        logging.info(f"Processing first message from media group {media_group_id}")

    logging.info(f"ACCEPT_CHANNELS_WITOUT_USERNAME={ACCEPT_CHANNELS_WITOUT_USERNAME}, username={forward_chat.get('username')}")
    accept_no_username = ACCEPT_CHANNELS_WITOUT_USERNAME.lower() == "true"
    if not forward_chat.get("username") and not accept_no_username:
        logging.error(f"Channel {forward_chat['title']} has no username")
        await update.message.reply_text("Error: channel must have a public username to subscribe. \nUse env ACCEPT_CHANNELS_WITOUT_USERNAME=true to accept channels without username (need support from RSS bridge).")
        return

    channel_username = forward_chat.get("username") or str(forward_chat.get("id"))
    context.user_data["channel_title"] = channel_username
    logging.info(f"Processing forwarded message from channel: {channel_username}")

    feed_url = RSS_BRIDGE_URL.replace("{channel}", channel_username) if "{channel}" in RSS_BRIDGE_URL else f"{RSS_BRIDGE_URL}/{channel_username}"
    
    await update.message.chat.send_action("typing")
    try:
        feeds = miniflux_client.get_feeds()
        target_feed = None
        for feed in feeds:
            feed_url_check = feed.get("feed_url", "")
            # Simple check first
            if feed_url == feed_url_check:
                 target_feed = feed
                 break
            # Check ignoring query parameters if simple check failed
            parsed_target = urllib.parse.urlparse(feed_url)
            parsed_check = urllib.parse.urlparse(feed_url_check)
            if parsed_target.scheme == parsed_check.scheme and \
               parsed_target.netloc == parsed_check.netloc and \
               parsed_target.path == parsed_check.path:
                target_feed = feed
                break
        
        if target_feed:
            logging.info(f"Channel @{channel_username} is already in subscriptions")
            
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
            logging.error(f"Failed to delete feed: {e}", exc_info=True)
            await query.edit_message_text(f"Failed to delete channel: {str(e)}")

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
    Create keyboard with flag options, showing current status (✅/❌).

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

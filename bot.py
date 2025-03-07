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
        # Check if feed exists and get feed details if it does
        feed_exists, feed_id, current_flags = check_feed_exists_with_details(miniflux_client, feed_url)
        
        if feed_exists:
            logging.info(f"Channel @{channel_username} is already in subscriptions")
            
            # Store feed_id in context for callback handlers
            context.user_data["current_feed_id"] = feed_id
            context.user_data["current_feed_url"] = feed_url
            
            # Create keyboard with management options
            keyboard = [
                [InlineKeyboardButton("Delete channel from subscriptions", callback_data="delete_feed")],
            ]
            
            # Add flag buttons
            available_flags = [
                "fwd", "video", "stream", "donat", "clown", 
                "poo", "advert", "link", "mention", "hid_channel", "foreign_channel"
            ]
            
            # Create rows with 3 buttons each for flags
            flag_buttons = []
            row = []
            
            for flag in available_flags:
                # Check if flag is already set
                is_set = flag in current_flags
                button_text = f"✅ {flag}" if is_set else f"➕ {flag}"
                row.append(InlineKeyboardButton(button_text, callback_data=f"toggle_flag_{flag}"))
                
                # Create a new row after every 3 buttons
                if len(row) == 3:
                    flag_buttons.append(row)
                    row = []
            
            # Add any remaining buttons
            if row:
                flag_buttons.append(row)
            
            # Add flag buttons to keyboard
            keyboard.extend(flag_buttons)
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await update.message.reply_text(
                f"Channel @{channel_username} is already in subscriptions. What would you like to do?",
                reply_markup=reply_markup
            )
            return
    except Exception as error:
        logging.error(f"Failed to check subscriptions: {error}")
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

def check_feed_exists_with_details(client, feed_url):
    """
    Check if feed already exists in subscriptions and return details
    
    Returns:
        tuple: (exists, feed_id, current_flags)
    """
    try:
        feeds = client.get_feeds()
        for feed in feeds:
            if feed["feed_url"] == feed_url:
                # Extract current flags
                current_flags = []
                if "exclude_flags=" in feed_url:
                    flags_part = feed_url.split("exclude_flags=")[1].split("&")[0]
                    current_flags = flags_part.split(",")
                
                return True, feed["id"], current_flags
        return False, None, []
    except Exception as error:
        logging.error(f"Failed to check existing feeds: {error}")
        raise

async def button_callback(update: Update, context: CallbackContext):
    """
    Handle callback query when user selects a category or management option.
    """
    query = update.callback_query
    await query.answer()
    data = query.data
    
    if data.startswith("cat_"):
        # Handle category selection (existing code)
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
    
    elif data == "delete_feed":
        # Handle feed deletion
        feed_id = context.user_data.get("current_feed_id")
        if not feed_id:
            await query.edit_message_text("Feed information is missing.")
            return
        
        try:
            miniflux_client.delete_feed(feed_id)
            await query.edit_message_text("Channel successfully deleted from subscriptions.")
        except Exception as error:
            logging.error(f"Failed to delete feed: {error}", exc_info=True)
            await query.edit_message_text(f"Error deleting channel: {str(error)}")
    
    elif data.startswith("toggle_flag_"):
        # Handle flag toggling
        flag = data.split("_", 2)[2]
        feed_id = context.user_data.get("current_feed_id")
        feed_url = context.user_data.get("current_feed_url")
        
        if not feed_id or not feed_url:
            await query.edit_message_text("Feed information is missing.")
            return
        
        try:
            # Get current feed data
            feed = miniflux_client.get_feed(feed_id)
            current_feed_url = feed.get("feed_url", "")
            
            # Parse current flags
            current_flags = []
            if "exclude_flags=" in current_feed_url:
                flags_part = current_feed_url.split("exclude_flags=")[1].split("&")[0]
                current_flags = flags_part.split(",")
            
            # Toggle flag
            if flag in current_flags:
                current_flags.remove(flag)
                action = "removed from"
            else:
                current_flags.append(flag)
                action = "added to"
            
            # Create new URL with updated flags
            new_url = current_feed_url
            if current_flags:
                # Replace or add flags parameter
                flags_str = ",".join(current_flags)
                if "exclude_flags=" in current_feed_url:
                    parts = current_feed_url.split("exclude_flags=")
                    rest = parts[1].split("&", 1)
                    if len(rest) > 1:
                        new_url = f"{parts[0]}exclude_flags={flags_str}&{rest[1]}"
                    else:
                        new_url = f"{parts[0]}exclude_flags={flags_str}"
                else:
                    # Add flags parameter
                    if "?" in current_feed_url:
                        new_url = f"{current_feed_url}&exclude_flags={flags_str}"
                    else:
                        new_url = f"{current_feed_url}?exclude_flags={flags_str}"
            else:
                # Remove flags parameter entirely
                if "exclude_flags=" in current_feed_url:
                    parts = current_feed_url.split("exclude_flags=")
                    rest = parts[1].split("&", 1)
                    if len(rest) > 1:
                        new_url = f"{parts[0]}{rest[1]}"
                    else:
                        # Remove the query parameter separator if it's the only parameter
                        new_url = parts[0].rstrip("?&")
            
            # Update feed URL
            success, updated_url, _ = update_feed_url(feed_id, new_url)
            
            if not success:
                await query.edit_message_text(
                    f"Failed to update feed URL. Miniflux may be ignoring URL parameters.\n"
                    f"Please update the URL manually in the Miniflux interface:\n"
                    f"{new_url}"
                )
                return
            
            # Update keyboard with new flag status
            channel_title = context.user_data.get("channel_title")
            
            # Create keyboard with management options
            keyboard = [
                [InlineKeyboardButton("Delete channel from subscriptions", callback_data="delete_feed")],
            ]
            
            # Add flag buttons
            available_flags = [
                "fwd", "video", "stream", "donat", "clown", 
                "poo", "advert", "link", "mention", "hid_channel", "foreign_channel"
            ]
            
            # Extract updated flags from the updated URL
            updated_flags = []
            if "exclude_flags=" in updated_url:
                flags_part = updated_url.split("exclude_flags=")[1].split("&")[0]
                updated_flags = flags_part.split(",")
            
            # Create rows with 3 buttons each for flags
            flag_buttons = []
            row = []
            
            for available_flag in available_flags:
                # Check if flag is set
                is_set = available_flag in updated_flags
                button_text = f"✅ {available_flag}" if is_set else f"➕ {available_flag}"
                row.append(InlineKeyboardButton(button_text, callback_data=f"toggle_flag_{available_flag}"))
                
                # Create a new row after every 3 buttons
                if len(row) == 3:
                    flag_buttons.append(row)
                    row = []
            
            # Add any remaining buttons
            if row:
                flag_buttons.append(row)
            
            # Add flag buttons to keyboard
            keyboard.extend(flag_buttons)
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            await query.edit_message_text(
                f"Flag '{flag}' {action} channel @{channel_title}.\nWhat would you like to do next?",
                reply_markup=reply_markup
            )
            
        except Exception as error:
            logging.error(f"Failed to toggle flag: {error}", exc_info=True)
            await query.edit_message_text(f"Error changing flag: {str(error)}")

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
        # Update feed with new URL
        miniflux_client.update_feed(feed_id=feed_id, feed_url=new_url)
        
        # Get the updated feed
        updated_feed = miniflux_client.get_feed(feed_id)
        updated_url = updated_feed.get("feed_url", "")
        logging.info(f"Verified updated feed URL: {updated_url}")
        
        # Check if the URL was updated
        if updated_url == new_url:
            logging.info(f"URL successfully updated to: {updated_url}")
            return True, updated_url, ""
        else:
            logging.error(f"URL update failed! Expected: {new_url}, Got: {updated_url}")
            return False, updated_url, ""
            
    except Exception as e:
        logging.error(f"Failed to update feed: {e}", exc_info=True)
        raise

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

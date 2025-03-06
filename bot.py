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
        if check_feed_exists(miniflux_client, feed_url):
            logging.info(f"Channel @{channel_username} is already in subscriptions")
            await update.message.reply_text(f"Channel @{channel_username} is already in subscriptions.")
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

async def button_callback(update: Update, context: CallbackContext):
    """
    Handle callback query when user selects a category.
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
            await update.message.reply_text(f"Channel @{channel_name} not found in subscriptions.")
            return
        
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
            await update.message.reply_text(f"Flag '{flag_to_add}' is already set for channel @{channel_name}.")
            return
        
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

        # Create headers for the request
        headers = {
            "Content-Type": "application/json"
        }

        # Use basic authentication
        auth = (MINIFLUX_USERNAME, MINIFLUX_PASSWORD)
        
        # Create data for the request
        data = {
            "feed_url": new_url,
            # Add all other fields from the current feed to avoid losing them
            "site_url": target_feed.get("site_url", ""),
            "title": target_feed.get("title", ""),
            "category_id": target_feed.get("category", {}).get("id"),
            "crawler": target_feed.get("crawler", False),
            "user_agent": target_feed.get("user_agent", ""),
            "username": target_feed.get("username", ""),
            "password": target_feed.get("password", "")
        }
        
        # Send PUT request directly
        api_url = f"{MINIFLUX_BASE_URL.rstrip('/')}/v1/feeds/{feed_id}"
        logging.info(f"Sending PUT request to {api_url} with data: {data}")
        
        response = requests.put(api_url, json=data, headers=headers, auth=auth, timeout=10)
        response.raise_for_status()  # Will raise an exception if status is not 2xx
        
        logging.info(f"API response: {response.status_code} - {response.text}")
        
        # Get the updated feed
        updated_feed = miniflux_client.get_feed(feed_id)
        updated_url = updated_feed.get("feed_url", "")
        logging.info(f"Verified updated feed URL: {updated_url}")
        
        # Check if the URL was updated
        if updated_url == new_url:
            logging.info(f"URL successfully updated to: {updated_url}")
        else:
            logging.error(f"URL update failed! Expected: {new_url}, Got: {updated_url}")
            
            # If URL was not updated, show a message to the user
            await update.message.reply_text(
                f"Failed to update feed URL. Miniflux may be ignoring URL parameters.\n"
                f"Please update the URL manually in the Miniflux interface:\n"
                f"{new_url}"
            )
            return
        
        # Extract updated flags from the updated URL
        updated_flags = []
        if "exclude_flags=" in updated_url:
            flags_part = updated_url.split("exclude_flags=")[1].split("&")[0]
            updated_flags = flags_part.split(",")
        
        # Display updated flags separated by spaces
        flags_display = " ".join(updated_flags)
        
        await update.message.reply_text(
            f"Added flag '{flag_to_add}' to channel @{channel_name}.\n"
            f"Current flags: {flags_display}"
        )
        
    except Exception as e:
        logging.error(f"Failed to update feed: {e}", exc_info=True)
        await update.message.reply_text(f"Failed to add flag: {str(e)}")
    

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
                ("add_flag", "Add flag to channel: /add_flag channel flag")
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
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    
    # Start the bot
    application.run_polling()

if __name__ == "__main__":
    main()

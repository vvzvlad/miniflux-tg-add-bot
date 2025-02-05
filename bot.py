import logging
import os
import json
import urllib.parse
import miniflux
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

def main():
    """
    Initialize the Telegram bot and register handlers.
    """
    application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.run_polling()

if __name__ == "__main__":
    main()

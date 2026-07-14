"""Telegram application assembly: handlers, error handler, polling."""

import logging

from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackContext,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from src.handlers.callbacks import button_callback
from src.handlers.commands import list_channels, start
from src.handlers.messages import handle_message
from src.settings import settings

ERROR_MESSAGE = "Sorry, something went wrong while processing your request. Please try again."


async def post_init(application: Application) -> None:
    """Set up the bot commands after initialization."""
    try:
        commands = [
            ("start", "Start working with the bot"),
            ("list", "Show list of subscribed channels"),
        ]
        await application.bot.set_my_commands(commands)
        logging.info("Bot commands have been set up successfully")
    except Exception as error:
        logging.error(f"Failed to set up bot commands: {error}")


async def error_handler(update: object, context: CallbackContext) -> None:
    """Log any unhandled handler exception and let the user know something failed.

    Without this, an unhandled exception leaves the user with no reply at all and
    the bot looks dead.
    """
    logging.error("Unhandled exception while processing an update", exc_info=context.error)

    if isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(chat_id=update.effective_chat.id, text=ERROR_MESSAGE)
        except Exception as error:
            logging.error(f"Failed to notify the user about an unhandled error: {error}")


def build_application() -> Application:
    """Build the Telegram application with all handlers registered."""
    application = (
        ApplicationBuilder()
        .token(settings.telegram_token)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("list", list_channels))
    application.add_handler(MessageHandler(filters.ChatType.PRIVATE, handle_message))
    application.add_handler(CallbackQueryHandler(button_callback))
    application.add_error_handler(error_handler)

    return application


def run() -> None:
    """Start the bot (long polling)."""
    logging.info("--- Configuration Settings ---")
    logging.info(f"MINIFLUX_BASE_URL: {settings.miniflux_base_url}")
    logging.info(f"RSS_BRIDGE_URL: {settings.rss_bridge_url}")
    logging.info(f"ACCEPT_CHANNELS_WITHOUT_USERNAME: {settings.accept_channels_without_username}")
    logging.info("----------------------------")

    application = build_application()
    application.run_polling()

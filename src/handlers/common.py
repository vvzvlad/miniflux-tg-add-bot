"""Helpers shared by the handlers."""

import logging

from telegram import Update
from telegram.error import BadRequest

from src.settings import is_admin

ACCESS_DENIED_MESSAGE = "Access denied. Only admin can use this bot."


async def ensure_admin(update: Update, action: str) -> bool:
    """Check that the message comes from the admin, replying with a refusal if not."""
    user = update.message.from_user if update.message else None
    if not user or not is_admin(user.username):
        logging.warning(
            f"Unauthorized access attempt for {action} from user: {user.username if user else 'Unknown'}"
        )
        await update.message.reply_text(ACCESS_DENIED_MESSAGE)
        return False
    return True


async def safe_edit_message(query, text: str, reply_markup=None) -> None:
    """Edit a callback query message, tolerating Telegram's "not modified" error.

    Editing a message to exactly the same text and markup (e.g. by pressing the
    same button twice) makes Telegram raise BadRequest; that is not an error for
    us, so it is swallowed. Any other BadRequest is re-raised.
    """
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as error:
        if "message is not modified" in str(error).lower():
            logging.debug("Ignoring Telegram 'message is not modified' error.")
            return
        raise

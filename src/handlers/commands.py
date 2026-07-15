"""Command handlers: /start and /list."""

import asyncio
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from src.handlers.common import clear_edit_state, ensure_admin
from src.miniflux_api import get_channels_by_category, get_client
from src.settings import settings

# Telegram's hard limit is 4096 characters; stay below it with a margin.
MAX_MESSAGE_LENGTH = 4000
# Telegram allows ~100 inline buttons per message; keep a margin so one message
# never carries more manage buttons than it has room for.
MAX_CHANNELS_PER_MESSAGE = 90


async def start(update: Update, context: CallbackContext):
    """
    Handle the /start command.
    Only processes commands from admin user.
    """
    if not await ensure_admin(update, "/start"):
        return

    # Running any command leaves a stuck regex / merge time edit flow.
    clear_edit_state(context)

    await update.message.reply_text(
        "Forward me a message from any channel (public or private) or send a link to a message "
        "to subscribe to its RSS feed."
    )


async def cancel(update: Update, context: CallbackContext):
    """Handle the /cancel command: leave any active edit flow."""
    if not await ensure_admin(update, "/cancel"):
        return

    was_editing = context.user_data.get('state') is not None
    clear_edit_state(context)
    await update.message.reply_text(
        "Cancelled. No changes were made." if was_editing else "Nothing to cancel."
    )


def _format_feed_line(feed_item: dict) -> str:
    """Format a single feed as one line of the /list output."""
    line = f"  • {feed_item['title']}"

    if feed_item["flags"]:
        line += f", flags: {' '.join(feed_item['flags'])}"

    if feed_item["excluded_text"]:
        line += f", regex: {feed_item['excluded_text']}"

    if feed_item.get("merge_seconds"):
        line += f", merge: {feed_item['merge_seconds']}s"

    return line + "\n"


def _build_category_messages(cat_title: str, feeds_in_cat: list[dict]) -> list[tuple[str, list[dict]]]:
    """Render one category into as many messages as the Telegram limit requires.

    Returns a list of (text, feeds_covered) tuples so the caller can attach a
    keyboard whose buttons match exactly the feeds shown in that message.
    """
    header = f"📁 {cat_title}\n"
    continued_header = f"📁 {cat_title} (continued)\n"
    lines = [_format_feed_line(feed_item) for feed_item in feeds_in_cat]

    full_message = header + "".join(lines)
    if len(full_message) <= MAX_MESSAGE_LENGTH and len(feeds_in_cat) <= MAX_CHANNELS_PER_MESSAGE:
        return [(full_message, list(feeds_in_cat))]

    chunks: list[tuple[str, list[dict]]] = []
    current_text = header
    current_feeds: list[dict] = []
    for feed_item, line in zip(feeds_in_cat, lines, strict=True):
        # Split on either the character limit or the per-message button budget.
        if current_feeds and (
            len(current_text) + len(line) > MAX_MESSAGE_LENGTH
            or len(current_feeds) >= MAX_CHANNELS_PER_MESSAGE
        ):
            chunks.append((current_text, current_feeds))
            current_text = continued_header
            current_feeds = []
        current_text += line
        current_feeds.append(feed_item)

    # Append the tail chunk unless it holds nothing but the continuation header
    if len(current_text.strip()) > len(continued_header.strip()):
        chunks.append((current_text, current_feeds))

    return chunks


async def list_channels(update: Update, context: CallbackContext):
    """
    Handle the /list command.
    Fetches structured channel data and formats it for Telegram display.
    """
    if not await ensure_admin(update, "/list"):
        return

    # Running any command leaves a stuck regex / merge time edit flow.
    clear_edit_state(context)

    await update.message.chat.send_action("typing")

    try:
        channels_by_category = await asyncio.to_thread(
            get_channels_by_category, get_client(), settings.rss_bridge_url
        )

        if not channels_by_category:
            await update.message.reply_text("No channels subscribed through RSS Bridge found.")
            return

        await update.message.reply_text("Subscribed channels by category:")

        for cat_title, feeds_in_cat in channels_by_category.items():
            for text, feeds_in_msg in _build_category_messages(cat_title, feeds_in_cat):
                # One management button per feed that carries a channel name.
                buttons = [
                    [InlineKeyboardButton(
                        f"⚙️ {feed['title']}", callback_data=f"manage|{feed['channel']}"
                    )]
                    for feed in feeds_in_msg
                    if feed.get("channel")
                ]
                if buttons:
                    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(buttons))
                else:
                    await update.message.reply_text(text)

    except Exception as error:
        logging.error(f"Failed to list channels: {error}", exc_info=True)
        await update.message.reply_text(f"Failed to list channels: {str(error)}")

"""Command handlers: /start and /list."""

import asyncio
import logging

from telegram import Update
from telegram.ext import CallbackContext

from src.handlers.common import ensure_admin
from src.miniflux_api import get_channels_by_category, get_client
from src.settings import settings

# Telegram's hard limit is 4096 characters; stay below it with a margin.
MAX_MESSAGE_LENGTH = 4000


async def start(update: Update, _context: CallbackContext):
    """
    Handle the /start command.
    Only processes commands from admin user.
    """
    if not await ensure_admin(update, "/start"):
        return

    await update.message.reply_text(
        "Forward me a message from any channel (public or private) or send a link to a message "
        "to subscribe to its RSS feed."
    )


def _format_feed_line(feed_item: dict) -> str:
    """Format a single feed as one line of the /list output."""
    line = f"  • {feed_item['title']}"

    if feed_item["flags"]:
        line += f", flags: {' '.join(feed_item['flags'])}"

    if feed_item["excluded_text"]:
        line += f", regex: {feed_item['excluded_text']}"

    return line + "\n"


def _build_category_messages(cat_title: str, feeds_in_cat: list[dict]) -> list[str]:
    """Render one category into as many messages as the Telegram limit requires."""
    header = f"📁 {cat_title}\n"
    continued_header = f"📁 {cat_title} (continued)\n"
    lines = [_format_feed_line(feed_item) for feed_item in feeds_in_cat]

    full_message = header + "".join(lines)
    if len(full_message) <= MAX_MESSAGE_LENGTH:
        return [full_message]

    chunks = []
    current_chunk = header
    for line in lines:
        if len(current_chunk) + len(line) > MAX_MESSAGE_LENGTH:
            chunks.append(current_chunk)
            current_chunk = continued_header
        current_chunk += line

    # Append the tail chunk unless it holds nothing but the continuation header
    if len(current_chunk.strip()) > len(continued_header.strip()):
        chunks.append(current_chunk)

    return chunks


async def list_channels(update: Update, _context: CallbackContext):
    """
    Handle the /list command.
    Fetches structured channel data and formats it for Telegram display.
    """
    if not await ensure_admin(update, "/list"):
        return

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
            for message in _build_category_messages(cat_title, feeds_in_cat):
                await update.message.reply_text(message)

    except Exception as error:
        logging.error(f"Failed to list channels: {error}", exc_info=True)
        await update.message.reply_text(f"Failed to list channels: {str(error)}")

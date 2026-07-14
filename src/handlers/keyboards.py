"""Inline keyboards and the cached list of flags supported by the RSS bridge."""

import asyncio
import logging
import time

import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.settings import settings

# The flag list changes rarely; cache it so a burst of button presses does not
# hammer the bridge (a keyboard is rebuilt on almost every interaction).
FLAGS_CACHE_TTL_SECONDS = 300
# A failed fetch is cached for a shorter time so the bridge recovers quickly.
FLAGS_CACHE_FAILURE_TTL_SECONDS = 30

FLAGS_UNAVAILABLE_NOTE = "\n\n⚠️ Flag list is temporarily unavailable, flag buttons are hidden."

# Module-level cache: (timestamp, flags)
_flags_cache: tuple[float, list[str]] | None = None


def fetch_available_flags(base_url: str | None) -> list[str]:
    """
    Fetch the list of available flags from the RSS Bridge API.

    This performs a blocking HTTP request: async callers must wrap it in
    asyncio.to_thread() (see get_available_flags()).

    Args:
        base_url: The RSS Bridge URL template.

    Returns:
        list: The available flags, or an empty list on failure. A failure never
        fabricates a flag: a made-up value would be rendered as a real button and
        written into the feed URL if pressed.
    """
    if not base_url:
        logging.error("missing base_url for fetch_available_flags")
        return []

    try:
        # The token is the path segment right after the {channel} placeholder,
        # e.g. "https://bridge.example.com/channel/{channel}/token".
        token = None
        if "{channel}" in base_url:
            parts = base_url.split("{channel}")
            if len(parts) > 1 and parts[1].startswith("/"):
                token_part = parts[1].strip("/").split("/")
                if token_part:
                    token = token_part[0]

        if not token:
            logging.error("could not extract token from RSS_BRIDGE_URL")
            return []

        # Only the protocol and the domain of the bridge are needed
        base_domain = "/".join(base_url.split("/")[:3])
        flags_url = f"{base_domain}/flags/{token}"
        logging.info(f"fetching flags from {flags_url}")

        response = requests.get(flags_url, timeout=10)

        if response.status_code == 200:
            flags = response.json()
            logging.info(f"successfully fetched {len(flags)} flags from bridge")
            return flags

        logging.error(f"failed to fetch flags: http status {response.status_code}")
        return []
    except Exception as e:
        logging.error(f"error fetching flags from bridge: {e}")
        return []


async def get_available_flags() -> list[str]:
    """Return the available flags, fetching them off the event loop and caching them."""
    global _flags_cache

    now = time.monotonic()
    if _flags_cache is not None:
        cached_at, cached_flags = _flags_cache
        ttl = FLAGS_CACHE_TTL_SECONDS if cached_flags else FLAGS_CACHE_FAILURE_TTL_SECONDS
        if now - cached_at < ttl:
            return cached_flags

    flags = await asyncio.to_thread(fetch_available_flags, settings.rss_bridge_url)
    _flags_cache = (time.monotonic(), flags)
    return flags


async def create_flag_keyboard(
    channel_username: str,
    current_flags: list[str] | None,
    current_merge_seconds: int | None = None,
    available_flags: list[str] | None = None,
) -> list[list[InlineKeyboardButton]]:
    """
    Create the channel options keyboard: a toggle per available flag (✅/❌),
    plus edit regex, edit merge time (with current value) and delete buttons.

    Args:
        channel_username: Channel username or ID.
        current_flags: Flags currently set on the feed (may be None).
        current_merge_seconds: The current merge time in seconds (or None).
        available_flags: Pre-fetched flag list; fetched (from cache) when omitted.

    Returns:
        list: Keyboard buttons.
    """
    current_flags = current_flags or []
    all_flags = available_flags if available_flags is not None else await get_available_flags()

    keyboard: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []

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

    merge_time_text = "Edit Merge Time"
    if current_merge_seconds is not None:
        merge_time_text += f" ({current_merge_seconds}s)"
    keyboard.append([InlineKeyboardButton(merge_time_text, callback_data=f"edit_merge_time|{channel_username}")])

    keyboard.append([InlineKeyboardButton("Delete channel", callback_data=f"delete|{channel_username}")])

    return keyboard


async def build_options_view(
    channel_username: str,
    current_flags: list[str] | None,
    current_merge_seconds: int | None = None,
) -> tuple[InlineKeyboardMarkup, str]:
    """Build the options keyboard for a channel.

    Returns the markup and a note to append to the message text: the note warns
    the user when the flag list could not be fetched and flag buttons are hidden.
    """
    available_flags = await get_available_flags()
    keyboard = await create_flag_keyboard(
        channel_username, current_flags, current_merge_seconds, available_flags=available_flags
    )
    note = "" if available_flags else FLAGS_UNAVAILABLE_NOTE
    return InlineKeyboardMarkup(keyboard), note


def build_category_keyboard(categories: list[dict]) -> tuple[InlineKeyboardMarkup, dict]:
    """Build the category selection keyboard.

    Returns the markup and the id -> title mapping the caller stores in user_data
    (it is needed to name the category in the confirmation message).
    """
    keyboard = []
    categories_dict = {}
    for category in categories:
        cat_title = category.get("title", "Unknown")
        cat_id = category.get("id")
        categories_dict[cat_id] = cat_title
        keyboard.append([InlineKeyboardButton(cat_title, callback_data=f"cat_{cat_id}")])
    return InlineKeyboardMarkup(keyboard), categories_dict

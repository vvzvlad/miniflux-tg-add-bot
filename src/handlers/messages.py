"""Message handler: forwards, Telegram links, RSS URLs and the edit-state flows."""

import asyncio
import logging
import re
from typing import NamedTuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext

from src.handlers.common import clear_edit_state, ensure_admin
from src.handlers.keyboards import build_category_keyboard, build_options_view
from src.miniflux_api import (
    check_feed_exists,
    fetch_categories,
    find_feed_by_channel,
    get_client,
    update_feed_url,
)
from src.settings import should_accept_channels_without_username
from src.url_constructor import build_feed_url, parse_feed_url
from src.url_utils import is_valid_rss_url, parse_telegram_link


class ParsedMessage(NamedTuple):
    """Result of parsing an incoming message.

    `handled` marks that the message was already answered (e.g. an invalid
    forward), so the caller must not fall through to the generic help text.
    """

    handled: bool = False
    channel_username: str | None = None
    channel_source_type: str | None = None
    direct_rss_url: str | None = None
    html_rss_links: list | None = None


async def _reply_with_options_keyboard(update: Update, channel_name: str, feed_id: int, text: str) -> None:
    """Re-fetch the feed and show the options keyboard for the channel."""
    try:
        client = get_client()
        feed = await asyncio.to_thread(client.get_feed, feed_id)
        parsed = parse_feed_url(feed.get("feed_url", ""))
        reply_markup, flags_note = await build_options_view(
            channel_name, parsed.get("flags") or [], parsed.get("merge_seconds")
        )
        await update.message.reply_text(f"{text}{flags_note}", reply_markup=reply_markup)
        logging.info(f"Displayed options keyboard for {channel_name}.")
    except Exception as error:
        logging.error(f"Failed to fetch flags/show keyboard for {channel_name}: {error}")


# Sentinel: a caller that does not pass a field keeps the feed's current value.
_KEEP = object()


async def _rebuild_and_update_feed(client, feed_id, channel_name, *, exclude_text=_KEEP, merge_seconds=_KEEP):
    """Fetch feed, override one field in its URL, push the update.

    Returns (status, error_message). status is one of:
    'ok' | 'no_url' | 'no_base_url' | 'update_failed'. error_message is set for 'update_failed'.
    The get_feed / update calls may raise: the caller catches those.
    """
    current_feed_data = await asyncio.to_thread(client.get_feed, feed_id)
    current_url = current_feed_data.get("feed_url", "")
    if not current_url:
        return ("no_url", None)
    parsed = parse_feed_url(current_url)
    base = parsed.get("base_url")
    if not base:
        return ("no_base_url", None)
    new_url = build_feed_url(
        base_url=base,
        channel_name=channel_name,
        flags=parsed.get("flags"),
        exclude_text=parsed.get("exclude_text") if exclude_text is _KEEP else exclude_text,
        merge_seconds=parsed.get("merge_seconds") if merge_seconds is _KEEP else merge_seconds,
    )
    success, _url, err = await asyncio.to_thread(update_feed_url, feed_id, new_url, client)
    return ("ok", None) if success else ("update_failed", err)


async def _handle_awaiting_regex(update: Update, context: CallbackContext):
    """Handles the logic when the bot is awaiting regex input."""
    msg = update.message
    channel_name = context.user_data.get('editing_regex_for_channel')
    feed_id = context.user_data.get('editing_feed_id')
    new_regex_raw = msg.text.strip() if msg.text else ""

    # Clean up state regardless of success/failure below
    clear_edit_state(context)
    logging.info(f"Processing new regex for channel {channel_name} (feed ID: {feed_id}). State cleared.")

    if not channel_name or not feed_id:
        logging.error("State 'awaiting_regex' was set, but channel_name or feed_id missing from context.")
        await msg.reply_text("Error: Missing context for regex update. Please try editing again.")
        return

    await msg.chat.send_action("typing")

    # '-' removes the regex filter
    remove_regex = new_regex_raw.lower() in ['-']
    regex_to_store = None if remove_regex or not new_regex_raw else new_regex_raw

    try:
        client = get_client()
        status, error_message = await _rebuild_and_update_feed(
            client, feed_id, channel_name, exclude_text=regex_to_store
        )

        if status == "no_url":
            logging.error(f"Could not retrieve current URL for feed {feed_id} ({channel_name}) before updating regex.")
            await msg.reply_text("Error: Could not retrieve current feed URL. Cannot update regex.")
            return
        if status == "no_base_url":
            logging.error(f"Could not extract base URL for feed {feed_id} ({channel_name})")
            await msg.reply_text("Internal error: could not determine base URL.")
            return
        if status == "update_failed":
            logging.error(
                f"Failed to update feed URL for {channel_name} (feed ID: {feed_id}) with new regex. "
                f"Error: {error_message}"
            )
            await msg.reply_text(
                f"Failed to update regex for channel @{channel_name}. Miniflux error: {error_message}"
            )
            return

        # status == "ok"
        if remove_regex or not regex_to_store:
            await msg.reply_text(f"Regex filter removed for channel @{channel_name}.")
        else:
            await msg.reply_text(f"Regex for channel @{channel_name} updated to: {regex_to_store}")

        await _reply_with_options_keyboard(
            update, channel_name, feed_id, f"Updated options for @{channel_name}. Choose an action:"
        )

    except Exception as error:
        logging.error(f"Error processing new regex for {channel_name}: {error}", exc_info=True)
        await msg.reply_text(f"An unexpected error occurred while updating the regex: {str(error)}")


async def _handle_awaiting_merge_time(update: Update, context: CallbackContext):
    """Handles the logic when the bot is awaiting merge time input."""
    msg = update.message
    channel_name = context.user_data.get('editing_merge_time_for_channel')
    feed_id = context.user_data.get('editing_feed_id')
    new_merge_time_raw = msg.text.strip() if msg.text else ""

    if not channel_name or not feed_id:
        clear_edit_state(context)
        logging.error("State 'awaiting_merge_time' was set, but channel_name or feed_id missing from context.")
        await msg.reply_text("Error: Missing context for merge time update. Please try editing again.")
        return

    # Validate the input BEFORE clearing the state: on invalid input the state is
    # kept so the user's next message is still read as a merge time.
    try:
        new_merge_seconds_to_set = int(new_merge_time_raw) if new_merge_time_raw else 0
    except ValueError:
        await msg.reply_text("Invalid input. Please send a number for merge time (seconds), or 0 to disable.")
        await _reply_with_options_keyboard(
            update, channel_name, feed_id, f"Options for @{channel_name}. Choose an action:"
        )
        return

    if new_merge_seconds_to_set < 0:
        await msg.reply_text("Merge time must be a non-negative number (or 0 to disable). Please try again.")
        await _reply_with_options_keyboard(
            update, channel_name, feed_id, f"Options for @{channel_name}. Choose an action:"
        )
        return

    # An empty input or 0 removes the merge time
    if new_merge_seconds_to_set == 0:
        new_merge_seconds_to_set = None
        logging.info(f"Received input to remove merge time for {channel_name}.")
    else:
        logging.info(f"Received new merge time for {channel_name}: {new_merge_seconds_to_set} seconds.")

    clear_edit_state(context)
    logging.info(f"Processing new merge time for channel {channel_name} (feed ID: {feed_id}). State cleared.")

    await msg.chat.send_action("typing")

    try:
        client = get_client()
        status, error_message = await _rebuild_and_update_feed(
            client, feed_id, channel_name, merge_seconds=new_merge_seconds_to_set
        )

        if status == "no_url":
            logging.error(
                f"Could not retrieve current URL for feed {feed_id} ({channel_name}) before updating merge time."
            )
            await msg.reply_text("Error: Could not retrieve current feed URL. Cannot update merge time.")
            return
        if status == "no_base_url":
            logging.error(f"Could not extract base URL for feed {feed_id} ({channel_name})")
            await msg.reply_text("Internal error: could not determine base URL.")
            return
        if status == "update_failed":
            logging.error(
                f"Failed to update feed URL for {channel_name} (feed ID: {feed_id}) with new merge time. "
                f"Error: {error_message}"
            )
            await msg.reply_text(
                f"Failed to update merge time for channel @{channel_name}. Miniflux error: {error_message}"
            )
            return

        # status == "ok"
        if new_merge_seconds_to_set is None:
            await msg.reply_text(f"Merge time filter removed for channel @{channel_name}.")
        else:
            await msg.reply_text(
                f"Merge time for channel @{channel_name} updated to: {new_merge_seconds_to_set} seconds."
            )

        await _reply_with_options_keyboard(
            update, channel_name, feed_id, f"Updated options for @{channel_name}. Choose an action:"
        )

    except Exception as error:
        logging.error(f"Error processing new merge time for {channel_name}: {error}", exc_info=True)
        await msg.reply_text(f"An unexpected error occurred while updating the merge time: {str(error)}")


async def _parse_message_content(update: Update, context: CallbackContext) -> ParsedMessage:
    """Parses the message to identify channel links, forwards, RSS feeds, or HTML with RSS."""
    msg = update.message
    msg_dict = msg.to_dict()

    # 1. Check for forward
    forward_chat = msg_dict.get("forward_from_chat")
    if forward_chat:
        if forward_chat["type"] != "channel":
            logging.info(f"Forwarded message is from {forward_chat['type']}, not from channel")
            await msg.reply_text("Please forward a message from a channel, not from other source.")
            return ParsedMessage(handled=True)

        logging.info(f"Processing forwarded message from channel: {forward_chat.get('username') or forward_chat.get('id')}")
        accept_no_username = should_accept_channels_without_username()
        logging.info(f"Value of ACCEPT_CHANNELS_WITHOUT_USERNAME: {accept_no_username}")
        if not forward_chat.get("username") and not accept_no_username:
            logging.error(f"Channel {forward_chat['title']} has no username and ACCEPT_CHANNELS_WITHOUT_USERNAME is false.")
            await msg.reply_text(
                "Error: channel must have a public username to subscribe. \n"
                "Use env ACCEPT_CHANNELS_WITHOUT_USERNAME=true to accept channels without username "
                "(needs support from RSS bridge)."
            )
            return ParsedMessage(handled=True)

        channel_username = forward_chat.get("username") or str(forward_chat.get("id"))
        # If this is part of a media group from a forward, mark it as processed
        media_group_id = msg.media_group_id
        if media_group_id:
            context.user_data["processed_media_group_id"] = media_group_id
            logging.info(f"Processing first forwarded message from media group {media_group_id}")

        return ParsedMessage(channel_username=channel_username, channel_source_type='forward')

    # 2. If not a forward, check for link or username in message text
    if msg.text:
        text = msg.text.strip()

        parsed_channel = None
        if text.startswith('@'):
            # Direct username mention (e.g. @channelname)
            match_username = re.match(r"@([a-zA-Z0-9_]+)", text)
            if match_username:
                parsed_channel = match_username.group(1)
                logging.info(f"Processing direct username: {parsed_channel}")
        else:
            parsed_channel = parse_telegram_link(text)

        if parsed_channel:
            logging.info(f"Processing Telegram channel identified as: {parsed_channel}")
            media_group_id = msg.media_group_id
            if media_group_id:
                context.user_data["processed_media_group_id"] = media_group_id
                logging.info(f"Processing first linked message from media group {media_group_id}")
            return ParsedMessage(channel_username=parsed_channel, channel_source_type='link_or_username')

        # Not a Telegram link: check whether it is a direct RSS/HTML URL
        if text.startswith('http://') or text.startswith('https://'):
            url = text
            logging.info(f"Checking if URL is a valid RSS feed or contains RSS links: {url}")
            await msg.chat.send_action("typing")
            is_direct_rss, result = await asyncio.to_thread(is_valid_rss_url, url)

            if is_direct_rss:
                logging.info(f"URL is a direct RSS feed: {result}")
                return ParsedMessage(direct_rss_url=result)

            if isinstance(result, list) and result:
                logging.info(f"Found {len(result)} RSS links in the webpage")
                return ParsedMessage(html_rss_links=result)

    # Nothing recognized
    return ParsedMessage()


async def _handle_telegram_channel(update: Update, context: CallbackContext, channel_username: str, channel_source_type: str):
    """Handles logic for processing a detected Telegram channel."""
    context.user_data["channel_title"] = channel_username
    logging.info(f"Processing Telegram channel identified as: {channel_username} (Source: {channel_source_type})")
    await update.message.chat.send_action("typing")

    client = get_client()
    try:
        target_feed = await asyncio.to_thread(find_feed_by_channel, client, channel_username)

        if target_feed:
            logging.info(f"Channel @{channel_username} is already in subscriptions (matched channel name)")
            feed_id = target_feed.get("id")
            current_flags = []
            current_merge_seconds = None
            try:
                # Re-fetch the feed to get the latest URL
                updated_target_feed = await asyncio.to_thread(client.get_feed, feed_id)
                parsed_current = parse_feed_url(updated_target_feed.get("feed_url", ""))
                current_flags = parsed_current.get("flags") or []
                current_merge_seconds = parsed_current.get("merge_seconds")
                logging.info(f"Current flags for @{channel_username}: {current_flags}, merge_seconds: {current_merge_seconds}")
            except Exception as error:
                logging.error(f"Failed to fetch current feed details for feed {feed_id}: {error}")
                await update.message.reply_text("Error fetching current feed status. Proceeding without status.")

            reply_markup, flags_note = await build_options_view(
                channel_username, current_flags, current_merge_seconds
            )
            await update.message.reply_text(
                f"Channel @{channel_username} is already in subscriptions. Choose an action:{flags_note}",
                reply_markup=reply_markup,
            )
            return

    except Exception as error:
        logging.error(f"Failed to check subscriptions or get existing feed details: {error}", exc_info=True)
        await update.message.reply_text("Failed to check existing subscriptions.")
        return

    # --- Channel feed does not exist, proceed with category selection ---
    try:
        categories = await asyncio.to_thread(fetch_categories, client)
    except Exception as error:
        logging.error(f"Failed to fetch categories: {error}")
        await update.message.reply_text("Failed to fetch categories from RSS reader.")
        return

    reply_markup, categories_dict = build_category_keyboard(categories)
    context.user_data["categories"] = categories_dict
    await update.message.reply_text(
        f"Select category for channel @{channel_username}:", reply_markup=reply_markup
    )


async def _handle_direct_rss(update: Update, context: CallbackContext, direct_rss_url: str):
    """Handles logic for processing a direct RSS feed URL."""
    client = get_client()
    try:
        if await asyncio.to_thread(check_feed_exists, client, direct_rss_url):
            await update.message.reply_text("This RSS feed is already in your subscriptions.")
            return
    except Exception as error:
        logging.error(f"Failed to check if feed exists: {error}")
        await update.message.reply_text(f"Failed to check if feed exists: {str(error)}")
        return

    context.user_data["direct_rss_url"] = direct_rss_url
    try:
        categories = await asyncio.to_thread(fetch_categories, client)
    except Exception as error:
        logging.error(f"Failed to fetch categories: {error}")
        await update.message.reply_text("Failed to fetch categories from RSS reader.")
        return

    reply_markup, categories_dict = build_category_keyboard(categories)
    context.user_data["categories"] = categories_dict
    await update.message.reply_text(
        "URL is a valid RSS feed. Select category:", reply_markup=reply_markup
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
        "Found multiple RSS feeds on the webpage. Select one to subscribe:",
        reply_markup=reply_markup,
    )


async def _handle_unknown_message(update: Update, _context: CallbackContext):
    """Handles messages that are not recognized as channels, RSS links, etc."""
    msg = update.message
    # A URL that looked like http(s) but turned out to be neither a feed nor an HTML page with feeds
    if msg and msg.text and (msg.text.startswith('http://') or msg.text.startswith('https://')):
        await update.message.reply_text(
            "The URL does not appear to be a valid RSS feed and no RSS links were found on the webpage."
        )
    else:
        logging.info("Message is not a forward, channel link, RSS feed, or webpage with RSS links.")
        await update.message.reply_text(
            "Please forward a message from any channel (public or private) or send a link to a message "
            "(e.g., https://t.me/channel_name/123 or https://t.me/-1002069358234/1951), "
            "or send a direct RSS feed URL."
        )


async def handle_message(update: Update, context: CallbackContext):
    """
    Handle incoming messages in private chat. Routes to specific handlers based on state or message content.
    Only processes messages from admin user.
    """
    msg = update.message
    if not msg:
        return

    if not await ensure_admin(update, "message"):
        return

    # --- State Handlers ---
    current_state = context.user_data.get('state')
    if current_state == 'awaiting_regex':
        await _handle_awaiting_regex(update, context)
        return
    if current_state == 'awaiting_merge_time':
        await _handle_awaiting_merge_time(update, context)
        return

    # --- Media Group Handling ---
    media_group_id = msg.media_group_id
    if media_group_id and context.user_data.get("processed_media_group_id") == media_group_id:
        logging.info(f"Skipping duplicate message from media group {media_group_id}")
        return

    # --- Content Parsing and Handling ---
    try:
        parsed = await _parse_message_content(update, context)

        # The message was already answered inside the parser (e.g. an invalid forward)
        if parsed.handled:
            return

        if parsed.channel_username:
            try:
                await _handle_telegram_channel(
                    update, context, parsed.channel_username, parsed.channel_source_type
                )
            except Exception as error:
                logging.error(f"Error processing telegram channel {parsed.channel_username}: {error}", exc_info=True)
                if error.__class__.__name__ == "TelegramError" and "rate limit" in str(error).lower():
                    await update.message.reply_text("Telegram API rate limit exceeded. Please try again later.")
                else:
                    await update.message.reply_text(
                        f"Error processing telegram channel @{parsed.channel_username}: {str(error)}"
                    )
        elif parsed.direct_rss_url:
            try:
                await _handle_direct_rss(update, context, parsed.direct_rss_url)
            except Exception as error:
                logging.error(f"Error processing RSS feed {parsed.direct_rss_url}: {error}", exc_info=True)
                await update.message.reply_text(f"Error processing RSS feed: {str(error)}")
        elif parsed.html_rss_links:
            try:
                await _handle_html_rss_links(update, context, parsed.html_rss_links)
            except Exception as error:
                logging.error(f"Error processing website with RSS links: {error}", exc_info=True)
                await update.message.reply_text(f"Error processing website with RSS links: {str(error)}")
        else:
            await _handle_unknown_message(update, context)
    except Exception as error:
        logging.error(f"Error parsing message content: {error}", exc_info=True)
        await update.message.reply_text(f"Error processing your message: {str(error)}")

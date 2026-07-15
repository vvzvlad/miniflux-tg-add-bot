"""Callback query handlers: category choice, RSS link choice, flags, regex, merge time, delete."""

import asyncio
import logging
import urllib.parse

from miniflux import ClientError, ServerError
from telegram import Update
from telegram.ext import CallbackContext

from src.handlers.common import clear_edit_state, safe_edit_message
from src.handlers.keyboards import build_category_keyboard, build_options_view
from src.miniflux_api import (
    check_feed_exists,
    create_feed,
    delete_feed,
    fetch_categories,
    find_feed_by_channel,
    format_miniflux_error,
    get_client,
    update_feed_url,
)
from src.settings import settings
from src.url_constructor import build_feed_url, parse_feed_url

REGEX_HELP = """
a*: 0 or more, a+: 1 or more, a?: 0 or 1
. — any character except newline
\\s — whitespace, \\S — not whitespace
\\d — digit, \\D — not digit
\\w — word, \\W — not word
[ABC] — character A, B or C, [^ABC] — not A, B or C
            """


async def _handle_flag_toggle(query, _context: CallbackContext, action: str, flag: str, channel_name: str):
    """Handles the logic for adding or removing a flag based on button press."""
    logging.info(f"Processing flag toggle: Action='{action}', Flag='{flag}', Channel='{channel_name}'")

    current_flags_on_error: list[str] = []
    current_merge_seconds_on_error = None

    try:
        client = get_client()
        # Resolve the feed from Miniflux by channel name: user_data does not survive
        # a restart, and the bot is restarted on every deployment.
        target_feed = await asyncio.to_thread(find_feed_by_channel, client, channel_name)
        if not target_feed:
            logging.error(f"No feed found for channel {channel_name} during flag toggle.")
            await safe_edit_message(query, f"Channel @{channel_name} not found in subscriptions.")
            return

        feed_id = target_feed.get("id")
        current_url = target_feed.get("feed_url", "")
        if not feed_id or not current_url:
            logging.error(f"Could not retrieve current URL for channel {channel_name}. Cannot toggle flag.")
            await safe_edit_message(query, f"Error: Could not get current feed details for @{channel_name}.")
            return

        parsed_data = parse_feed_url(current_url)
        current_flags = parsed_data.get("flags") or []
        current_merge_seconds = parsed_data.get("merge_seconds")
        # Remembered so the keyboard can be rebuilt in the error paths below
        current_flags_on_error = current_flags[:]
        current_merge_seconds_on_error = current_merge_seconds
        base_url_for_build = parsed_data.get("base_url")

        if not base_url_for_build:
            logging.error(f"Could not extract base URL from {current_url} for @{channel_name}")
            await safe_edit_message(query, "Internal error: could not determine base URL.")
            return

        # Calculate the new flag list
        new_flags = current_flags[:]
        success_message_part = ""
        if action == "add":
            if flag in new_flags:
                reply_markup, flags_note = await build_options_view(
                    channel_name, current_flags, current_merge_seconds
                )
                await safe_edit_message(
                    query,
                    f"Flag '{flag}' is already set for channel @{channel_name}. Choose an action:{flags_note}",
                    reply_markup=reply_markup,
                )
                return
            new_flags.append(flag)
            success_message_part = f"Flag '{flag}' added"
        elif action == "remove":
            if flag not in new_flags:
                reply_markup, flags_note = await build_options_view(
                    channel_name, current_flags, current_merge_seconds
                )
                await safe_edit_message(
                    query,
                    f"Flag '{flag}' is not set for channel @{channel_name}. Choose an action:{flags_note}",
                    reply_markup=reply_markup,
                )
                return
            new_flags = [existing for existing in new_flags if existing != flag]
            success_message_part = f"Flag '{flag}' removed"
        else:
            logging.error(f"Unknown flag action '{action}' requested.")
            await safe_edit_message(query, "Internal error: Unknown flag action.")
            return

        new_url = build_feed_url(
            base_url=base_url_for_build,
            channel_name=channel_name,
            flags=new_flags if new_flags else None,
            exclude_text=parsed_data.get("exclude_text"),
            merge_seconds=current_merge_seconds,
        )

        logging.info(f"Attempting flag update. Old flags: {current_flags}, New flags: {new_flags}. Target URL: {new_url}")

        success, _updated_url, error_message = await asyncio.to_thread(
            update_feed_url, feed_id, new_url, client
        )

        if not success:
            reply_markup, flags_note = await build_options_view(
                channel_name, current_flags_on_error, current_merge_seconds_on_error
            )
            await safe_edit_message(
                query,
                f"Failed to update flags for @{channel_name}. Error: {error_message}. Choose an action:{flags_note}",
                reply_markup=reply_markup,
            )
            return

        flags_display = " ".join(new_flags) if new_flags else "none"
        reply_markup, flags_note = await build_options_view(channel_name, new_flags, current_merge_seconds)
        await safe_edit_message(
            query,
            f"{success_message_part} for channel @{channel_name}. Current flags: {flags_display}\n"
            f"Choose an action:{flags_note}",
            reply_markup=reply_markup,
        )

    except Exception as error:
        logging.error(f"Failed during _handle_flag_toggle for {channel_name}: {error}", exc_info=True)
        reply_markup, flags_note = await build_options_view(
            channel_name, current_flags_on_error, current_merge_seconds_on_error
        )
        await safe_edit_message(
            query,
            f"Failed to process flag action: {str(error)}. Choose an action:{flags_note}",
            reply_markup=reply_markup,
        )


async def _handle_rss_link_selection(query, context: CallbackContext, data: str):
    """Handle the choice of one RSS link found on an HTML page."""
    client = get_client()
    link_index = int(data.split("_")[2])
    rss_links = context.user_data.get("rss_links", [])

    if not rss_links or link_index >= len(rss_links):
        await safe_edit_message(query, "Invalid RSS link selection or session expired.")
        return

    selected_link = rss_links[link_index]
    feed_url = selected_link.get("href")

    if not feed_url:
        await safe_edit_message(query, "Selected RSS link has no URL.")
        return

    try:
        if await asyncio.to_thread(check_feed_exists, client, feed_url):
            await safe_edit_message(query, "This RSS feed is already in your subscriptions.")
            return
    except Exception as error:
        logging.error(f"Failed to check if feed exists: {error}")
        await safe_edit_message(query, f"Failed to check if feed exists: {str(error)}")
        return

    # Store the selected RSS URL for the category selection step
    context.user_data["direct_rss_url"] = feed_url
    context.user_data.pop("rss_links", None)

    try:
        categories = await asyncio.to_thread(fetch_categories, client)
    except Exception as error:
        logging.error(f"Failed to fetch categories: {error}")
        await safe_edit_message(query, "Failed to fetch categories from RSS reader.")
        return

    reply_markup, categories_dict = build_category_keyboard(categories)
    context.user_data["categories"] = categories_dict

    await safe_edit_message(
        query,
        f"Selected RSS feed: {selected_link.get('title', 'RSS Feed')}\nChoose a category:",
        reply_markup=reply_markup,
    )


async def _handle_category_selection(query, context: CallbackContext, data: str):
    """Handle the category button: subscribe the pending RSS feed or Telegram channel."""
    client = get_client()
    cat_id_str = data.split("_", 1)[1]
    try:
        cat_id = int(cat_id_str)
    except ValueError:
        await safe_edit_message(query, "Invalid category ID.")
        return

    instance_url = settings.miniflux_base_url.rstrip('/').replace('http://', '').replace('https://', '')

    # --- Direct RSS feed subscription ---
    direct_rss_url = context.user_data.get("direct_rss_url")
    if direct_rss_url:
        feed_url = direct_rss_url

        await query.message.chat.send_action("typing")
        try:
            logging.info(f"Subscribing to direct RSS feed '{feed_url}' in category {cat_id}")
            await asyncio.to_thread(create_feed, client, feed_url, cat_id)
            # Clear the pending URL only after a successful subscription, so a failed
            # attempt can be retried without the user re-sending the link.
            context.user_data.pop("direct_rss_url", None)
            category_title = context.user_data.get("categories", {}).get(cat_id, "Unknown")
            await safe_edit_message(
                query,
                f"✅ Direct RSS feed {feed_url} has been subscribed on {instance_url} instance, "
                f"category '{category_title.strip()}'",
            )
        except (ClientError, ServerError) as error:
            error_message = format_miniflux_error(error)
            logging.error(f"Miniflux API error while subscribing to feed '{feed_url}': {error_message}")
            await safe_edit_message(query, f"Failed to subscribe to RSS feed '{feed_url}': {error_message}")
        except Exception as error:
            logging.error(f"Unexpected error while subscribing to feed '{feed_url}': {str(error)}", exc_info=True)
            await safe_edit_message(query, f"Unexpected error while subscribing to RSS feed: {str(error)}")
        return

    # --- Telegram channel subscription ---
    channel_title = context.user_data.get("channel_title")
    if not channel_title:
        await safe_edit_message(query, "Channel information is missing.")
        return

    # The bridge URL template is validated at startup, so the placeholder is always present.
    feed_url = settings.rss_bridge_url.replace("{channel}", urllib.parse.quote(channel_title, safe=""))

    await query.message.chat.send_action("typing")
    try:
        logging.info(f"Subscribing to feed '{feed_url}' in category {cat_id}")
        await asyncio.to_thread(create_feed, client, feed_url, cat_id)
        context.user_data.pop("channel_title", None)
        category_title = context.user_data.get("categories", {}).get(cat_id, "Unknown")
        await safe_edit_message(
            query,
            f"✅ Channel @{channel_title} has been subscribed on {instance_url} instance, "
            f"added to category '{category_title.strip()}'",
        )
    except (ClientError, ServerError) as error:
        error_message = format_miniflux_error(error)
        logging.error(f"Miniflux API error while subscribing to feed '{feed_url}': {error_message}")
        await safe_edit_message(query, f"Failed to subscribe to RSS feed '{feed_url}': {error_message}")
    except Exception as error:
        logging.error(f"Unexpected error while subscribing to feed '{feed_url}': {str(error)}", exc_info=True)
        await safe_edit_message(query, f"Unexpected error while subscribing to RSS feed: {str(error)}")


async def _handle_delete_channel(query, channel_name: str):
    """Handle the delete channel button."""
    client = get_client()
    await query.message.chat.send_action("typing")
    try:
        target_feed = await asyncio.to_thread(find_feed_by_channel, client, channel_name)
        if not target_feed:
            await safe_edit_message(query, f"Channel @{channel_name} not found in subscriptions.")
            return

        # The Miniflux client is synchronous: it must be called in a worker thread.
        success, error_message = await asyncio.to_thread(delete_feed, client, target_feed.get("id"))
        if not success:
            await safe_edit_message(query, f"Failed to delete channel: {error_message}")
            return

        await safe_edit_message(query, f"Channel @{channel_name} has been deleted from subscriptions.")

    except Exception as error:
        logging.error(f"Failed to delete feed for {channel_name}: {error}", exc_info=True)
        await safe_edit_message(query, f"Failed to delete channel: {str(error)}")


async def _handle_manage_channel(query, _context: CallbackContext, channel_name: str):
    """Open the options view for a subscribed channel (from the /list manage button).

    Mirrors the "already subscribed" branch of _handle_telegram_channel, but edits
    the /list message in place instead of sending a new one.
    """
    client = get_client()
    await query.message.chat.send_action("typing")
    try:
        target_feed = await asyncio.to_thread(find_feed_by_channel, client, channel_name)
        if not target_feed:
            await safe_edit_message(query, f"Channel @{channel_name} not found in subscriptions.")
            return

        feed_id = target_feed.get("id")
        current_feed = await asyncio.to_thread(client.get_feed, feed_id)
        parsed_current = parse_feed_url(current_feed.get("feed_url", ""))
        current_flags = parsed_current.get("flags") or []
        current_merge_seconds = parsed_current.get("merge_seconds")

        reply_markup, flags_note = await build_options_view(
            channel_name, current_flags, current_merge_seconds
        )
        await safe_edit_message(
            query,
            f"Options for @{channel_name}. Choose an action:{flags_note}",
            reply_markup=reply_markup,
        )

    except Exception as error:
        logging.error(f"Failed to open management view for {channel_name}: {error}", exc_info=True)
        await safe_edit_message(query, f"Failed to open options for @{channel_name}: {str(error)}")


async def _handle_edit_regex(query, context: CallbackContext, channel_name: str):
    """Handle the edit regex button: prompt the user and switch to the awaiting_regex state."""
    client = get_client()
    await query.message.chat.send_action("typing")
    try:
        target_feed = await asyncio.to_thread(find_feed_by_channel, client, channel_name)
        feed_id = target_feed.get("id") if target_feed else None

        if not target_feed or not feed_id:
            logging.warning(f"Target feed or feed_id not found for {channel_name} after searching feeds.")
            await safe_edit_message(
                query, f"Channel @{channel_name} not found in subscriptions or feed ID missing."
            )
            return

        parsed_data = parse_feed_url(target_feed.get("feed_url", ""))
        current_regex = parsed_data.get("exclude_text") or ""

        if current_regex:
            logging.info(f"Found current regex for {channel_name}: '{current_regex}'")
        else:
            logging.info(f"No current exclude_text regex found for {channel_name}")

        context.user_data['state'] = 'awaiting_regex'
        context.user_data['editing_regex_for_channel'] = channel_name
        context.user_data['editing_feed_id'] = feed_id
        logging.info(f"Set state to 'awaiting_regex' for channel {channel_name} (feed ID: {feed_id})")

        if current_regex:
            prompt_message = (
                f"Current regex for @{channel_name} is:\n{current_regex}\n\n"
                "Please send the new regex. Send '-' to remove the regex filter. \n"
                "Example: реклама|спам|сбор\\sденег|подписка\n\n"
                "Regex help:\n"
                f"{REGEX_HELP}"
            )
        else:
            prompt_message = (
                f"No current regex set for @{channel_name}.\n"
                "Please send the new regex. Send '-' to remove the regex filter. \n"
                "Example: реклама|спам|сбор\\sденег|подписка\n\n"
                "Regex help:\n"
                f"{REGEX_HELP}"
            )

        await safe_edit_message(query, prompt_message)

    except Exception as error:
        logging.error(f"Failed during edit_regex preparation for {channel_name}: {error}", exc_info=True)
        clear_edit_state(context)
        await safe_edit_message(query, f"Failed to start regex edit: {str(error)}")


async def _handle_edit_merge_time(query, context: CallbackContext, channel_name: str):
    """Handle the edit merge time button: prompt the user and switch to the awaiting_merge_time state."""
    client = get_client()
    await query.message.chat.send_action("typing")
    try:
        target_feed = await asyncio.to_thread(find_feed_by_channel, client, channel_name)
        feed_id = target_feed.get("id") if target_feed else None

        if not target_feed or not feed_id:
            logging.warning(f"Target feed or feed_id not found for {channel_name} after searching feeds.")
            await safe_edit_message(
                query, f"Channel @{channel_name} not found in subscriptions or feed ID missing."
            )
            return

        parsed_data = parse_feed_url(target_feed.get("feed_url", ""))
        current_merge_seconds = parsed_data.get("merge_seconds")

        if current_merge_seconds is not None:
            logging.info(f"Found current merge_seconds for {channel_name}: {current_merge_seconds}")
        else:
            logging.info(f"No current merge_seconds found for {channel_name}")

        context.user_data['state'] = 'awaiting_merge_time'
        context.user_data['editing_merge_time_for_channel'] = channel_name
        context.user_data['editing_feed_id'] = feed_id
        logging.info(f"Set state to 'awaiting_merge_time' for channel {channel_name} (feed ID: {feed_id})")

        prompt_message = f"Editing merge time for @{channel_name}.\n"
        if current_merge_seconds is not None:
            prompt_message += f"Current merge time: {current_merge_seconds} seconds.\n\n"
        else:
            prompt_message += "Merge time is not currently set.\n\n"
        prompt_message += "Please send the new merge time in seconds (e.g., 300). Send 0 to disable merging."

        await safe_edit_message(query, prompt_message)

    except Exception as error:
        logging.error(f"Failed during edit_merge_time preparation for {channel_name}: {error}", exc_info=True)
        clear_edit_state(context)
        await safe_edit_message(query, f"Failed to start merge time edit: {str(error)}")


async def button_callback(update: Update, context: CallbackContext):
    """
    Handle callback query when user selects a category or flag action.
    """
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("rss_link_"):
        try:
            await _handle_rss_link_selection(query, context, data)
        except Exception as error:
            logging.error(f"Error processing RSS link selection: {error}", exc_info=True)
            await safe_edit_message(query, f"Error processing RSS link selection: {str(error)}")

    elif data.startswith("cat_"):
        await _handle_category_selection(query, context, data)

    elif data.startswith("add_flag|") or data.startswith("remove_flag|"):
        try:
            action_part, channel_name, flag = data.split("|", 2)
            # 'add_flag' -> 'add', 'remove_flag' -> 'remove'
            action = action_part.split("_")[0]
            await _handle_flag_toggle(query, context, action, flag, channel_name)
        except ValueError as error:
            logging.error(f"Could not parse flag callback data: {data}. Error: {error}")
            await safe_edit_message(query, "Invalid callback data format for flag action.")
        except Exception as error:
            logging.error(f"Unexpected error processing flag callback '{data}': {error}", exc_info=True)
            await safe_edit_message(query, "An unexpected error occurred processing the flag action.")

    elif data.startswith("manage|"):
        await _handle_manage_channel(query, context, data.split("|", 1)[1])

    elif data.startswith("delete|"):
        await _handle_delete_channel(query, data.split("|", 1)[1])

    elif data.startswith("edit_regex|"):
        await _handle_edit_regex(query, context, data.split("|", 1)[1])

    elif data.startswith("edit_merge_time|"):
        await _handle_edit_merge_time(query, context, data.split("|", 1)[1])

    else:
        logging.warning(f"Received unknown callback query data: {data}")
        await safe_edit_message(query, "Unknown action.")

"""Tests for the handlers: /start, /list, incoming messages and button callbacks."""

import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from miniflux import ClientError

from src.handlers.callbacks import _handle_flag_toggle, button_callback
from src.handlers.commands import cancel, list_channels, start
from src.handlers.messages import (
    _handle_awaiting_merge_time,
    _handle_awaiting_regex,
    _handle_telegram_channel,
    handle_message,
)
from src.settings import settings

BRIDGE_URL = "http://test.rssbridge.local/rss/{channel}/test_token"


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    """Every test in this module runs as the admin against the test bridge."""
    return admin_settings


def feed_url_for(channel: str, query: str = "") -> str:
    """The feed URL the configured bridge template produces for a channel."""
    return BRIDGE_URL.replace("{channel}", channel) + query


# --- /start -----------------------------------------------------------------


async def test_start_admin(mock_update, mock_context):
    mock_update.message.from_user.username = "test_admin"

    await start(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Forward me a message from any channel" in mock_update.message.reply_text.call_args[0][0]


async def test_start_non_admin(mock_update, mock_context):
    mock_update.message.from_user.username = "non_admin_user"

    await start(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Access denied" in mock_update.message.reply_text.call_args[0][0]


async def test_start_clears_edit_state(mock_update, mock_context):
    """Running /start leaves a stuck regex / merge edit flow."""
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": "test_channel",
        "editing_feed_id": 7,
    }

    await start(mock_update, mock_context)

    assert "state" not in mock_context.user_data
    assert "editing_merge_time_for_channel" not in mock_context.user_data
    assert "editing_feed_id" not in mock_context.user_data


# --- /cancel ----------------------------------------------------------------


async def test_cancel_while_editing_clears_state(mock_update, mock_context):
    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": "test_channel",
        "editing_feed_id": 42,
    }

    await cancel(mock_update, mock_context)

    assert "state" not in mock_context.user_data
    assert "editing_regex_for_channel" not in mock_context.user_data
    assert "editing_feed_id" not in mock_context.user_data
    mock_update.message.reply_text.assert_called_once_with("Cancelled. No changes were made.")


async def test_cancel_with_no_state(mock_update, mock_context):
    await cancel(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once_with("Nothing to cancel.")


async def test_cancel_non_admin_leaves_state_untouched(mock_update, mock_context):
    mock_update.message.from_user.username = "not_admin"
    mock_context.user_data = {"state": "awaiting_regex"}

    await cancel(mock_update, mock_context)

    assert mock_context.user_data.get("state") == "awaiting_regex"
    mock_update.message.reply_text.assert_called_once_with("Access denied. Only admin can use this bot.")


async def test_list_clears_edit_state(mock_update, mock_context):
    """Running /list leaves a stuck regex / merge edit flow."""
    mock_context.user_data = {"state": "awaiting_regex", "editing_feed_id": 7}

    with patch("src.handlers.commands.get_channels_by_category", return_value={}):
        await list_channels(mock_update, mock_context)

    assert "state" not in mock_context.user_data
    assert "editing_feed_id" not in mock_context.user_data


# --- handle_message: forwards -----------------------------------------------


async def test_handle_message_forward_new_channel(mock_update, mock_context, mock_miniflux_client):
    """A forward from a channel with no existing feed asks for a category."""
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {
            "id": 67890,
            "title": "Test Channel",
            "username": "test_channel",
            "type": "channel",
        }
    }
    mock_miniflux_client.get_feeds.return_value = []

    with patch(
        "src.handlers.messages.fetch_categories",
        return_value=[{"id": 1, "title": "Category 1"}, {"id": 2, "title": "Category 2"}],
    ):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "category" in mock_update.message.reply_text.call_args[0][0].lower()
    assert mock_context.user_data["channel_title"] == "test_channel"
    assert mock_context.user_data["categories"] == {1: "Category 1", 2: "Category 2"}


async def test_handle_message_forward_existing_channel(mock_update, mock_context, mock_miniflux_client):
    """A forward from an already-subscribed channel shows the options keyboard."""
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": 1, "title": "T", "username": "test_channel", "type": "channel"}
    }
    existing_feed = {"id": 55, "feed_url": feed_url_for("test_channel", "?exclude_flags=fwd")}
    mock_miniflux_client.get_feeds.return_value = [existing_feed]
    mock_miniflux_client.get_feed.return_value = existing_feed

    await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    text, kwargs = mock_update.message.reply_text.call_args
    assert "already in subscriptions" in text[0]
    assert kwargs["reply_markup"] is not None


# --- handle_message: RSS URLs -----------------------------------------------


async def test_handle_message_direct_rss(mock_update, mock_context, mock_miniflux_client):
    """A direct RSS URL is offered for subscription with a category keyboard."""
    rss_url = "https://direct.example.com/feed.xml"
    mock_update.message.text = rss_url
    mock_update.message.to_dict.return_value = {"text": rss_url}
    mock_miniflux_client.get_feeds.return_value = []

    with patch("src.handlers.messages.is_valid_rss_url", return_value=(True, rss_url)) as mock_is_valid, \
         patch(
             "src.handlers.messages.fetch_categories",
             return_value=[{"id": 10, "title": "RSS Cat 1"}, {"id": 11, "title": "RSS Cat 2"}],
         ) as mock_fetch_cat:
        await handle_message(mock_update, mock_context)

    mock_is_valid.assert_called_once_with(rss_url)
    mock_fetch_cat.assert_called_once_with(mock_miniflux_client)
    mock_update.message.reply_text.assert_called_once()

    call_args, call_kwargs = mock_update.message.reply_text.call_args
    assert call_args[0].startswith("URL is a valid RSS feed. Select category:")
    assert "reply_markup" in call_kwargs
    assert mock_context.user_data["direct_rss_url"] == rss_url
    assert mock_context.user_data["categories"] == {10: "RSS Cat 1", 11: "RSS Cat 2"}


async def test_handle_message_direct_rss_already_subscribed(mock_update, mock_context, mock_miniflux_client):
    rss_url = "https://direct.example.com/feed.xml"
    mock_update.message.text = rss_url
    mock_update.message.to_dict.return_value = {"text": rss_url}
    mock_miniflux_client.get_feeds.return_value = [{"feed_url": rss_url}]

    with patch("src.handlers.messages.is_valid_rss_url", return_value=(True, rss_url)):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once_with(
        "This RSS feed is already in your subscriptions."
    )


async def test_handle_message_html_page(mock_update, mock_context):
    """An HTML page with several feeds offers a link-selection keyboard."""
    html_url = "https://blog.example.com/article"
    found_rss_links = [
        {"title": "Blog Feed", "href": "https://blog.example.com/feed.xml"},
        {"title": "Comments Feed", "href": "https://blog.example.com/comments/feed/"},
    ]
    mock_update.message.text = html_url
    mock_update.message.to_dict.return_value = {"text": html_url}

    with patch("src.handlers.messages.is_valid_rss_url", return_value=(False, found_rss_links)) as mock_is_valid:
        await handle_message(mock_update, mock_context)

    mock_is_valid.assert_called_once_with(html_url)
    mock_update.message.reply_text.assert_called_once()

    call_args, call_kwargs = mock_update.message.reply_text.call_args
    assert call_args[0].startswith("Found multiple RSS feeds on the webpage.")
    assert "reply_markup" in call_kwargs
    assert mock_context.user_data["rss_links"] == found_rss_links
    assert mock_context.user_data.get("categories") is None


async def test_handle_message_unknown(mock_update, mock_context):
    """A URL that is neither a feed nor a page with feeds gets a specific reply."""
    unknown_url = "https://example.com/not_a_feed"
    mock_update.message.text = unknown_url
    mock_update.message.to_dict.return_value = {"text": unknown_url}

    with patch("src.handlers.messages.is_valid_rss_url", return_value=(False, [])) as mock_is_valid:
        await handle_message(mock_update, mock_context)

    mock_is_valid.assert_called_once_with(unknown_url)
    mock_update.message.reply_text.assert_called_once()
    assert "does not appear to be a valid RSS feed" in mock_update.message.reply_text.call_args[0][0]


async def test_handle_message_non_admin(mock_update, mock_context):
    mock_update.message.from_user.username = "someone_else"

    await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once_with("Access denied. Only admin can use this bot.")


# --- /list ------------------------------------------------------------------


async def test_list_channels_success(mock_update, mock_context, mock_miniflux_client):
    """/list renders one plain-text message per category (no MarkdownV2)."""
    channel_data = {
        "Category A": [
            {"title": "channel_one", "flags": ["#noads", "#images"], "excluded_text": None, "merge_seconds": None},
            {"title": "channel_two", "flags": [], "excluded_text": "filter this", "merge_seconds": 300},
        ],
        "Category B": [
            {"title": "channel_three", "flags": [], "excluded_text": None, "merge_seconds": None}
        ],
    }

    with patch("src.handlers.commands.get_channels_by_category", return_value=channel_data) as mock_get_channels:
        await list_channels(mock_update, mock_context)

    mock_get_channels.assert_called_once_with(mock_miniflux_client, settings.rss_bridge_url)
    mock_update.message.chat.send_action.assert_called_once_with("typing")

    # 1 header + 1 message per category
    assert mock_update.message.reply_text.call_count == 3

    header_args, _ = mock_update.message.reply_text.call_args_list[0]
    assert header_args[0] == "Subscribed channels by category:"

    cat_a_args, cat_a_kwargs = mock_update.message.reply_text.call_args_list[1]
    assert "📁 Category A" in cat_a_args[0]
    assert "• channel_one, flags: #noads #images" in cat_a_args[0]
    assert "• channel_two, regex: filter this" in cat_a_args[0]
    # merge_seconds is rendered as a suffix after the regex
    assert "• channel_two, regex: filter this, merge: 300s" in cat_a_args[0]
    # The listing is plain text now: no MarkdownV2, so nothing needs escaping
    assert "parse_mode" not in cat_a_kwargs

    cat_b_args, cat_b_kwargs = mock_update.message.reply_text.call_args_list[2]
    assert "📁 Category B" in cat_b_args[0]
    assert "• channel_three" in cat_b_args[0]
    assert "parse_mode" not in cat_b_kwargs


async def test_list_channels_empty(mock_update, mock_context, mock_miniflux_client):
    with patch("src.handlers.commands.get_channels_by_category", return_value={}) as mock_get_channels:
        await list_channels(mock_update, mock_context)

    mock_get_channels.assert_called_once_with(mock_miniflux_client, settings.rss_bridge_url)
    mock_update.message.chat.send_action.assert_called_once_with("typing")
    mock_update.message.reply_text.assert_called_once_with(
        "No channels subscribed through RSS Bridge found."
    )


async def test_list_channels_non_admin(mock_update, mock_context):
    mock_update.message.from_user.username = "other_user"

    with patch("src.handlers.commands.get_channels_by_category") as mock_get_channels:
        await list_channels(mock_update, mock_context)

    mock_get_channels.assert_not_called()
    mock_update.message.chat.send_action.assert_not_called()
    mock_update.message.reply_text.assert_called_once_with("Access denied. Only admin can use this bot.")


async def test_list_channels_api_error(mock_update, mock_context):
    """An API failure is reported to the user instead of crashing the handler."""
    api_error = Exception("API connection failed")

    with patch("src.handlers.commands.get_channels_by_category", side_effect=api_error) as mock_get:
        await list_channels(mock_update, mock_context)

    mock_update.message.chat.send_action.assert_called_once_with("typing")
    mock_get.assert_called_once()
    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "Failed to list channels" in error_message
    assert str(api_error) in error_message


# --- Interactive /list: manage buttons --------------------------------------


async def test_list_channels_attaches_manage_buttons(mock_update, mock_context, mock_miniflux_client):
    """Each feed carrying a channel gets a manage button keyed by that channel."""
    channel_data = {
        "News": [
            {"title": "Chan One", "channel": "chan_one", "flags": [], "excluded_text": None, "merge_seconds": None},
            {"title": "Chan Two", "channel": "chan_two", "flags": [], "excluded_text": None, "merge_seconds": None},
        ],
    }

    with patch("src.handlers.commands.get_channels_by_category", return_value=channel_data):
        await list_channels(mock_update, mock_context)

    # Header + one category message
    assert mock_update.message.reply_text.call_count == 2
    _cat_args, cat_kwargs = mock_update.message.reply_text.call_args_list[1]
    reply_markup = cat_kwargs["reply_markup"]

    callbacks = [btn.callback_data for row in reply_markup.inline_keyboard for btn in row]
    assert callbacks == ["manage|chan_one", "manage|chan_two"]
    texts = [btn.text for row in reply_markup.inline_keyboard for btn in row]
    assert texts == ["⚙️ Chan One", "⚙️ Chan Two"]


async def test_manage_channel_callback_opens_options(mock_update, mock_context, mock_miniflux_client):
    """The manage callback resolves the feed and edits the message to the options view."""
    mock_update.callback_query.data = "manage|test_channel"
    existing_feed = {"id": 77, "feed_url": feed_url_for("test_channel", "?exclude_flags=fwd")}
    mock_miniflux_client.get_feeds.return_value = [existing_feed]
    mock_miniflux_client.get_feed.return_value = existing_feed

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_once()
    args, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert "Options for @test_channel" in args[0]
    assert kwargs["reply_markup"] is not None


async def test_manage_channel_callback_not_found(mock_update, mock_context, mock_miniflux_client):
    """A manage button for an unsubscribed channel reports it as not found."""
    mock_update.callback_query.data = "manage|ghost"
    mock_miniflux_client.get_feeds.return_value = []

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "not found in subscriptions" in mock_update.callback_query.edit_message_text.call_args[0][0]


# --- Flag toggling ----------------------------------------------------------


async def test_handle_flag_toggle_add(mock_query, mock_context, mock_miniflux_client):
    """Adding a flag rewrites the feed URL and reports the new flag list."""
    channel_name = "channel_add_flag"
    flag = "video"
    feed_id = 110
    original_url = feed_url_for(channel_name)
    mock_miniflux_client.get_feeds.return_value = [{"id": feed_id, "feed_url": original_url}]

    with patch("src.handlers.callbacks.update_feed_url", return_value=(True, "url", None)) as mock_update_api:
        await _handle_flag_toggle(mock_query, mock_context, "add", flag, channel_name)

    expected_url = feed_url_for(channel_name, f"?exclude_flags={flag}")
    mock_update_api.assert_called_once_with(feed_id, expected_url, mock_miniflux_client)

    mock_query.edit_message_text.assert_called_once()
    text = mock_query.edit_message_text.call_args[0][0]
    assert f"Flag '{flag}' added for channel @{channel_name}" in text
    assert f"Current flags: {flag}" in text
    assert "Choose an action:" in text


async def test_handle_flag_toggle_remove(mock_query, mock_context, mock_miniflux_client):
    """Removing the last flag drops the exclude_flags parameter entirely."""
    channel_name = "channel_remove_flag"
    flag = "video"
    feed_id = 111
    original_url = feed_url_for(channel_name, f"?exclude_flags={flag}")
    mock_miniflux_client.get_feeds.return_value = [{"id": feed_id, "feed_url": original_url}]

    with patch("src.handlers.callbacks.update_feed_url", return_value=(True, "url", None)) as mock_update_api:
        await _handle_flag_toggle(mock_query, mock_context, "remove", flag, channel_name)

    mock_update_api.assert_called_once_with(feed_id, feed_url_for(channel_name), mock_miniflux_client)

    mock_query.edit_message_text.assert_called_once()
    text = mock_query.edit_message_text.call_args[0][0]
    assert f"Flag '{flag}' removed for channel @{channel_name}" in text
    assert "Current flags: none" in text


async def test_handle_flag_toggle_channel_not_found(mock_query, mock_context, mock_miniflux_client):
    mock_miniflux_client.get_feeds.return_value = []

    await _handle_flag_toggle(mock_query, mock_context, "add", "video", "ghost_channel")

    mock_query.edit_message_text.assert_called_once()
    assert "not found in subscriptions" in mock_query.edit_message_text.call_args[0][0]


async def test_handle_flag_toggle_already_set(mock_query, mock_context, mock_miniflux_client):
    """Adding a flag that is already set is a no-op with an explanatory message."""
    channel_name = "chan"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 1, "feed_url": feed_url_for(channel_name, "?exclude_flags=video")}
    ]

    with patch("src.handlers.callbacks.update_feed_url") as mock_update_api:
        await _handle_flag_toggle(mock_query, mock_context, "add", "video", channel_name)

    mock_update_api.assert_not_called()
    assert "is already set" in mock_query.edit_message_text.call_args[0][0]


async def test_handle_flag_toggle_not_set_on_remove(mock_query, mock_context, mock_miniflux_client):
    channel_name = "chan"
    mock_miniflux_client.get_feeds.return_value = [{"id": 1, "feed_url": feed_url_for(channel_name)}]

    with patch("src.handlers.callbacks.update_feed_url") as mock_update_api:
        await _handle_flag_toggle(mock_query, mock_context, "remove", "video", channel_name)

    mock_update_api.assert_not_called()
    assert "is not set" in mock_query.edit_message_text.call_args[0][0]


async def test_handle_flag_toggle_update_error(mock_query, mock_context, mock_miniflux_client):
    """A failed URL update is reported, with the keyboard still offered."""
    channel_name = "test_channel"
    mock_miniflux_client.get_feeds.return_value = [{"id": 123, "feed_url": feed_url_for(channel_name)}]

    with patch("src.handlers.callbacks.update_feed_url", return_value=(False, None, "Update failed")):
        await _handle_flag_toggle(mock_query, mock_context, "add", "video", channel_name)

    mock_query.edit_message_text.assert_called_once()
    text, kwargs = mock_query.edit_message_text.call_args
    assert "Failed to update flags for @test_channel" in text[0]
    assert "Update failed" in text[0]
    assert kwargs["reply_markup"] is not None


async def test_handle_flag_toggle_get_feeds_error(mock_query, mock_context, mock_miniflux_client):
    """A Miniflux failure while resolving the feed is reported to the user."""
    mock_miniflux_client.get_feeds.side_effect = Exception("Failed to fetch feed data")

    await _handle_flag_toggle(mock_query, mock_context, "add", "video", "test_channel")

    mock_query.edit_message_text.assert_called_once()
    text = mock_query.edit_message_text.call_args[0][0]
    assert "Failed to process flag action" in text
    assert "Failed to fetch feed data" in text


async def test_button_callback_flag_toggle_routing(mock_update, mock_context, mock_miniflux_client):
    """button_callback parses 'add_flag|<channel>|<flag>' and dispatches correctly."""
    mock_update.callback_query.data = "add_flag|test_channel|video"

    with patch("src.handlers.callbacks._handle_flag_toggle", new=AsyncMock()) as mock_toggle:
        await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_toggle.assert_called_once_with(
        mock_update.callback_query, mock_context, "add", "video", "test_channel"
    )


# --- Regex editing ----------------------------------------------------------


async def test_button_callback_edit_regex_request(mock_update, mock_context, mock_miniflux_client):
    """The 'Edit Regex' button prompts for input and switches to awaiting_regex."""
    channel_name = "channel_for_regex"
    feed_id = 101
    mock_update.callback_query.data = f"edit_regex|{channel_name}"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": feed_id, "feed_url": feed_url_for(channel_name, "?exclude_text=old_regex")}
    ]

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_miniflux_client.get_feeds.assert_called_once()

    mock_update.callback_query.edit_message_text.assert_called_once()
    text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert f"Current regex for @{channel_name}" in text
    assert "old_regex" in text
    assert "Please send the new regex" in text

    assert mock_context.user_data["state"] == "awaiting_regex"
    assert mock_context.user_data["editing_regex_for_channel"] == channel_name
    assert mock_context.user_data["editing_feed_id"] == feed_id


async def test_button_callback_edit_regex_no_current_regex(mock_update, mock_context, mock_miniflux_client):
    channel_name = "chan"
    mock_update.callback_query.data = f"edit_regex|{channel_name}"
    mock_miniflux_client.get_feeds.return_value = [{"id": 1, "feed_url": feed_url_for(channel_name)}]

    await button_callback(mock_update, mock_context)

    text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert f"No current regex set for @{channel_name}" in text


async def test_button_callback_edit_regex_channel_missing(mock_update, mock_context, mock_miniflux_client):
    mock_update.callback_query.data = "edit_regex|ghost"
    mock_miniflux_client.get_feeds.return_value = []

    await button_callback(mock_update, mock_context)

    text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "not found in subscriptions" in text
    assert "state" not in mock_context.user_data


async def test_button_callback_edit_regex_get_feeds_error(mock_update, mock_context, mock_miniflux_client):
    """A failure while preparing the edit clears any half-set state."""
    mock_update.callback_query.data = "edit_regex|test_channel"
    mock_miniflux_client.get_feeds.side_effect = Exception("Failed to fetch feed data")

    await button_callback(mock_update, mock_context)

    text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Failed to start regex edit" in text
    assert "state" not in mock_context.user_data


async def test_handle_message_awaiting_regex_update(mock_update, mock_context, mock_miniflux_client):
    """Sending a regex in awaiting_regex state updates the feed and clears the state."""
    channel_name = "channel_for_regex"
    feed_id = 101
    new_regex = "(keep|this|pattern)"
    original_url = feed_url_for(channel_name, "?exclude_flags=fwd")
    expected_new_url = feed_url_for(
        channel_name, f"?exclude_flags=fwd&exclude_text={urllib.parse.quote(new_regex)}"
    )

    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": channel_name,
        "editing_feed_id": feed_id,
    }
    mock_update.message.text = new_regex
    mock_miniflux_client.get_feed.side_effect = [
        {"id": feed_id, "feed_url": original_url},
        {"id": feed_id, "feed_url": expected_new_url},
    ]

    with patch(
        "src.handlers.messages.update_feed_url", return_value=(True, expected_new_url, None)
    ) as mock_update_api:
        await handle_message(mock_update, mock_context)

    # Once to read the current URL, once to rebuild the keyboard afterwards
    assert mock_miniflux_client.get_feed.call_count == 2
    mock_update_api.assert_called_once_with(feed_id, expected_new_url, mock_miniflux_client)

    assert mock_update.message.reply_text.call_count == 2
    confirmation = mock_update.message.reply_text.call_args_list[0][0][0]
    assert f"Regex for channel @{channel_name} updated to: {new_regex}" in confirmation
    keyboard_message = mock_update.message.reply_text.call_args_list[1][0][0]
    assert f"Updated options for @{channel_name}" in keyboard_message

    assert "state" not in mock_context.user_data


async def test_handle_message_awaiting_regex_remove(mock_update, mock_context, mock_miniflux_client):
    """Sending '-' removes the regex but keeps the other feed parameters."""
    channel_name = "channel_to_clear_regex"
    feed_id = 102
    original_url = feed_url_for(channel_name, "?exclude_text=%28old%7Cfilter%29&merge_seconds=600")
    expected_new_url = feed_url_for(channel_name, "?merge_seconds=600")

    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": channel_name,
        "editing_feed_id": feed_id,
    }
    mock_update.message.text = "-"
    mock_miniflux_client.get_feed.side_effect = [
        {"id": feed_id, "feed_url": original_url},
        {"id": feed_id, "feed_url": expected_new_url},
    ]

    with patch(
        "src.handlers.messages.update_feed_url", return_value=(True, expected_new_url, None)
    ) as mock_update_api:
        await handle_message(mock_update, mock_context)

    # The merge time survives the regex removal
    mock_update_api.assert_called_once_with(feed_id, expected_new_url, mock_miniflux_client)

    assert mock_update.message.reply_text.call_count == 2
    assert f"Regex filter removed for channel @{channel_name}" in (
        mock_update.message.reply_text.call_args_list[0][0][0]
    )
    assert "state" not in mock_context.user_data


async def test_handle_awaiting_regex_missing_context(mock_update, mock_context):
    """The state without its companion keys is a bug: say so and clear the state."""
    mock_context.user_data = {"state": "awaiting_regex"}
    mock_update.message.text = "regex"

    await _handle_awaiting_regex(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Error: Missing context for regex update" in mock_update.message.reply_text.call_args[0][0]
    assert "state" not in mock_context.user_data


async def test_handle_awaiting_regex_get_feed_error(mock_update, mock_context, mock_miniflux_client):
    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "exclude this text"
    mock_miniflux_client.get_feed.side_effect = Exception("Failed to fetch feed data")

    await _handle_awaiting_regex(mock_update, mock_context)

    mock_miniflux_client.get_feed.assert_called_once_with(123)
    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "An unexpected error occurred" in error_message
    assert "Failed to fetch feed data" in error_message


async def test_handle_awaiting_regex_update_error(mock_update, mock_context, mock_miniflux_client):
    channel_name = "test_channel"
    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": channel_name,
        "editing_feed_id": 123,
    }
    mock_update.message.text = "exclude this text"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for(channel_name)}

    with patch("src.handlers.messages.update_feed_url", return_value=(False, None, "Update failed")):
        await _handle_awaiting_regex(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "Failed to update regex for channel" in error_message
    assert "Update failed" in error_message
    assert "state" not in mock_context.user_data


# --- Merge time editing -----------------------------------------------------


async def test_button_callback_edit_merge_time_request(mock_update, mock_context, mock_miniflux_client):
    channel_name = "chan"
    mock_update.callback_query.data = f"edit_merge_time|{channel_name}"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 9, "feed_url": feed_url_for(channel_name, "?merge_seconds=300")}
    ]

    await button_callback(mock_update, mock_context)

    text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Current merge time: 300 seconds" in text
    assert mock_context.user_data["state"] == "awaiting_merge_time"
    assert mock_context.user_data["editing_merge_time_for_channel"] == channel_name
    assert mock_context.user_data["editing_feed_id"] == 9


async def test_handle_awaiting_merge_time_success(mock_update, mock_context, mock_miniflux_client):
    channel_name = "chan"
    feed_id = 7
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": channel_name,
        "editing_feed_id": feed_id,
    }
    mock_update.message.text = "300"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for(channel_name)}

    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)) as mock_update_api:
        await _handle_awaiting_merge_time(mock_update, mock_context)

    mock_update_api.assert_called_once_with(
        feed_id, feed_url_for(channel_name, "?merge_seconds=300"), mock_miniflux_client
    )
    assert "updated to: 300 seconds" in mock_update.message.reply_text.call_args_list[0][0][0]
    assert "state" not in mock_context.user_data


async def test_handle_awaiting_merge_time_remove(mock_update, mock_context, mock_miniflux_client):
    """0 disables merging: the merge_seconds parameter is dropped."""
    channel_name = "chan"
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": channel_name,
        "editing_feed_id": 7,
    }
    mock_update.message.text = "0"
    mock_miniflux_client.get_feed.return_value = {
        "feed_url": feed_url_for(channel_name, "?merge_seconds=600")
    }

    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)) as mock_update_api:
        await _handle_awaiting_merge_time(mock_update, mock_context)

    mock_update_api.assert_called_once_with(7, feed_url_for(channel_name), mock_miniflux_client)
    assert "Merge time filter removed" in mock_update.message.reply_text.call_args_list[0][0][0]


async def test_handle_awaiting_merge_time_invalid(mock_update, mock_context, mock_miniflux_client):
    """Invalid input keeps the state so the next message is still read as a merge time."""
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": "channel_name",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "invalid_input"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for("channel_name")}

    await _handle_awaiting_merge_time(mock_update, mock_context)

    # The retry state survives: the user can simply send a number now
    assert mock_context.user_data["state"] == "awaiting_merge_time"
    assert mock_context.user_data["editing_merge_time_for_channel"] == "channel_name"
    assert mock_context.user_data["editing_feed_id"] == 123

    mock_update.message.reply_text.assert_any_call(
        "Invalid input. Please send a number for merge time (seconds), or 0 to disable."
    )
    # The error plus the options keyboard
    assert mock_update.message.reply_text.call_count == 2


async def test_handle_awaiting_merge_time_negative_value(mock_update, mock_context, mock_miniflux_client):
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "-10"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for("test_channel")}

    await _handle_awaiting_merge_time(mock_update, mock_context)

    assert mock_context.user_data["state"] == "awaiting_merge_time"
    error_messages = [
        args[0][0]
        for args in mock_update.message.reply_text.call_args_list
        if "non-negative" in args[0][0]
    ]
    assert error_messages


async def test_handle_awaiting_merge_time_missing_context(mock_update, mock_context):
    mock_context.user_data = {"state": "awaiting_merge_time"}
    mock_update.message.text = "300"

    await _handle_awaiting_merge_time(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Error: Missing context for merge time update" in mock_update.message.reply_text.call_args[0][0]
    assert "state" not in mock_context.user_data


async def test_handle_awaiting_merge_time_update_error(mock_update, mock_context, mock_miniflux_client):
    channel_name = "test_channel"
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": channel_name,
        "editing_feed_id": 123,
    }
    mock_update.message.text = "300"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for(channel_name)}

    with patch("src.handlers.messages.update_feed_url", return_value=(False, None, "Update failed")):
        await _handle_awaiting_merge_time(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "Failed to update merge time" in error_message
    assert "Update failed" in error_message
    assert "state" not in mock_context.user_data


async def test_handle_awaiting_merge_time_too_large(mock_update, mock_context, mock_miniflux_client):
    """A number the bridge rejects is still a number: the API error is surfaced."""
    channel_name = "test_channel"
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": channel_name,
        "editing_feed_id": 123,
    }
    mock_update.message.text = "100000000"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for(channel_name)}

    with patch(
        "src.handlers.messages.update_feed_url",
        return_value=(False, None, "Merge time value too large"),
    ):
        await _handle_awaiting_merge_time(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "Failed to update merge time" in error_message
    assert "Merge time value too large" in error_message


async def test_handle_awaiting_merge_time_get_feed_error(mock_update, mock_context, mock_miniflux_client):
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "300"
    mock_miniflux_client.get_feed.side_effect = Exception("Failed to fetch feed data")

    await _handle_awaiting_merge_time(mock_update, mock_context)

    mock_miniflux_client.get_feed.assert_called_once_with(123)
    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "An unexpected error occurred" in error_message
    assert "Failed to fetch feed data" in error_message


# --- Media groups -----------------------------------------------------------


async def test_handle_message_media_group_skipping(mock_update, mock_context):
    """Only the first message of a media group is processed."""
    media_group_id = "test_media_group_123"
    mock_update.message.media_group_id = media_group_id
    mock_context.user_data["processed_media_group_id"] = media_group_id

    with patch("src.handlers.messages._parse_message_content", new=AsyncMock()) as mock_parse:
        await handle_message(mock_update, mock_context)

    mock_parse.assert_not_called()
    mock_update.message.reply_text.assert_not_called()
    mock_update.message.chat.send_action.assert_not_called()


async def test_handle_message_media_group_different_groups(mock_update, mock_context, mock_miniflux_client):
    """A message from a new media group is processed and becomes the new marker."""
    mock_update.message.media_group_id = "media_group_1"
    mock_context.user_data["processed_media_group_id"] = "previous_media_group"
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": 1, "title": "T", "username": "chan", "type": "channel"}
    }
    mock_miniflux_client.get_feeds.return_value = []

    with patch("src.handlers.messages.fetch_categories", return_value=[{"id": 1, "title": "News"}]):
        await handle_message(mock_update, mock_context)

    assert mock_context.user_data["processed_media_group_id"] == "media_group_1"
    mock_update.message.reply_text.assert_called_once()


# --- _handle_telegram_channel error paths -----------------------------------


async def test_handle_telegram_channel_fetch_categories_error(mock_update, mock_context, mock_miniflux_client):
    mock_miniflux_client.get_feeds.return_value = []

    with patch("src.handlers.messages.fetch_categories", side_effect=Exception("boom")):
        await _handle_telegram_channel(mock_update, mock_context, "new_channel_test", "forward")

    mock_miniflux_client.get_feeds.assert_called_once()
    mock_update.message.reply_text.assert_called_with("Failed to fetch categories from RSS reader.")


async def test_handle_telegram_channel_get_feeds_error(mock_update, mock_context, mock_miniflux_client):
    mock_miniflux_client.get_feeds.side_effect = Exception("Failed to get feeds")

    await _handle_telegram_channel(mock_update, mock_context, "existing_channel_test", "forward")

    mock_miniflux_client.get_feeds.assert_called_once()
    mock_update.message.reply_text.assert_called_with("Failed to check existing subscriptions.")


# --- Delete channel ---------------------------------------------------------


async def test_button_callback_delete_feed_success(mock_update, mock_context, mock_miniflux_client):
    """Deleting a channel calls the SYNC client.delete_feed and confirms success."""
    channel_name = "channel_to_delete"
    feed_id = 456
    mock_update.callback_query.data = f"delete|{channel_name}"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": feed_id, "feed_url": feed_url_for(channel_name)}
    ]

    await button_callback(mock_update, mock_context)

    mock_miniflux_client.get_feeds.assert_called_once()
    mock_miniflux_client.delete_feed.assert_called_once_with(feed_id)

    mock_update.callback_query.edit_message_text.assert_called_once()
    message = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert f"Channel @{channel_name} has been deleted from subscriptions." in message


async def test_button_callback_delete_feed_error(mock_update, mock_context, mock_miniflux_client):
    channel_name = "channel_to_delete"
    mock_update.callback_query.data = f"delete|{channel_name}"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 456, "feed_url": feed_url_for(channel_name)}
    ]
    mock_miniflux_client.delete_feed.side_effect = Exception("Failed to delete feed")

    await button_callback(mock_update, mock_context)

    mock_miniflux_client.delete_feed.assert_called_once_with(456)
    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "Failed to delete channel" in mock_update.callback_query.edit_message_text.call_args[0][0]


async def test_button_callback_delete_channel_not_found(mock_update, mock_context, mock_miniflux_client):
    mock_update.callback_query.data = "delete|ghost"
    mock_miniflux_client.get_feeds.return_value = []

    await button_callback(mock_update, mock_context)

    mock_miniflux_client.delete_feed.assert_not_called()
    assert "not found in subscriptions" in mock_update.callback_query.edit_message_text.call_args[0][0]


# --- Category selection -----------------------------------------------------


async def test_button_callback_select_category(mock_update, mock_context, mock_miniflux_client):
    """Choosing a category subscribes the pending Telegram channel."""
    mock_update.callback_query.data = "cat_1"
    mock_context.user_data = {
        "channel_title": "test_channel",
        "categories": {1: "Category 1", 2: "Category 2"},
    }

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_miniflux_client.create_feed.assert_called_once_with(
        feed_url_for("test_channel"), category_id=1
    )
    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "subscribed" in mock_update.callback_query.edit_message_text.call_args[0][0].lower()
    assert "channel_title" not in mock_context.user_data


async def test_button_callback_category_selection_with_direct_rss(mock_update, mock_context, mock_miniflux_client):
    """Choosing a category subscribes the pending direct RSS feed."""
    direct_rss_url = "https://example.com/feed.xml"
    mock_update.callback_query.data = "cat_42"
    mock_context.user_data = {
        "direct_rss_url": direct_rss_url,
        "categories": {42: "News Category"},
    }

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.message.chat.send_action.assert_called_once_with("typing")
    mock_miniflux_client.create_feed.assert_called_once_with(direct_rss_url, category_id=42)

    message_text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Direct RSS feed" in message_text
    assert "News Category" in message_text
    assert "direct_rss_url" not in mock_context.user_data


async def test_button_callback_category_selection_api_error(mock_update, mock_context, mock_miniflux_client):
    """A Miniflux error while subscribing is reported with its status and reason."""
    mock_update.callback_query.data = "cat_24"
    mock_context.user_data = {
        "channel_title": "error_channel",
        "categories": {24: "Telegram Channels"},
    }

    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.json.return_value = {"error_message": "Feed already exists"}
    mock_miniflux_client.create_feed.side_effect = ClientError(mock_response)

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.message.chat.send_action.assert_called_once_with("typing")
    error_message = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Failed to subscribe" in error_message
    assert "400" in error_message
    assert "Feed already exists" in error_message


async def test_button_callback_category_missing_channel_info(mock_update, mock_context, mock_miniflux_client):
    mock_update.callback_query.data = "cat_1"
    mock_context.user_data = {"categories": {1: "Category 1"}}

    await button_callback(mock_update, mock_context)

    mock_miniflux_client.create_feed.assert_not_called()
    assert "Channel information is missing." in (
        mock_update.callback_query.edit_message_text.call_args[0][0]
    )


async def test_button_callback_invalid_cat_id(mock_update, mock_context):
    mock_update.callback_query.data = "cat_invalid"

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_once_with(
        "Invalid category ID.", reply_markup=None
    )


# --- Unknown callback data --------------------------------------------------


async def test_button_callback_unknown_data(mock_update, mock_context):
    mock_update.callback_query.data = "unknown_action_format"

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once_with(
        "Unknown action.", reply_markup=None
    )


# --- Misc input -------------------------------------------------------------


async def test_unexpected_sticker_input(mock_update, mock_context):
    """A sticker (no text, no forward) gets the generic help message."""
    mock_update.message.text = None
    mock_update.message.to_dict.return_value = {"sticker": {"file_id": "abc"}}

    await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "forward" in mock_update.message.reply_text.call_args[0][0].lower()


async def test_handle_message_without_message_is_ignored(mock_context):
    """An update with no message (e.g. an edited-message event) is a no-op."""
    update = MagicMock()
    update.message = None

    await handle_message(update, mock_context)  # must not raise

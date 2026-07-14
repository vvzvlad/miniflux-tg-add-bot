"""State transitions: entering and leaving the awaiting_regex / awaiting_merge_time flows."""

from unittest.mock import patch

import pytest

from src.handlers.callbacks import button_callback
from src.handlers.messages import handle_message

BRIDGE_URL = "http://test.rssbridge.local/rss/{channel}/test_token"


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


def feed_url_for(channel: str, query: str = "") -> str:
    return BRIDGE_URL.replace("{channel}", channel) + query


# --- awaiting_regex ---------------------------------------------------------


async def test_awaiting_regex_state_transition(mock_update, mock_context, mock_miniflux_client):
    """The edit_regex button switches the conversation into awaiting_regex."""
    mock_update.callback_query.data = "edit_regex|test_channel"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 123, "feed_url": feed_url_for("test_channel")}
    ]

    await button_callback(mock_update, mock_context)

    assert mock_context.user_data["state"] == "awaiting_regex"
    assert mock_context.user_data["editing_regex_for_channel"] == "test_channel"
    assert mock_context.user_data["editing_feed_id"] == 123

    mock_update.callback_query.edit_message_text.assert_called_once()
    text = mock_update.callback_query.edit_message_text.call_args[0][0].lower()
    assert "regex" in text
    assert "send" in text


async def test_awaiting_regex_state_processing(mock_update, mock_context, mock_miniflux_client):
    """Sending a regex updates the feed and clears the state."""
    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "new_regex_pattern"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for("test_channel")}

    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)):
        await handle_message(mock_update, mock_context)

    assert "state" not in mock_context.user_data
    assert "editing_regex_for_channel" not in mock_context.user_data
    assert "editing_feed_id" not in mock_context.user_data

    messages = [call[0][0] for call in mock_update.message.reply_text.call_args_list]
    assert any("updated to" in msg and "new_regex_pattern" in msg for msg in messages)


async def test_awaiting_regex_remove_pattern(mock_update, mock_context, mock_miniflux_client):
    """Sending '-' removes the regex filter."""
    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "-"
    mock_miniflux_client.get_feed.return_value = {
        "feed_url": feed_url_for("test_channel", "?exclude_text=old_regex")
    }

    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)):
        await handle_message(mock_update, mock_context)

    messages = [call[0][0] for call in mock_update.message.reply_text.call_args_list]
    assert any("Regex filter removed" in msg for msg in messages)


# --- awaiting_merge_time ----------------------------------------------------


async def test_awaiting_merge_time_state_transition(mock_update, mock_context, mock_miniflux_client):
    mock_update.callback_query.data = "edit_merge_time|test_channel"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 123, "feed_url": feed_url_for("test_channel", "?merge_seconds=3600")}
    ]

    await button_callback(mock_update, mock_context)

    assert mock_context.user_data["state"] == "awaiting_merge_time"
    assert mock_context.user_data["editing_merge_time_for_channel"] == "test_channel"
    assert mock_context.user_data["editing_feed_id"] == 123

    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "merge time" in mock_update.callback_query.edit_message_text.call_args[0][0].lower()


async def test_awaiting_merge_time_state_processing(mock_update, mock_context, mock_miniflux_client):
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "7200"
    mock_miniflux_client.get_feed.return_value = {
        "feed_url": feed_url_for("test_channel", "?merge_seconds=3600")
    }

    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)):
        await handle_message(mock_update, mock_context)

    assert "state" not in mock_context.user_data
    messages = [call[0][0] for call in mock_update.message.reply_text.call_args_list]
    assert any("7200" in msg and "updated" in msg.lower() for msg in messages)


async def test_awaiting_merge_time_remove_setting(mock_update, mock_context, mock_miniflux_client):
    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_update.message.text = "0"
    mock_miniflux_client.get_feed.return_value = {
        "feed_url": feed_url_for("test_channel", "?merge_seconds=3600")
    }

    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)):
        await handle_message(mock_update, mock_context)

    messages = [call[0][0] for call in mock_update.message.reply_text.call_args_list]
    assert any("Merge time" in msg and "removed" in msg.lower() for msg in messages)


# --- Flag toggle dispatch ---------------------------------------------------


async def test_flag_toggle_dispatched_from_callback(mock_update, mock_context, mock_miniflux_client):
    """A flag button reaches _handle_flag_toggle with the parsed action/flag/channel."""
    from unittest.mock import AsyncMock

    mock_update.callback_query.data = "add_flag|test_channel|video"

    with patch("src.handlers.callbacks._handle_flag_toggle", new=AsyncMock()) as mock_toggle:
        await button_callback(mock_update, mock_context)

    mock_toggle.assert_called_once_with(
        mock_update.callback_query, mock_context, "add", "video", "test_channel"
    )


# --- A full conversation ----------------------------------------------------


async def test_complex_state_sequence(mock_update, mock_context, mock_miniflux_client):
    """Forward -> pick category -> edit regex -> send regex, end to end."""
    # 1. Forward a new channel
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": 12345, "username": "test_channel", "title": "T", "type": "channel"}
    }
    mock_miniflux_client.get_feeds.return_value = []

    with patch(
        "src.handlers.messages.fetch_categories",
        return_value=[{"id": 1, "title": "News"}, {"id": 2, "title": "Tech"}],
    ):
        await handle_message(mock_update, mock_context)

    assert mock_context.user_data["channel_title"] == "test_channel"
    assert len(mock_context.user_data["categories"]) == 2

    # 2. Select a category
    mock_update.callback_query.data = "cat_1"
    await button_callback(mock_update, mock_context)
    assert "channel_title" not in mock_context.user_data
    mock_miniflux_client.create_feed.assert_called_once()

    # 3. Ask to edit the regex
    mock_update.callback_query.data = "edit_regex|test_channel"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 123, "feed_url": feed_url_for("test_channel")}
    ]
    await button_callback(mock_update, mock_context)
    assert mock_context.user_data["state"] == "awaiting_regex"
    assert mock_context.user_data["editing_feed_id"] == 123

    # 4. Send the regex
    mock_update.message.text = "spam|ads"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for("test_channel")}
    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)):
        await handle_message(mock_update, mock_context)

    assert "state" not in mock_context.user_data
    messages = [call[0][0] for call in mock_update.message.reply_text.call_args_list]
    assert any("spam|ads" in msg for msg in messages)

"""End-to-end flows across handlers, driven only through the public entry points."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from miniflux import ClientError

from src.bot import build_application
from src.handlers.callbacks import button_callback
from src.handlers.commands import list_channels
from src.handlers.messages import ParsedMessage, handle_message

BRIDGE_URL = "http://test.rssbridge.local/rss/{channel}/test_token"


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


def feed_url_for(channel: str, query: str = "") -> str:
    return BRIDGE_URL.replace("{channel}", channel) + query


# --- Add a channel, then configure a regex filter ---------------------------


async def test_channel_regex_filter_flow(mock_update, mock_context, mock_miniflux_client):
    """Forward a channel, choose a category, then set a regex — one continuous flow."""
    # Phase 1: forward a channel
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": 12345, "title": "Test Channel", "username": "test_channel", "type": "channel"}
    }
    mock_miniflux_client.get_feeds.return_value = []

    with patch(
        "src.handlers.messages.fetch_categories",
        return_value=[{"id": 1, "title": "News"}, {"id": 2, "title": "Tech"}],
    ):
        await handle_message(mock_update, mock_context)

    assert "select category" in mock_update.message.reply_text.call_args[0][0].lower()
    mock_update.message.reply_text.reset_mock()

    # Phase 2: select a category
    mock_update.callback_query.data = "cat_1"
    await button_callback(mock_update, mock_context)

    mock_miniflux_client.create_feed.assert_called_once()
    assert "subscribed" in mock_update.callback_query.edit_message_text.call_args[0][0].lower()
    mock_update.callback_query.edit_message_text.reset_mock()

    # Phase 3: ask to edit the regex
    mock_update.callback_query.data = "edit_regex|test_channel"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 42, "title": "Test Channel", "feed_url": feed_url_for("test_channel")}
    ]

    await button_callback(mock_update, mock_context)

    assert mock_context.user_data["state"] == "awaiting_regex"
    assert mock_context.user_data["editing_feed_id"] == 42
    assert "send the new regex" in mock_update.callback_query.edit_message_text.call_args[0][0].lower()
    mock_update.callback_query.edit_message_text.reset_mock()

    # Phase 4: send the regex
    mock_update.message.text = "unwanted|spam"
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for("test_channel")}

    with patch("src.handlers.messages.update_feed_url", return_value=(True, "url", None)) as mock_update_url:
        await handle_message(mock_update, mock_context)

    assert "state" not in mock_context.user_data
    mock_update_url.assert_called_once()
    success_messages = [
        call[0][0]
        for call in mock_update.message.reply_text.call_args_list
        if "regex" in call[0][0].lower() and "updated" in call[0][0].lower()
    ]
    assert success_messages


# --- List, then delete a feed ------------------------------------------------


async def test_listing_deleting_flow(mock_update, mock_context, mock_miniflux_client):
    with patch(
        "src.handlers.commands.get_channels_by_category",
        return_value={"News": [{"title": "channel_one", "flags": [], "excluded_text": None, "merge_seconds": None}]},
    ):
        await list_channels(mock_update, mock_context)

    all_text = " ".join(call[0][0] for call in mock_update.message.reply_text.call_args_list)
    assert "subscribed channels" in all_text.lower()
    assert "channel_one" in all_text

    # Delete channel_one
    mock_update.callback_query.data = "delete|channel_one"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 101, "title": "Channel One", "feed_url": feed_url_for("channel_one")}
    ]

    await button_callback(mock_update, mock_context)

    # The synchronous client method is called with the resolved feed id
    mock_miniflux_client.delete_feed.assert_called_once_with(101)
    assert "deleted" in mock_update.callback_query.edit_message_text.call_args[0][0].lower()


# --- Recovery from a transient Miniflux error --------------------------------


async def test_error_recovery_miniflux_api(mock_update, mock_context, mock_miniflux_client):
    """A failed attempt shows an error; the very next attempt can still succeed."""
    mock_update.message.text = "https://t.me/channel_name"

    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.json.return_value = {"error_message": "Connection timeout"}
    mock_miniflux_client.get_feeds.side_effect = [ClientError(mock_response), []]

    with patch(
        "src.handlers.messages._parse_message_content",
        new=AsyncMock(return_value=ParsedMessage(channel_username="channel_name", channel_source_type="link_or_username")),
    ), patch("src.handlers.messages.fetch_categories", return_value=[{"id": 1, "title": "News"}]):
        # First attempt fails during the subscription check
        await handle_message(mock_update, mock_context)
        error_messages = [
            call[0][0]
            for call in mock_update.message.reply_text.call_args_list
            if "failed" in call[0][0].lower() or "error" in call[0][0].lower()
        ]
        assert error_messages
        mock_update.message.reply_text.reset_mock()

        # Second attempt succeeds and asks for a category
        await handle_message(mock_update, mock_context)
        assert "select category" in mock_update.message.reply_text.call_args[0][0].lower()


# --- Multiple concurrent updates --------------------------------------------


async def test_multiple_simultaneous_interactions(mock_context, mock_miniflux_client):
    """Two updates handled concurrently both get a reply."""
    def make_update(username):
        update = MagicMock()
        update.message = MagicMock()
        update.message.from_user = MagicMock(username="test_admin")
        update.message.text = f"https://t.me/{username}"
        update.message.media_group_id = None
        update.message.to_dict = MagicMock(return_value={"text": f"https://t.me/{username}"})
        update.message.reply_text = AsyncMock()
        update.message.chat = MagicMock()
        update.message.chat.send_action = AsyncMock()
        return update

    update1 = make_update("channel_one")
    update2 = make_update("channel_two")
    mock_miniflux_client.get_feeds.return_value = []

    with patch("src.handlers.messages.fetch_categories", return_value=[{"id": 1, "title": "News"}]):
        await asyncio.gather(
            handle_message(update1, mock_context),
            handle_message(update2, mock_context),
        )

    update1.message.reply_text.assert_called()
    update2.message.reply_text.assert_called()


# --- build_application ------------------------------------------------------


def test_build_application_registers_handlers():
    """build_application wires the token and registers all handlers plus the error handler."""
    with patch("src.bot.ApplicationBuilder") as mock_builder:
        mock_app = MagicMock()
        mock_builder.return_value.token.return_value.post_init.return_value.build.return_value = mock_app

        result = build_application()

    assert result is mock_app
    # start, list, message and callback handlers
    assert mock_app.add_handler.call_count >= 4
    mock_app.add_error_handler.assert_called_once()

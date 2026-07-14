"""Error handling across the handlers: every failure must reach the user as a reply.

(The channel_management import this file used to carry is gone with the module.)
"""

from unittest.mock import AsyncMock, patch

import pytest
from miniflux import ClientError, ServerError
from telegram.error import TelegramError

from src.handlers.callbacks import button_callback
from src.handlers.commands import list_channels
from src.handlers.messages import ParsedMessage, handle_message


class MockResponse:
    """Enough of a requests.Response for miniflux.ClientError to work with."""

    def __init__(self, status_code=400, message="Error"):
        self.status_code = status_code
        self.message = message

    def json(self):
        return {"error_message": self.message}

    def __repr__(self):
        # Some handlers render the error with str(error), which falls back to the
        # response's repr — keep it readable so the assertions test the real text.
        return f"status {self.status_code}: {self.message}"


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


# --- handle_message wraps every branch --------------------------------------


async def test_handle_telegram_channel_rate_limit_error(mock_update, mock_context):
    """A Telegram rate limit is recognized and explained rather than dumped raw."""
    with patch(
        "src.handlers.messages._parse_message_content",
        new=AsyncMock(return_value=ParsedMessage(channel_username="test_channel", channel_source_type="forward")),
    ), patch(
        "src.handlers.messages._handle_telegram_channel",
        new=AsyncMock(side_effect=TelegramError("Rate limit exceeded")),
    ):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0].lower()
    assert "rate limit" in message


async def test_handle_telegram_channel_api_error(mock_update, mock_context):
    """A Miniflux error inside the channel branch is reported with the channel name."""
    error = ServerError(MockResponse(status_code=500, message="Internal server error"))

    with patch(
        "src.handlers.messages._parse_message_content",
        new=AsyncMock(return_value=ParsedMessage(channel_username="test_channel", channel_source_type="forward")),
    ), patch("src.handlers.messages._handle_telegram_channel", new=AsyncMock(side_effect=error)):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0]
    assert "Error processing telegram channel" in message
    assert "test_channel" in message


async def test_handle_message_parse_exception(mock_update, mock_context):
    """A parser blow-up is caught by handle_message and answered once."""
    with patch(
        "src.handlers.messages._parse_message_content",
        new=AsyncMock(side_effect=Exception("Failed to parse message content")),
    ):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0]
    assert "Error processing your message" in message
    assert "Failed to parse message content" in message


async def test_handle_message_telegram_channel_exception(mock_update, mock_context):
    with patch(
        "src.handlers.messages._parse_message_content",
        new=AsyncMock(return_value=ParsedMessage(channel_username="channel_username", channel_source_type="forward")),
    ), patch(
        "src.handlers.messages._handle_telegram_channel",
        new=AsyncMock(side_effect=Exception("Failed to handle telegram channel")),
    ):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0].lower()
    assert "error" in message
    assert "channel" in message


async def test_handle_message_direct_rss_exception(mock_update, mock_context):
    with patch(
        "src.handlers.messages._parse_message_content",
        new=AsyncMock(return_value=ParsedMessage(direct_rss_url="https://example.com/feed.xml")),
    ), patch(
        "src.handlers.messages._handle_direct_rss",
        new=AsyncMock(side_effect=Exception("Failed to handle direct RSS")),
    ):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0]
    assert "Error processing RSS feed" in message


async def test_handle_message_html_rss_exception(mock_update, mock_context):
    links = [{"title": "Test", "href": "https://example.com/feed.xml"}]

    with patch(
        "src.handlers.messages._parse_message_content",
        new=AsyncMock(return_value=ParsedMessage(html_rss_links=links)),
    ), patch(
        "src.handlers.messages._handle_html_rss_links",
        new=AsyncMock(side_effect=Exception("Failed to handle HTML RSS links")),
    ):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Error processing website with RSS links" in (
        mock_update.message.reply_text.call_args[0][0]
    )


async def test_handle_message_malicious_url(mock_update, mock_context):
    """A hostile URL scheme is not recognized and gets the generic help text."""
    mock_update.message.text = "javascript:alert('XSS attack')"
    mock_update.message.to_dict.return_value = {"text": "javascript:alert('XSS attack')"}

    await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0]
    assert "Please forward a message from any channel" in message


# --- button_callback edge cases ---------------------------------------------


async def test_button_callback_invalid_cat_id(mock_update, mock_context):
    mock_update.callback_query.data = "cat_invalid"
    mock_context.user_data = {"categories": {1: "Category 1"}}

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "Invalid category ID." in mock_update.callback_query.edit_message_text.call_args[0][0]


async def test_button_callback_missing_channel_and_rss(mock_update, mock_context, mock_miniflux_client):
    """Category chosen but nothing pending: the session is gone, say so."""
    mock_update.callback_query.data = "cat_1"
    mock_context.user_data = {"categories": {1: "Category 1"}}

    await button_callback(mock_update, mock_context)

    mock_miniflux_client.create_feed.assert_not_called()
    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "missing" in mock_update.callback_query.edit_message_text.call_args[0][0].lower()


async def test_button_callback_network_timeout(mock_update, mock_context):
    """A timeout while checking the feed is surfaced with its status code."""
    mock_update.callback_query.data = "rss_link_0"
    mock_context.user_data = {
        "rss_links": [{"href": "https://example.com/feed.xml", "title": "Test Feed"}]
    }

    with patch(
        "src.handlers.callbacks.check_feed_exists",
        side_effect=ClientError(MockResponse(status_code=408, message="Request timed out")),
    ):
        await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    message = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Failed to check if feed exists" in message
    assert "Request timed out" in message


# --- /list ------------------------------------------------------------------


async def test_list_channels_empty_data(mock_update, mock_context):
    with patch("src.handlers.commands.get_channels_by_category", return_value={}):
        await list_channels(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "No channels subscribed" in mock_update.message.reply_text.call_args[0][0]

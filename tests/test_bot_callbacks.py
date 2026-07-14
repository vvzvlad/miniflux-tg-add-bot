"""Error handling inside src/handlers/callbacks.py::button_callback.

(The channel_management tests that used to live here are gone with the module: it
was dead code — never imported by the bot, and its SQLite tables were never created.)
"""

from unittest.mock import patch

import pytest
from miniflux import ClientError

from src.handlers.callbacks import button_callback


class MockResponse:
    """Enough of a requests.Response for miniflux.ClientError to work with."""

    def __init__(self, status_code=400, message="Error"):
        self.status_code = status_code
        self.message = message

    def json(self):
        return {"error_message": self.message}


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


async def test_button_callback_category_selection_api_error(mock_update, mock_context, mock_miniflux_client):
    """A Miniflux error while subscribing a direct feed is shown to the user."""
    mock_update.callback_query.data = "cat_123"
    mock_context.user_data = {
        "direct_rss_url": "https://example.com/feed.xml",
        "categories": {123: "Test Category"},
    }
    mock_miniflux_client.create_feed.side_effect = ClientError(
        MockResponse(status_code=400, message="API Error")
    )

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    message = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Failed to subscribe" in message
    assert "API Error" in message
    # The URL survives the failure so the user can retry without re-sending the link
    assert mock_context.user_data["direct_rss_url"] == "https://example.com/feed.xml"


async def test_button_callback_rss_link_check_feed_error(mock_update, mock_context):
    """A failure of the existence check is reported, not swallowed."""
    mock_update.callback_query.data = "rss_link_0"
    mock_context.user_data = {
        "rss_links": [{"href": "https://example.com/feed.xml", "title": "Test Feed"}]
    }

    with patch(
        "src.handlers.callbacks.check_feed_exists",
        side_effect=ClientError(MockResponse(status_code=400, message="Feed check failed")),
    ):
        await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "Failed to check if feed exists" in (
        mock_update.callback_query.edit_message_text.call_args[0][0]
    )


async def test_button_callback_rss_link_fetch_categories_error(mock_update, mock_context):
    """A failure to list categories stops the flow with a clear message."""
    mock_update.callback_query.data = "rss_link_0"
    mock_context.user_data = {
        "rss_links": [{"href": "https://example.com/feed.xml", "title": "Test Feed"}]
    }

    with patch("src.handlers.callbacks.check_feed_exists", return_value=False), \
         patch("src.handlers.callbacks.fetch_categories", side_effect=Exception("boom")):
        await button_callback(mock_update, mock_context)

    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "Failed to fetch categories" in (
        mock_update.callback_query.edit_message_text.call_args[0][0]
    )


async def test_button_callback_rss_link_success(mock_update, mock_context, mock_miniflux_client):
    """Choosing one of the discovered feeds stores it and asks for a category."""
    mock_update.callback_query.data = "rss_link_1"
    mock_context.user_data = {
        "rss_links": [
            {"href": "https://example.com/a.xml", "title": "Feed A"},
            {"href": "https://example.com/b.xml", "title": "Feed B"},
        ]
    }
    mock_miniflux_client.get_feeds.return_value = []
    mock_miniflux_client.get_categories.return_value = [{"id": 3, "title": "Cat"}]

    await button_callback(mock_update, mock_context)

    assert mock_context.user_data["direct_rss_url"] == "https://example.com/b.xml"
    assert "rss_links" not in mock_context.user_data
    assert mock_context.user_data["categories"] == {3: "Cat"}

    text, kwargs = mock_update.callback_query.edit_message_text.call_args
    assert "Feed B" in text[0]
    assert kwargs["reply_markup"] is not None


async def test_button_callback_rss_link_invalid_index(mock_update, mock_context):
    """A stale keyboard (index out of range) is reported as an expired session."""
    mock_update.callback_query.data = "rss_link_5"
    mock_context.user_data = {"rss_links": [{"href": "https://example.com/a.xml"}]}

    await button_callback(mock_update, mock_context)

    assert "Invalid RSS link selection or session expired." in (
        mock_update.callback_query.edit_message_text.call_args[0][0]
    )


async def test_button_callback_delete_feed_get_feeds_error(mock_update, mock_context, mock_miniflux_client):
    """A failure to list the feeds while deleting is reported to the user."""
    mock_update.callback_query.data = "delete|channelname"
    mock_miniflux_client.get_feeds.side_effect = ClientError(
        MockResponse(status_code=400, message="Get feeds failed")
    )

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.message.chat.send_action.assert_called_once_with("typing")
    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "Failed to delete channel" in (
        mock_update.callback_query.edit_message_text.call_args[0][0]
    )


async def test_button_callback_delete_feed_execute_error(mock_update, mock_context, mock_miniflux_client):
    """A failure of the delete call itself is reported with the Miniflux reason."""
    mock_update.callback_query.data = "delete|channelname"
    mock_miniflux_client.get_feeds.return_value = [
        {"id": 123, "title": "Test Feed", "feed_url": "http://test.rssbridge.local/rss/channelname/test_token"}
    ]
    mock_miniflux_client.delete_feed.side_effect = ClientError(
        MockResponse(status_code=400, message="Delete feed failed")
    )

    await button_callback(mock_update, mock_context)

    mock_update.callback_query.answer.assert_called_once()
    mock_miniflux_client.delete_feed.assert_called_once_with(123)
    message = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Failed to delete channel" in message
    assert "Delete feed failed" in message


async def test_button_callback_flag_data_without_flag(mock_update, mock_context):
    """Malformed flag callback data is rejected with a clear message."""
    mock_update.callback_query.data = "add_flag|only_channel"

    await button_callback(mock_update, mock_context)

    assert "Invalid callback data format for flag action." in (
        mock_update.callback_query.edit_message_text.call_args[0][0]
    )

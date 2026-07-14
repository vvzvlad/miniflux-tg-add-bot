"""Error paths of src/handlers/messages.py::_handle_awaiting_regex."""

from unittest.mock import patch

import pytest

from src.handlers.messages import _handle_awaiting_regex


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


async def test_handle_awaiting_regex_missing_context(mock_update, mock_context):
    """The state is set but its companion keys are gone: report and clear."""
    mock_update.message.text = "test_regex"
    mock_context.user_data = {"state": "awaiting_regex"}

    await _handle_awaiting_regex(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Error: Missing context" in mock_update.message.reply_text.call_args[0][0]
    assert "state" not in mock_context.user_data


async def test_handle_awaiting_regex_get_feed_returns_no_url(mock_update, mock_context, mock_miniflux_client):
    """A feed without a URL cannot be rewritten: say so instead of building garbage."""
    mock_update.message.text = "test_regex"
    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_miniflux_client.get_feed.return_value = {}

    await _handle_awaiting_regex(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Error: Could not retrieve current feed URL" in mock_update.message.reply_text.call_args[0][0]

    assert "state" not in mock_context.user_data
    assert "editing_regex_for_channel" not in mock_context.user_data
    assert "editing_feed_id" not in mock_context.user_data


async def test_handle_awaiting_regex_missing_base_url(mock_update, mock_context, mock_miniflux_client):
    """A feed URL we cannot decompose is an internal error, not a silent failure."""
    mock_update.message.text = "test_regex"
    mock_context.user_data = {
        "state": "awaiting_regex",
        "editing_regex_for_channel": "test_channel",
        "editing_feed_id": 123,
    }
    mock_miniflux_client.get_feed.return_value = {"feed_url": "http://example.com/feed"}

    with patch("src.handlers.messages.parse_feed_url", return_value={}):
        await _handle_awaiting_regex(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "Internal error: could not determine base URL" in mock_update.message.reply_text.call_args[0][0]

    assert "state" not in mock_context.user_data
    assert "editing_regex_for_channel" not in mock_context.user_data
    assert "editing_feed_id" not in mock_context.user_data

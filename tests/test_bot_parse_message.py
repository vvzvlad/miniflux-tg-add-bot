"""Tests for src/handlers/messages.py::_parse_message_content."""

from unittest.mock import patch

import pytest

from src.handlers.messages import ParsedMessage, _parse_message_content, handle_message
from src.settings import settings


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


async def test_parse_message_content_invalid_url(mock_update, mock_context):
    """Text that is neither a link nor a URL is not recognized as anything."""
    mock_update.message.text = "invalid url without http"
    mock_update.message.to_dict.return_value = {"text": "invalid url without http"}

    result = await _parse_message_content(mock_update, mock_context)

    assert result == ParsedMessage()
    assert result.handled is False
    assert result.channel_username is None


async def test_parse_message_content_telegram_username(mock_update, mock_context):
    """A bare @username is treated as a channel."""
    mock_update.message.text = "@test_channel"
    mock_update.message.to_dict.return_value = {"text": "@test_channel"}

    result = await _parse_message_content(mock_update, mock_context)

    assert result.channel_username == "test_channel"
    assert result.channel_source_type == "link_or_username"
    assert result.direct_rss_url is None
    assert result.html_rss_links is None


async def test_parse_message_content_tg_link(mock_update, mock_context):
    """A t.me link is resolved to its channel."""
    mock_update.message.text = "https://t.me/test_channel"
    mock_update.message.to_dict.return_value = {"text": "https://t.me/test_channel"}

    result = await _parse_message_content(mock_update, mock_context)

    assert result.channel_username == "test_channel"
    assert result.channel_source_type == "link_or_username"


async def test_parse_message_content_forward_from_channel(mock_update, mock_context):
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": 1, "title": "T", "username": "fwd_channel", "type": "channel"}
    }

    result = await _parse_message_content(mock_update, mock_context)

    assert result.channel_username == "fwd_channel"
    assert result.channel_source_type == "forward"
    assert result.handled is False


async def test_parse_message_content_forward_from_group_is_handled(mock_update, mock_context):
    """A forward from a group is answered right there, and marked as handled."""
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": 1, "title": "T", "type": "group"}
    }

    result = await _parse_message_content(mock_update, mock_context)

    assert result.handled is True
    assert result.channel_username is None
    mock_update.message.reply_text.assert_called_once_with(
        "Please forward a message from a channel, not from other source."
    )


async def test_parse_message_content_channel_without_username_rejected(
    mock_update, mock_context, monkeypatch
):
    """Without ACCEPT_CHANNELS_WITHOUT_USERNAME, a private channel is refused here."""
    monkeypatch.setattr(settings, "accept_channels_without_username", False)
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": -100123, "title": "Private", "type": "channel"}
    }

    result = await _parse_message_content(mock_update, mock_context)

    assert result.handled is True
    assert result.channel_username is None
    mock_update.message.reply_text.assert_called_once()
    assert "public username" in mock_update.message.reply_text.call_args[0][0]


async def test_parse_message_content_channel_without_username_accepted(
    mock_update, mock_context, monkeypatch
):
    """With the flag on, the numeric channel id is used instead of a username."""
    monkeypatch.setattr(settings, "accept_channels_without_username", True)
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": -100123, "title": "Private", "type": "channel"}
    }

    result = await _parse_message_content(mock_update, mock_context)

    assert result.handled is False
    assert result.channel_username == "-100123"
    mock_update.message.reply_text.assert_not_called()


async def test_parse_message_content_rss_url_error(mock_update, mock_context):
    """An error inside the RSS check propagates to the caller."""
    mock_update.message.text = "https://example.com/feed"
    mock_update.message.to_dict.return_value = {"text": "https://example.com/feed"}

    with patch("src.handlers.messages.is_valid_rss_url", side_effect=Exception("Test RSS validation error")):
        with pytest.raises(Exception, match="Test RSS validation error"):
            await _parse_message_content(mock_update, mock_context)


async def test_handle_message_with_rss_detection_error(mock_update, mock_context):
    """handle_message catches a parser failure and tells the user about it."""
    mock_update.message.text = "https://example.com/some-page"
    mock_update.message.to_dict.return_value = {"text": "https://example.com/some-page"}

    with patch("src.handlers.messages.is_valid_rss_url", side_effect=Exception("Test error detecting RSS")):
        await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0]
    assert "Error processing your message" in message
    assert "Test error detecting RSS" in message

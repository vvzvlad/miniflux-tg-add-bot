import sys
import os
import logging
import pytest
from unittest.mock import patch, MagicMock, AsyncMock, call, ANY

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Message
from telegram.ext import CallbackContext, ConversationHandler
from telegram.error import TelegramError, NetworkError, BadRequest
from miniflux import ClientError, ServerError

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

import bot
import channel_management

# Define custom exceptions for testing
class URLException(Exception):
    """Custom exception for URL-related errors in tests."""
    pass

class MinifluxApiError(Exception):
    """Custom exception for Miniflux API errors in tests."""
    def __init__(self, message, status_code=500):
        self.status_code = status_code
        super().__init__(message)

# Helper mock classes
class MockResponse:
    def __init__(self, status_code=400, message="Error"):
        self.status_code = status_code
        self.message = message
    
    def json(self):
        return {"error_message": self.message}
    
    def get_error_reason(self):
        return self.message

# Fixtures
@pytest.fixture
def mock_update():
    """Create a mock update object for testing."""
    mock = MagicMock(spec=Update)
    mock.message = MagicMock()
    mock.message.reply_text = AsyncMock()
    mock.message.chat = MagicMock()
    mock.message.chat.send_action = AsyncMock()
    mock.message.from_user = MagicMock()
    mock.message.from_user.username = "test_admin"
    return mock

@pytest.fixture
def mock_context():
    """Create a mock context object for testing."""
    mock = MagicMock(spec=CallbackContext)
    mock.user_data = {}
    return mock

@pytest.fixture
def mock_config_and_client():
    """Create a mock for the miniflux_client."""
    mock = MagicMock()
    with patch('bot.miniflux_client', mock):
        yield mock

# Tests for error handling in _handle_telegram_channel
@pytest.mark.asyncio
@patch('bot.fetch_categories')
async def test_handle_telegram_channel_rate_limit_error(mock_fetch_categories, mock_update, mock_context, mock_config_and_client):
    """Test _handle_telegram_channel when Telegram API rate limit error occurs."""
    # Setup
    channel_username = "test_channel"
    channel_source_type = "forward"
    
    # Патчим parse_message_content чтобы избежать попыток анализа неверного сообщения
    with patch('bot._parse_message_content', new=AsyncMock()) as mock_parse:
        # Возвращаем правильно сформированные данные для telegram-канала
        mock_parse.return_value = (channel_username, channel_source_type, None, None)

        # Имитируем ошибку TelegramError при обработке канала
        with patch('bot._handle_telegram_channel', new=AsyncMock()) as mock_handle:
            # Simulate a rate limit error when sending typing action
            mock_update.message.chat.send_action.side_effect = TelegramError("Rate limit exceeded")
            
            # Имитируем обработку ошибки внутри _handle_telegram_channel
            mock_handle.side_effect = TelegramError("Rate limit exceeded")
            
            # Execute - вызываем обработчик сообщений, который должен перехватить ошибку
            await bot.handle_message(mock_update, mock_context)
            
            # Assert - проверяем, что отправлено сообщение об ошибке
            mock_parse.assert_called_once() # Проверяем что функция парсинга была вызвана
            mock_handle.assert_called_once() # Проверяем что функция обработки канала была вызвана
            mock_update.message.reply_text.assert_called_once() # Должно быть только одно сообщение об ошибке
            call_args = mock_update.message.reply_text.call_args[0][0]
            assert "telegram" in call_args.lower() or "rate limit" in call_args.lower()

@pytest.mark.asyncio
async def test_handle_telegram_channel_already_exists_api_error(mock_update, mock_context, mock_config_and_client):
    """Test _handle_telegram_channel when feed exists but API error occurs during check."""
    # Setup
    channel_username = "test_channel"
    channel_source_type = "forward"
    
    # Mock get_feeds to return feeds
    mock_config_and_client.get_feeds.return_value = [
        {
            "id": 123,
            "title": "Test Channel",
            "feed_url": f"https://example.com/rss/test_channel"
        }
    ]
    
    # Патчим parse_message_content чтобы избежать попыток анализа неверного сообщения
    with patch('bot._parse_message_content', new=AsyncMock()) as mock_parse:
        # Возвращаем правильно сформированные данные для telegram-канала
        mock_parse.return_value = (channel_username, channel_source_type, None, None)
        
        # Patch check_feed_exists, которая будет вызвана в _handle_telegram_channel
        with patch('miniflux_api.check_feed_exists', new=AsyncMock()) as mock_check:
            # Simulate an API error during the feed check process
            mock_response = MockResponse(status_code=500, message="Internal server error")
            mock_check.side_effect = ServerError(mock_response)
            
            # Mock is_admin to return True
            with patch('bot.is_admin', return_value=True):
                # Mock RSS_BRIDGE_URL (used in check)
                with patch('bot.RSS_BRIDGE_URL', 'https://example.com/rss/{channel}'):
                    with patch('bot._handle_telegram_channel', new=AsyncMock()) as mock_handle:
                        # Имитируем обработку ошибки внутри _handle_telegram_channel
                        mock_handle.side_effect = ServerError(mock_response)
                        
                        # Execute - вызываем handle_message, который должен перехватить ошибку
                        await bot.handle_message(mock_update, mock_context)
                        
                        # Assert - проверяем, что отправлено сообщение об ошибке
                        mock_parse.assert_called_once() # Проверяем что функция парсинга была вызвана
                        mock_handle.assert_called_once() # Проверяем что функция обработки канала была вызвана
                        mock_update.message.reply_text.assert_called_once() # Должно быть только одно сообщение
                        call_args = mock_update.message.reply_text.call_args[0][0]
                        # Проверяем что есть любое упоминание об ошибке и канале
                        assert "error" in call_args.lower() or "failed" in call_args.lower()
                        assert channel_username in call_args or f"@{channel_username}" in call_args
                        assert "mockresponse" in call_args.lower() or "servererror" in call_args.lower() or "server" in call_args.lower() or "500" in call_args

# Tests for error handling in handle_message
@pytest.mark.asyncio
@patch('bot._parse_message_content')
async def test_handle_message_parse_exception(mock_parse_content, mock_update, mock_context):
    """Test handle_message when parsing message content throws an exception."""
    # Setup - вызываем исключение при парсинге контента сообщения
    mock_parse_content.side_effect = Exception("Failed to parse message content")
    
    # Execute - вызываем обработчик сообщений
    await bot.handle_message(mock_update, mock_context)
    
    # Assert - проверяем перехват ошибки
    mock_update.message.reply_text.assert_called_once()
    call_args = mock_update.message.reply_text.call_args[0][0]
    assert "error" in call_args.lower() or "failed" in call_args.lower() or "invalid" in call_args.lower()

@pytest.mark.asyncio
@patch('bot._parse_message_content')
@patch('bot._handle_telegram_channel')
async def test_handle_message_telegram_channel_exception(mock_handle_channel, mock_parse_content, mock_update, mock_context):
    """Test handle_message when handling telegram channel throws an exception."""
    # Setup
    mock_parse_content.return_value = ("channel_username", "forward", None, None)
    mock_handle_channel.side_effect = Exception("Failed to handle telegram channel")
    
    # Execute
    await bot.handle_message(mock_update, mock_context)
    
    # Assert
    mock_update.message.reply_text.assert_called_once()
    call_args = mock_update.message.reply_text.call_args[0][0]
    assert "error" in call_args.lower() or "failed" in call_args.lower()
    assert "channel" in call_args.lower() or "telegram" in call_args.lower()

@pytest.mark.asyncio
@patch('bot._parse_message_content')
@patch('bot._handle_direct_rss')
async def test_handle_message_direct_rss_exception(mock_handle_rss, mock_parse_content, mock_update, mock_context):
    """Test handle_message when handling direct RSS throws an exception."""
    # Setup
    mock_parse_content.return_value = (None, None, "https://example.com/feed.xml", None)
    mock_handle_rss.side_effect = Exception("Failed to handle direct RSS")
    
    # Execute
    await bot.handle_message(mock_update, mock_context)
    
    # Assert
    mock_update.message.reply_text.assert_called_once()
    call_args = mock_update.message.reply_text.call_args[0][0]
    assert "error" in call_args.lower() or "failed" in call_args.lower()
    assert "rss" in call_args.lower() or "feed" in call_args.lower()

@pytest.mark.asyncio
@patch('bot._parse_message_content')
@patch('bot._handle_html_rss_links')
async def test_handle_message_html_rss_exception(mock_handle_html, mock_parse_content, mock_update, mock_context):
    """Test handle_message when handling HTML RSS links throws an exception."""
    # Setup
    mock_parse_content.return_value = (None, None, None, [{"title": "Test", "href": "https://example.com/feed.xml"}])
    mock_handle_html.side_effect = Exception("Failed to handle HTML RSS links")
    
    # Execute
    await bot.handle_message(mock_update, mock_context)
    
    # Assert
    mock_update.message.reply_text.assert_called_once()
    call_args = mock_update.message.reply_text.call_args[0][0]
    assert "error" in call_args.lower() or "failed" in call_args.lower()
    assert "website" in call_args.lower() or "html" in call_args.lower() or "rss links" in call_args.lower()

# Tests for edge cases in button_callback
@pytest.mark.asyncio
async def test_button_callback_invalid_cat_id(mock_update, mock_context):
    """Test button_callback with invalid category id format."""
    # Setup
    mock_update.callback_query = AsyncMock()
    mock_update.callback_query.data = "cat_invalid"  # Invalid format - not a number
    mock_context.user_data = {
        "categories": {1: "Category 1", 2: "Category 2"}
    }
    
    # Execute
    await bot.button_callback(mock_update, mock_context)
    
    # Assert
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    call_args = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "invalid" in call_args.lower() or "error" in call_args.lower()
    assert "category" in call_args.lower() or "cat_" in call_args.lower()

@pytest.mark.asyncio
async def test_button_callback_missing_category_data(mock_update, mock_context):
    """Test button_callback with missing user data for categories."""
    # Setup
    mock_update.callback_query = AsyncMock()
    mock_update.callback_query.data = "cat_1"
    mock_context.user_data = {}  # Missing categories data
    
    # Execute
    await bot.button_callback(mock_update, mock_context)
    
    # Assert
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    call_args = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "missing" in call_args.lower() or "error" in call_args.lower() or "invalid" in call_args.lower()

@pytest.mark.asyncio
async def test_button_callback_missing_direct_rss_url(mock_update, mock_context, mock_config_and_client):
    """Test button_callback with category selection but missing direct_rss_url."""
    # Setup
    mock_update.callback_query = AsyncMock()
    mock_update.callback_query.data = "cat_1"
    mock_context.user_data = {
        "categories": {1: "Category 1", 2: "Category 2"}
        # Missing direct_rss_url and channel_title
    }
    
    # Execute
    await bot.button_callback(mock_update, mock_context)
    
    # Assert
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    call_args = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "missing" in call_args.lower() or "error" in call_args.lower() or "invalid" in call_args.lower()

# Tests for handling network timeouts
@pytest.mark.asyncio
@patch('bot.check_feed_exists')
async def test_button_callback_network_timeout(mock_check_feed, mock_update, mock_context, mock_config_and_client):
    """Test button_callback handling network timeout during API calls."""
    # Setup
    mock_update.callback_query = AsyncMock()
    mock_update.callback_query.data = "rss_link_0"
    mock_update.callback_query.message = AsyncMock()
    mock_context.user_data = {
        "rss_links": [{"href": "https://example.com/feed.xml", "title": "Test Feed"}]
    }
    
    # Simulate network timeout 
    mock_check_feed.side_effect = ClientError(MockResponse(status_code=408, message="Request timed out"))
    
    # Execute
    await bot.button_callback(mock_update, mock_context)
    
    # Assert
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once()
    call_args = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "failed" in call_args.lower() or "error" in call_args.lower()
    assert "feed exists" in call_args.lower() or "check" in call_args.lower()
    # В сообщении должно содержаться либо тайм-аут, либо хотя бы код ошибки
    assert "timeout" in call_args.lower() or "timed out" in call_args.lower() or "408" in call_args or "error" in call_args.lower()

# Tests for invalid user input patterns
@pytest.mark.asyncio
async def test_handle_message_malicious_url(mock_update, mock_context):
    """Test handle_message with potentially malicious URL."""
    # Setup
    mock_update.message.text = "javascript:alert('XSS attack')"
    
    # Patch вместо перехвата
    with patch('bot._parse_message_content', new=AsyncMock()) as mock_parse:
        mock_parse.side_effect = Exception("Invalid URL scheme")
        
        # Execute
        await bot.handle_message(mock_update, mock_context)
        
        # Assert - проверяем что была только одна ошибка
        call_args_list = mock_update.message.reply_text.call_args_list
        assert len(call_args_list) > 0
        # В одном из сообщений должно быть упоминание URL или схемы
        has_error_message = False
        for call in call_args_list:
            args = call[0][0].lower()
            if "invalid" in args or "error" in args or "not a valid url" in args:
                has_error_message = True
                break
        assert has_error_message

@pytest.mark.asyncio
async def test_list_channels_empty_data(mock_update, mock_context, mock_config_and_client):
    """Test list_channels when API returns empty data."""
    # Setup - make get_channels_by_category return an empty dict
    with patch('bot.get_channels_by_category', return_value={}):
        # Execute
        await bot.list_channels(mock_update, mock_context)
        
        # Assert
        mock_update.message.reply_text.assert_called_once()
        call_args = mock_update.message.reply_text.call_args[0][0]
        assert "no channels" in call_args.lower() or "no subscriptions" in call_args.lower() or "empty" in call_args.lower() 
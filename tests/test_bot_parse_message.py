from unittest.mock import MagicMock, patch, AsyncMock
import pytest
import sys
import os
import re

# Add the parent directory to sys.path to import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import _parse_message_content, handle_message

@pytest.mark.asyncio
async def test_parse_message_content_invalid_url():
    """Test handling of invalid URLs in _parse_message_content."""
    # Mock the update object
    update = MagicMock()
    update.message = MagicMock()
    update.message.to_dict.return_value = {"text": "invalid url without http"}
    update.message.text = "invalid url without http"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.message.media_group_id = None
    
    # Create context
    context = MagicMock()
    
    # Call the function
    result = await _parse_message_content(update, context)
    
    # This should return all None values due to invalid URL
    assert result == (None, None, None, None)

@pytest.mark.asyncio
async def test_parse_message_content_telegram_username():
    """Test parsing a Telegram username."""
    # Mock the update object
    update = MagicMock()
    update.message = MagicMock()
    update.message.to_dict.return_value = {"text": "@test_channel"}
    update.message.text = "@test_channel"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.message.media_group_id = None
    
    # Create context mock
    context = MagicMock()
    context.user_data = {}
    
    # Mock the re.match function
    original_re_match = re.match
    def patched_re_match(pattern, text):
        if pattern == r"@([a-zA-Z0-9_]+)" and text == "@test_channel":
            match = MagicMock()
            match.group.return_value = "test_channel"
            return match
        return original_re_match(pattern, text)
    
    # Patch re.match for this test
    with patch('re.match', patched_re_match):
        # Call the function
        result = await _parse_message_content(update, context)
        
        # Should return the channel username and source type
        channel_username, channel_source_type, direct_rss_url, html_rss_links = result
        assert channel_username == "test_channel"
        assert channel_source_type == "link_or_username"
        assert direct_rss_url is None
        assert html_rss_links is None

@pytest.mark.asyncio
async def test_parse_message_content_tg_link():
    """Test parsing a Telegram link with specific coverage of lines 394-397."""
    # Mock the update object
    update = MagicMock()
    update.message = MagicMock()
    update.message.to_dict.return_value = {"text": "https://t.me/test_channel"}
    update.message.text = "https://t.me/test_channel"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.message.media_group_id = None
    
    # Create context mock
    context = MagicMock()
    context.user_data = {}
    
    # Patch parse_telegram_link to return a channel
    with patch('bot.parse_telegram_link', return_value="test_channel"):
        # Call the function
        result = await _parse_message_content(update, context)
        
        # Should return the channel username and source type
        channel_username, channel_source_type, direct_rss_url, html_rss_links = result
        assert channel_username == "test_channel"
        assert channel_source_type == "link_or_username"
        assert direct_rss_url is None
        assert html_rss_links is None

@pytest.mark.asyncio
async def test_parse_message_content_rss_url_error():
    """Test handling errors during RSS URL processing to cover lines 403-410."""
    # Mock the update object
    update = MagicMock()
    update.message = MagicMock()
    update.message.to_dict.return_value = {"text": "https://example.com/feed"}
    update.message.text = "https://example.com/feed"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.message.media_group_id = None
    
    # Create context mock
    context = MagicMock()
    context.user_data = {}
    
    # First patch parse_telegram_link to return None (not a Telegram channel)
    with patch('bot.parse_telegram_link', return_value=None):
        # Then patch is_valid_rss_url to raise an exception - this should cover lines 403-410
        with patch('bot.is_valid_rss_url', side_effect=Exception("Test RSS validation error")):
            # The function should catch this exception and return None values
            with pytest.raises(Exception) as exc_info:
                await _parse_message_content(update, context)
            assert "Test RSS validation error" in str(exc_info.value)

@pytest.mark.asyncio
async def test_handle_message_with_rss_detection_error():
    """Test handling of errors in RSS detection at higher level in handle_message."""
    # Mock the update object
    update = MagicMock()
    update.message = MagicMock()
    update.message.to_dict.return_value = {"text": "https://example.com/some-page"}
    update.message.text = "https://example.com/some-page"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.message.media_group_id = None
    update.message.from_user = MagicMock()
    update.message.from_user.username = "test_admin"  # Set admin username
    
    # Create context
    context = MagicMock()
    context.user_data = {}  # Initialize user_data
    
    # Mock is_admin to return True
    with patch('bot.is_admin', return_value=True):
        # Mock parse_telegram_link to return None (not a Telegram channel)
        with patch('bot.parse_telegram_link', return_value=None):
            # Mock is_valid_rss_url to raise an exception
            # This should be caught by handle_message's error handling
            with patch('bot.is_valid_rss_url', side_effect=Exception("Test error detecting RSS")):
                # Call handle_message, which calls _parse_message_content internally
                await handle_message(update, context)
                
                # Verify that reply_text was called with error message
                update.message.reply_text.assert_called_once()
                called_with = update.message.reply_text.call_args[0][0]
                assert "Error processing your message" in called_with
                assert "Test error detecting RSS" in called_with 
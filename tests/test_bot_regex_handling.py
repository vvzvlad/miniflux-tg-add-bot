from unittest.mock import MagicMock, patch, AsyncMock
import pytest
import sys
import os

# Add the parent directory to sys.path to import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import _handle_awaiting_regex

@pytest.mark.asyncio
async def test_handle_awaiting_regex_missing_context():
    """Test handling of missing context data in _handle_awaiting_regex."""
    # Mock the update object
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test_regex"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    
    # Create context with missing data - this should trigger the error branch
    context = MagicMock()
    context.user_data = {
        'state': 'awaiting_regex',
        # Missing 'editing_regex_for_channel' and 'editing_feed_id'
    }
    
    # Call the function
    await _handle_awaiting_regex(update, context)
    
    # Verify the error message was sent
    update.message.reply_text.assert_called_once()
    error_msg = update.message.reply_text.call_args[0][0]
    assert "Error: Missing context" in error_msg, "Should show missing context error"
    
    # Verify state was cleaned up
    assert 'state' not in context.user_data, "State should be cleared"

@pytest.mark.asyncio
async def test_handle_awaiting_regex_get_feed_error():
    """Test handling of errors when retrieving feed URL from miniflux."""
    # Mock the update object
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test_regex"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    
    # Create context with necessary data
    context = MagicMock()
    context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': 'test_channel',
        'editing_feed_id': 123
    }
    
    # Mock miniflux_client.get_feed to return empty data without feed_url
    # This should trigger the error at lines 161-163
    with patch('bot.miniflux_client.get_feed', return_value={}):
        # Call the function
        await _handle_awaiting_regex(update, context)
        
        # Verify the error message was sent
        update.message.reply_text.assert_called_once()
        error_msg = update.message.reply_text.call_args[0][0]
        assert "Error: Could not retrieve current feed URL" in error_msg
        
        # Verify state was cleaned up
        assert 'state' not in context.user_data, "State should be cleared"
        assert 'editing_regex_for_channel' not in context.user_data, "editing_regex_for_channel should be cleared"
        assert 'editing_feed_id' not in context.user_data, "editing_feed_id should be cleared"

@pytest.mark.asyncio
async def test_handle_awaiting_regex_missing_base_url():
    """Test handling of errors when base URL cannot be extracted from feed URL."""
    # Mock the update object
    update = MagicMock()
    update.message = AsyncMock()
    update.message.text = "test_regex"
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    
    # Create context with necessary data
    context = MagicMock()
    context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': 'test_channel',
        'editing_feed_id': 123
    }
    
    # Mock miniflux_client.get_feed to return data with feed_url
    with patch('bot.miniflux_client.get_feed', return_value={"feed_url": "http://example.com/feed"}):
        # Mock parse_feed_url to return data without base_url
        # This should trigger the error at lines 179-181
        with patch('bot.parse_feed_url', return_value={}):
            # Call the function
            await _handle_awaiting_regex(update, context)
            
            # Verify the error message was sent
            update.message.reply_text.assert_called_once()
            error_msg = update.message.reply_text.call_args[0][0]
            assert "Internal error: could not determine base URL" in error_msg
            
            # Verify state was cleaned up
            assert 'state' not in context.user_data, "State should be cleared"
            assert 'editing_regex_for_channel' not in context.user_data, "editing_regex_for_channel should be cleared"
            assert 'editing_feed_id' not in context.user_data, "editing_feed_id should be cleared" 
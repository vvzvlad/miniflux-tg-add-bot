from unittest.mock import MagicMock, patch, AsyncMock
import pytest
import sys
import os

# Add the parent directory to sys.path to import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot import list_channels

@pytest.mark.asyncio
async def test_message_chunking_for_long_list():
    """Test that long messages are properly chunked when listing channels."""
    # Mock the update object
    update = MagicMock()
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.message.from_user = MagicMock()
    update.message.from_user.username = "test_admin"  # Set username for admin check
    
    # Create test data with many feeds to generate a long message
    # This should trigger the chunking logic in lines 94-126
    many_feeds = [
        {
            "category": {"title": "TestCategory"}, 
            "title": f"Feed{i}_" + "x" * 200,  # Make each title very long to trigger chunking
            "id": i,
            "flags": [],  # Initialize empty flags for all feeds
            "excluded_text": None  # Initialize excluded_text as None for all feeds
        }
        for i in range(1, 100)  # Create many feeds to ensure the message exceeds 4000 chars
    ]
    
    # Set flags for some feeds to make the message even longer
    for i in range(20):
        many_feeds[i]["flags"] = ["some_flag", "another_flag", "third_flag", "fourth_flag"]
        
    # Set excluded_text for some feeds to make the message even longer
    for i in range(30, 50):
        many_feeds[i]["excluded_text"] = f"excluded_pattern_{i}_" + "y" * 100
    
    # Create a context mock
    context = MagicMock()
    
    # Create a patch for is_admin to always return True
    with patch('bot.is_admin', return_value=True):
        # Patch get_channels_by_category to pass through our test data
        with patch('bot.get_channels_by_category', return_value={"TestCategory": many_feeds}):
            # Call the function
            await list_channels(update, context)
            
            # Check that reply_text was called multiple times (for chunks)
            call_count = update.message.reply_text.call_count
            # We should have at least 3 calls if chunking worked:
            # 1. "Subscribed channels by category:"
            # 2. First chunk
            # 3. Second chunk (or more)
            assert call_count >= 3, f"Expected at least 3 calls to reply_text, but got {call_count}"
            
            # Verify the introduction message
            intro_text = update.message.reply_text.call_args_list[0][0][0]
            assert "Subscribed channels by category" in intro_text
            
            # Check that the second call contains the category title
            first_chunk = update.message.reply_text.call_args_list[1][0][0]
            assert "TestCategory" in first_chunk, "First chunk should contain category title"
            
            # If we have more than 2 chunks, the last one should contain "(continued)"
            if call_count > 3:
                last_call_args = update.message.reply_text.call_args_list[-1][0]
                assert "(continued)" in last_call_args[0], "Later chunk should indicate continuation"
            
            # Make sure each message is under 4000 chars
            for call in update.message.reply_text.call_args_list:
                message_text = call[0][0]
                assert len(message_text) <= 4000, f"Chunked message exceeds 4000 chars: {len(message_text)}"

@pytest.mark.asyncio
async def test_no_chunking_for_short_list():
    """Test that short messages are not chunked when listing channels."""
    # Mock the update object
    update = MagicMock()
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    update.message.from_user = MagicMock()
    update.message.from_user.username = "test_admin"  # Set username for admin check
    
    # Create test data with few feeds to generate a short message
    few_feeds = [
        {
            "category": {"title": "TestCategory"}, 
            "title": f"Feed{i}", 
            "id": i,
            "flags": [],  # Initialize empty flags
            "excluded_text": None  # Initialize excluded_text as None
        }
        for i in range(1, 5)  # Only a few feeds
    ]
    
    # Create a context mock
    context = MagicMock()
    
    # Create a patch for is_admin to always return True
    with patch('bot.is_admin', return_value=True):
        # Patch get_channels_by_category to pass through our test data
        with patch('bot.get_channels_by_category', return_value={"TestCategory": few_feeds}):
            # Call the function
            await list_channels(update, context)
            
            # Check that reply_text was called exactly twice (intro + one category)
            call_count = update.message.reply_text.call_count
            assert call_count == 2, f"Expected exactly 2 calls to reply_text, but got {call_count}"
            
            # First call should be the introduction
            intro_text = update.message.reply_text.call_args_list[0][0][0]
            assert "Subscribed channels by category" in intro_text
            
            # Second call should contain the category with all feed titles
            message_text = update.message.reply_text.call_args_list[1][0][0]
            assert "TestCategory" in message_text
            for i in range(1, 5):
                assert f"Feed{i}" in message_text, f"Message should contain Feed{i}" 
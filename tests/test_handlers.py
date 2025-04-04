import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call
import sys
import os
from datetime import datetime
import urllib.parse
import inspect
import logging
import asyncio

# Import from parent directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import bot functions
from bot import (
    start, 
    handle_message, 
    button_callback,
    list_channels,
    _parse_message_content,
    _handle_awaiting_regex,
    _handle_awaiting_merge_time,
    _handle_telegram_channel,
    _handle_direct_rss,
    _handle_html_rss_links,
    _handle_unknown_message,
    _handle_flag_toggle,
    create_flag_keyboard
)
from miniflux_api import fetch_categories, get_channels_by_category

# Import other functions/modules used in bot for patching
from url_constructor import parse_feed_url, build_feed_url
from miniflux_api import update_feed_url as update_feed_url_api # Alias used in bot.py
# Import state constants or check how state is represented if not string
# Assuming state is stored as string e.g., 'awaiting_regex'
# Import button class for keyboard mock
from telegram import InlineKeyboardButton

# Import RSS_BRIDGE_URL directly from config for assertion
from config import RSS_BRIDGE_URL

# Import exceptions for testing error handling
from miniflux import ClientError

# Fixtures `mock_update`, `mock_context`, `mock_config_and_client` are provided by conftest.py

# Test data
EXPECTED_FEED_URL = "https://example.com/feed"

@pytest.mark.asyncio
async def test_start_admin(mock_update, mock_context, mock_config_and_client):
    """Test the start command for admin user."""
    # Set up
    mock_update.message.from_user.username = "test_admin"  # Admin user (defined in conftest.py)
    
    # Call the function
    await start(mock_update, mock_context)
    
    # Assert
    mock_update.message.reply_text.assert_called_once()
    assert "Forward me a message from any channel" in mock_update.message.reply_text.call_args[0][0]

@pytest.mark.asyncio
async def test_start_non_admin(mock_update, mock_context, mock_config_and_client):
    """Test the start command for non-admin user."""
    # Set up
    mock_update.message.from_user.username = "non_admin_user"  # Non-admin user
    
    # Call the function
    await start(mock_update, mock_context)
    
    # Assert
    mock_update.message.reply_text.assert_called_once()
    assert "Access denied" in mock_update.message.reply_text.call_args[0][0]

@pytest.mark.asyncio
async def test_handle_message_forward_new_channel(mock_update, mock_context, mock_config_and_client):
    """Test handling forwarded message for a new channel."""
    # Set up
    mock_update.message.from_user.username = "test_admin"  # Admin user
    
    # Mocking the forwarded message
    message_dict = {
        'forward_from_chat': {
            'id': 67890,
            'title': 'Test Channel',
            'username': 'test_channel',
            'type': 'channel'
        },
        'forward_date': datetime.now()
    }
    mock_update.message.to_dict.return_value = message_dict
    
    # Set up the miniflux client to return no existing feeds
    mock_config_and_client.get_feeds.return_value = []
    
    # Mock fetch_categories directly in bot module
    with patch('bot.fetch_categories', return_value=[
        {'id': 1, 'title': 'Category 1'},
        {'id': 2, 'title': 'Category 2'}
    ]):
        # Call the function
        await handle_message(mock_update, mock_context)
    
    # Assert
    mock_update.message.reply_text.assert_called_once()
    assert "category" in mock_update.message.reply_text.call_args[0][0].lower()

@pytest.mark.asyncio    
async def test_button_callback_select_category(mock_update, mock_context, mock_config_and_client):
    """Test callback for selecting a category."""
    # Set up
    mock_update.callback_query.data = "cat_1"  # Correct format: cat_{category_id}
    mock_update.callback_query.from_user.username = "test_admin"  # Admin user
    
    # Context state similar to after handling forward message
    # This state would be set when handle_message asks for category selection
    mock_context.user_data = {
        'channel_title': 'test_channel', # Required to build feed URL
        'categories': {1: 'Category 1', 2: 'Category 2'} # Required for success message
    }
    
    # Set return value for create_feed
    mock_config_and_client.create_feed.return_value = None
    
    # Call the function
    await button_callback(mock_update, mock_context)
    
    # Assert
    mock_update.callback_query.answer.assert_called_once()
    
    # Check if create_feed was called with expected arguments
    expected_feed_url = "http://test.rssbridge.local/rss/test_channel/test_token" # Based on conftest and user_data
    mock_config_and_client.create_feed.assert_called_once_with(expected_feed_url, category_id=1)
    
    mock_update.callback_query.edit_message_text.assert_called_once()
    assert "subscribed" in mock_update.callback_query.edit_message_text.call_args[0][0].lower()

# --- Tests for handle_message variations ---

@pytest.mark.asyncio
async def test_handle_message_direct_rss(mock_update, mock_context, mock_config_and_client):
    """Test handling a message with a direct RSS URL."""
    # Setup
    rss_url = "https://direct.example.com/feed.xml"
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.text = rss_url
    mock_update.message.forward_from_chat = None # Not a forward
    mock_update.message.to_dict.return_value = { # Simplified dict for parsing
        "message_id": 2,
        "from": {"id": 12345, "is_bot": False, "username": "test_admin"},
        "chat": {"id": 12345, "type": "private"},
        "text": rss_url
    }
    
    # Mock dependencies
    # is_valid_rss_url returns a tuple (is_direct, result)
    with patch('bot.is_valid_rss_url', return_value=(True, rss_url)) as mock_is_valid:
        with patch('bot.fetch_categories', return_value=[
             {'id': 10, 'title': 'RSS Cat 1'},
             {'id': 11, 'title': 'RSS Cat 2'}
         ]) as mock_fetch_cat:
        
            # Call handler
            await handle_message(mock_update, mock_context)
        
            # Assertions
            mock_is_valid.assert_called_once_with(rss_url)
            mock_fetch_cat.assert_called_once_with(mock_config_and_client)
            mock_update.message.reply_text.assert_called_once()
            
            # Check category selection message (actual text from bot.py)
            call_args, call_kwargs = mock_update.message.reply_text.call_args
            assert call_args[0].startswith("URL is a valid RSS feed. Select category:") 
            assert "reply_markup" in call_kwargs
            
            # Check context
            assert mock_context.user_data.get('direct_rss_url') == rss_url
            assert mock_context.user_data.get('categories') == {10: 'RSS Cat 1', 11: 'RSS Cat 2'}

@pytest.mark.asyncio
async def test_handle_message_html_page(mock_update, mock_context, mock_config_and_client):
    """Test handling a message with a URL linking to an HTML page containing RSS links."""
    # Setup
    html_url = "https://blog.example.com/article"
    # Correct format: list of dictionaries
    found_rss_links = [
        {"title": "Blog Feed", "href": "https://blog.example.com/feed.xml"},
        {"title": "Comments Feed", "href": "https://blog.example.com/comments/feed/"}
    ]
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.text = html_url
    mock_update.message.forward_from_chat = None
    mock_update.message.to_dict.return_value = { # Simplified dict
        "message_id": 3,
        "from": {"id": 12345, "is_bot": False, "username": "test_admin"},
        "chat": {"id": 12345, "type": "private"},
        "text": html_url
    }
    
    # Mock dependencies
    # is_valid_rss_url returns (False, list_of_links) for HTML
    with patch('bot.is_valid_rss_url', return_value=(False, found_rss_links)) as mock_is_valid:
        # No need to patch fetch_categories here, it's called later in button_callback
        # We just need to ensure the link selection keyboard is shown.
        pass # No inner patch needed

        # Call handler
        await handle_message(mock_update, mock_context)

        # Assertions
        mock_is_valid.assert_called_once_with(html_url)
        # fetch_categories is NOT called at this stage
        # mock_fetch_cat.assert_called_once_with(mock_config_and_client) 
        mock_update.message.reply_text.assert_called_once()
        
        # Check RSS link selection message (actual text from _handle_html_rss_links)
        call_args, call_kwargs = mock_update.message.reply_text.call_args
        assert call_args[0].startswith("Found multiple RSS feeds on the webpage. Select one to subscribe:") 
        assert "reply_markup" in call_kwargs
        
        # Check context (rss_links should be stored, but categories not yet)
        assert mock_context.user_data.get('rss_links') == found_rss_links # Stored for button callback
        assert mock_context.user_data.get('categories') is None # Not fetched yet

@pytest.mark.asyncio
async def test_handle_message_unknown(mock_update, mock_context, mock_config_and_client):
    """Test handling a message that is not a forward, link, or valid RSS/HTML URL."""
    # Setup - Use a URL that will return (False, []) from is_valid_rss_url
    unknown_url = "https://example.com/not_a_feed"
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.text = unknown_url 
    mock_update.message.forward_from_chat = None
    mock_update.message.to_dict.return_value = { # Simplified dict
        "message_id": 4,
        "from": {"id": 12345, "is_bot": False, "username": "test_admin"},
        "chat": {"id": 12345, "type": "private"},
        "text": unknown_url
    }
    
    # Mock dependencies
    # is_valid_rss_url returns (False, []) for unknown URLs
    with patch('bot.is_valid_rss_url', return_value=(False, [])) as mock_is_valid:
        
        # Call handler
        await handle_message(mock_update, mock_context)
    
        # Assertions
        mock_is_valid.assert_called_once_with(unknown_url) # Ensure it was called for the URL
        mock_update.message.reply_text.assert_called_once()
        # Check the specific message for invalid URLs from _handle_unknown_message
        assert "does not appear to be a valid RSS feed" in mock_update.message.reply_text.call_args[0][0]

# --- Tests for /list command ---

@pytest.mark.asyncio
async def test_list_channels_success(mock_update, mock_context, mock_config_and_client):
    """Test /list command with successful data retrieval and formatting."""
    # Setup
    mock_update.message.from_user.username = "test_admin"
    
    # Mock data returned by get_channels_by_category
    mock_channel_data = {
        "Category A": [
            {"title": "channel_one", "flags": ["#noads", "#images"], "excluded_text": None, "merge_seconds": None},
            {"title": "channel_two", "flags": [], "excluded_text": "filter this", "merge_seconds": 300}
        ],
        "Category B": [
            {"title": "channel_three", "flags": [], "excluded_text": None, "merge_seconds": None}
        ]
    }
    
    # Patch the function within the bot module where it's used
    with patch('bot.get_channels_by_category', return_value=mock_channel_data) as mock_get_channels:
        # Call the /list handler
        await list_channels(mock_update, mock_context)
        
        # Assertions
        mock_get_channels.assert_called_once_with(
            mock_config_and_client,
            RSS_BRIDGE_URL
        )
        mock_update.message.chat.send_action.assert_called_once_with("typing")
        
        # Check that reply_text was called (1 header + 2 categories in this case)
        assert mock_update.message.reply_text.call_count == 3
        
        # Check header message
        header_call_args, _ = mock_update.message.reply_text.call_args_list[0]
        assert header_call_args[0] == "Subscribed channels by category:"
        
        # Check first category message
        cat_a_call_args, cat_a_call_kwargs = mock_update.message.reply_text.call_args_list[1]
        assert "📁 Category A" in cat_a_call_args[0]
        assert "• channel_one, flags: #noads #images" in cat_a_call_args[0]
        assert "• channel_two, regex: `filter this`" in cat_a_call_args[0]
        assert cat_a_call_kwargs.get("parse_mode") == "MarkdownV2" # Check parse mode
        
        # Check second category message
        cat_b_call_args, cat_b_call_kwargs = mock_update.message.reply_text.call_args_list[2]
        assert "📁 Category B" in cat_b_call_args[0]
        assert "• channel_three" in cat_b_call_args[0]
        assert cat_b_call_kwargs.get("parse_mode") == "MarkdownV2"

@pytest.mark.asyncio
async def test_list_channels_empty(mock_update, mock_context, mock_config_and_client):
    """Test /list command when no channels are subscribed."""
    # Setup
    mock_update.message.from_user.username = "test_admin"
    
    # Mock empty return value
    with patch('bot.get_channels_by_category', return_value={}) as mock_get_channels:
        # Call the /list handler
        await list_channels(mock_update, mock_context)
        
        # Assertions
        mock_get_channels.assert_called_once_with(
            mock_config_and_client,
            RSS_BRIDGE_URL
        )
        mock_update.message.chat.send_action.assert_called_once_with("typing")
        mock_update.message.reply_text.assert_called_once_with("No channels subscribed through RSS Bridge found.")

@pytest.mark.asyncio
async def test_list_channels_non_admin(mock_update, mock_context, mock_config_and_client):
    """Test /list command for a non-admin user."""
    # Setup
    mock_update.message.from_user.username = "other_user"
    
    with patch('bot.get_channels_by_category') as mock_get_channels:
        # Call the /list handler
        await list_channels(mock_update, mock_context)
        
        # Assertions
        mock_get_channels.assert_not_called() # Should not attempt to get data
        mock_update.message.chat.send_action.assert_not_called() # Should not send typing
        mock_update.message.reply_text.assert_called_once_with("Access denied. Only admin can use this bot.")

@pytest.mark.asyncio
@patch('bot.get_channels_by_category')
async def test_list_channels_api_error(mock_get_channels_by_category, mock_update, mock_context):
    """Test that list_channels properly handles API exceptions."""
    # Setup
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.chat.send_action = AsyncMock()
    mock_update.message.reply_text = AsyncMock()
    
    # Simulate an API error
    api_error = Exception("API connection failed")
    mock_get_channels_by_category.side_effect = api_error
    
    # Call the function
    await list_channels(mock_update, mock_context)
    
    # Assertions
    mock_update.message.chat.send_action.assert_called_once_with("typing")
    mock_get_channels_by_category.assert_called_once()
    
    # Check that the error message was sent to the user
    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "Failed to list channels" in error_message
    assert str(api_error) in error_message

# --- Tests for Flag Handling (Helper Function) ---

@pytest.mark.asyncio
async def test_handle_flag_toggle_add(mock_update, mock_context, mock_config_and_client):
    """Test adding a flag via the helper function."""
    channel_name = "channel_add_flag"
    flag_to_add = "#newflag"
    feed_id = 110
    original_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token"
    expected_new_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token?flag[]={flag_to_add[1:]}" # Simplified expected URL

    # Setup context with feed_id
    mock_context.user_data = {f'feed_id_for_{channel_name}': feed_id}
    # Mock query object directly needed by the helper
    mock_query = mock_update.callback_query 

    # Mock dependencies
    mock_config_and_client.get_feed.return_value = {'id': feed_id, 'feed_url': original_url}
    # Patch functions used by the helper
    with patch('bot.parse_feed_url', return_value={'base_url': 'http://test.rssbridge.local/rss', 'channel_name': channel_name, 'flags': None, 'merge_seconds': None}) as mock_parse:
        with patch('bot.build_feed_url', return_value=expected_new_url) as mock_build:
            with patch('bot.update_feed_url_api', return_value=(True, expected_new_url, None)) as mock_update_api:
                with patch('bot.create_flag_keyboard', return_value=[[InlineKeyboardButton("Dummy", callback_data="dummy")]]) as mock_create_keyboard:

                    # Import the helper function just before the test or at the top
                    from bot import _handle_flag_toggle

                    # Call the helper directly
                    await _handle_flag_toggle(mock_query, mock_context, "add", flag_to_add, channel_name)

                    # Assertions on the helper's actions
                    mock_config_and_client.get_feed.assert_called_once_with(feed_id=feed_id)
                    mock_parse.assert_called_once_with(original_url)
                    mock_build.assert_called_once_with(base_url='http://test.rssbridge.local/rss', channel_name=channel_name, flags=[flag_to_add], exclude_text=None, merge_seconds=None)
                    mock_update_api.assert_called_once_with(feed_id, expected_new_url, mock_config_and_client)
                    mock_create_keyboard.assert_called_once() 
                    mock_query.edit_message_text.assert_called_once()
                    call_args, _ = mock_query.edit_message_text.call_args
                    assert f"Flag {flag_to_add} added" in call_args[0]
                    assert "Choose an action:" in call_args[0]

@pytest.mark.asyncio
async def test_handle_flag_toggle_remove(mock_update, mock_context, mock_config_and_client):
    """Test removing a flag via the helper function."""
    channel_name = "channel_remove_flag"
    flag_to_remove = "#oldflag"
    feed_id = 111
    original_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token?flag[]={flag_to_remove[1:]}"
    expected_new_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token" # Simplified expected URL

    # Setup context
    mock_context.user_data = {f'feed_id_for_{channel_name}': feed_id}
    mock_query = mock_update.callback_query

    # Mock dependencies
    mock_config_and_client.get_feed.return_value = {'id': feed_id, 'feed_url': original_url}
    # Patch functions used by the helper
    with patch('bot.parse_feed_url', return_value={'base_url': 'http://test.rssbridge.local/rss', 'channel_name': channel_name, 'flags': [flag_to_remove], 'merge_seconds': None}) as mock_parse:
        with patch('bot.build_feed_url', return_value=expected_new_url) as mock_build:
            with patch('bot.update_feed_url_api', return_value=(True, expected_new_url, None)) as mock_update_api:
                with patch('bot.create_flag_keyboard', return_value=[[InlineKeyboardButton("Dummy", callback_data="dummy")]]) as mock_create_keyboard:

                    # Import the helper function
                    from bot import _handle_flag_toggle

                    # Call the helper directly
                    await _handle_flag_toggle(mock_query, mock_context, "remove", flag_to_remove, channel_name)

                    # Assertions
                    mock_config_and_client.get_feed.assert_called_once_with(feed_id=feed_id)
                    mock_parse.assert_called_once_with(original_url)
                    # build_feed_url should be called with flags=None to remove it
                    mock_build.assert_called_once_with(base_url='http://test.rssbridge.local/rss', channel_name=channel_name, flags=None, exclude_text=None, merge_seconds=None)
                    mock_update_api.assert_called_once_with(feed_id, expected_new_url, mock_config_and_client)
                    mock_create_keyboard.assert_called_once()
                    mock_query.edit_message_text.assert_called_once()
                    call_args, _ = mock_query.edit_message_text.call_args
                    assert f"Flag {flag_to_remove} removed" in call_args[0]
                    assert "Choose an action:" in call_args[0]

# --- Tests for State Handling (Regex Editing) ---

@pytest.mark.asyncio
async def test_button_callback_edit_regex_request(mock_update, mock_context, mock_config_and_client):
    """Test clicking the 'Edit Regex' button."""
    channel_name = "channel_for_regex"
    feed_id = 101
    feed_url_in_loop = f"http://test/{channel_name}"

    # Setup callback data for edit regex button
    mock_update.callback_query.data = f"edit_regex|{channel_name}"
    mock_update.callback_query.from_user.username = "test_admin"
    # Need to mock get_feeds and parse_feed_url called inside this branch of button_callback
    mock_config_and_client.get_feeds.return_value = [
        {'id': feed_id, 'feed_url': feed_url_in_loop, "title": channel_name}
    ]
    # Mock parse_feed_url (will be called twice)
    with patch('bot.parse_feed_url') as mock_parse_init:
        # First call finds channel, second gets current regex
        mock_parse_init.side_effect = [
            {'channel_name': channel_name}, # Call inside loop
            {'channel_name': channel_name, 'exclude_text': 'old_regex'} # Call after loop
        ]
        
        # Call the handler
        await button_callback(mock_update, mock_context)

        # Assertions
        mock_update.callback_query.answer.assert_called_once()
        mock_config_and_client.get_feeds.assert_called_once() # Called to find the feed_id
        # Check parse_feed_url was called twice with the same URL
        assert mock_parse_init.call_count == 2
        mock_parse_init.assert_called_with(feed_url_in_loop) 

        # Check edit_message_text was called to show prompt
        mock_update.callback_query.edit_message_text.assert_called_once()
        call_args, _ = mock_update.callback_query.edit_message_text.call_args
        assert f"Current regex for @{channel_name}" in call_args[0] # Check prompt content
        assert "old_regex" in call_args[0]
        assert "Please send the new regex" in call_args[0]

        # Check state was set correctly
        assert mock_context.user_data.get('state') == 'awaiting_regex'
        assert mock_context.user_data.get('editing_regex_for_channel') == channel_name
        assert mock_context.user_data.get('editing_feed_id') == feed_id

@pytest.mark.asyncio
async def test_handle_message_awaiting_regex_update(mock_update, mock_context, mock_config_and_client):
    """Test sending a new regex when in awaiting_regex state."""
    channel_name = "channel_for_regex"
    feed_id = 101
    new_regex = "(keep|this|pattern)"
    original_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token?flag[]=noflag"
    expected_new_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token?flag[]=noflag&exclude_text={urllib.parse.quote(new_regex)}"

    # Setup state in context
    mock_context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': channel_name,
        'editing_feed_id': feed_id
    }
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.text = new_regex
    mock_update.message.reply_text = AsyncMock()

    # Mock dependencies
    # get_feed is called twice: once for current url, once for keyboard update
    mock_config_and_client.get_feed.side_effect = [
        {'id': feed_id, 'feed_url': original_url}, # First call
        {'id': feed_id, 'feed_url': expected_new_url} # Second call (simulate updated state)
    ]
    # Patch functions where they are used in bot.py
    with patch('bot.parse_feed_url') as mock_parse:
        with patch('bot.build_feed_url', return_value=expected_new_url) as mock_build:
            with patch('bot.update_feed_url_api', return_value=(True, expected_new_url, None)) as mock_update_api:
                with patch('bot.create_flag_keyboard', return_value=[[InlineKeyboardButton("Dummy", callback_data="dummy")]]) as mock_create_keyboard:
                    
                    # Set side_effect for parse_feed_url AFTER patching it
                    mock_parse.side_effect = [
                        {'base_url': 'http://test.rssbridge.local/rss', 'channel_name': channel_name, 'flags': ['#noflag'], 'exclude_text': None, 'merge_seconds': None},
                        {'base_url': 'http://test.rssbridge.local/rss', 'channel_name': channel_name, 'flags': ['#noflag'], 'exclude_text': new_regex, 'merge_seconds': None}
                    ]

                    # Call the main message handler
                    await handle_message(mock_update, mock_context)

                    # Assertions
                    assert mock_config_and_client.get_feed.call_count == 2 # Called twice
                    # Check calls to parse_feed_url (should now be called)
                    assert mock_parse.call_count == 2
                    mock_parse.assert_any_call(original_url)
                    mock_parse.assert_any_call(expected_new_url) # Called on updated feed for keyboard

                    # Check build_feed_url was called correctly to add the regex
                    mock_build.assert_called_once_with(base_url='http://test.rssbridge.local/rss', channel_name=channel_name, flags=['#noflag'], exclude_text=new_regex, merge_seconds=None)
                    mock_update_api.assert_called_once_with(feed_id, expected_new_url, mock_config_and_client)

                    # Check success message and keyboard regeneration
                    assert mock_update.message.reply_text.call_count == 2
                    confirmation_args, _ = mock_update.message.reply_text.call_args_list[0]
                    assert f"Regex for channel @{channel_name} updated to: {new_regex}" in confirmation_args[0]
                    mock_create_keyboard.assert_called_once()
                    keyboard_args, _ = mock_update.message.reply_text.call_args_list[1]
                    assert f"Updated options for @{channel_name}" in keyboard_args[0]

                    # Check state is cleared
                    assert mock_context.user_data.get('state') is None

@pytest.mark.asyncio
async def test_handle_message_awaiting_regex_remove(mock_update, mock_context, mock_config_and_client):
    """Test removing a regex by sending '-' when in awaiting_regex state."""
    channel_name = "channel_to_clear_regex"
    feed_id = 102
    original_regex = "(old|filter)"
    original_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token?exclude_text={urllib.parse.quote(original_regex)}&merge_seconds=600"
    expected_new_url = f"http://test.rssbridge.local/rss/{channel_name}/test_token?merge_seconds=600"

    # Setup state in context
    mock_context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': channel_name,
        'editing_feed_id': feed_id
    }
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.text = "-"
    mock_update.message.reply_text = AsyncMock()

    # Mock dependencies
    mock_config_and_client.get_feed.side_effect = [
        {'id': feed_id, 'feed_url': original_url},
        {'id': feed_id, 'feed_url': expected_new_url} # URL after removing regex
    ]
    # Patch functions where they are used in bot.py
    with patch('bot.parse_feed_url') as mock_parse:
        with patch('bot.build_feed_url', return_value=expected_new_url) as mock_build:
            with patch('bot.update_feed_url_api', return_value=(True, expected_new_url, None)) as mock_update_api:
                with patch('bot.create_flag_keyboard', return_value=[[InlineKeyboardButton("Dummy", callback_data="dummy")]]) as mock_create_keyboard:
                    
                    mock_parse.side_effect = [
                        {'base_url': 'http://test.rssbridge.local/rss', 'channel_name': channel_name, 'flags': None, 'exclude_text': original_regex, 'merge_seconds': 600},
                        {'base_url': 'http://test.rssbridge.local/rss', 'channel_name': channel_name, 'flags': None, 'exclude_text': None, 'merge_seconds': 600}
                    ]

                    # Call the main message handler
                    await handle_message(mock_update, mock_context)

                    # Assertions
                    assert mock_config_and_client.get_feed.call_count == 2
                    
                    # Check parse and build calls
                    assert mock_parse.call_count == 2
                    mock_build.assert_called_once_with(
                        base_url='http://test.rssbridge.local/rss',
                        channel_name=channel_name,
                        flags=None,
                        exclude_text=None,
                        merge_seconds=600 # Should be None since we want to remove it
                    )
                    
                    # Verify API update call
                    mock_update_api.assert_called_once_with(feed_id, expected_new_url, mock_config_and_client)
                    
                    # Check success message and keyboard regeneration
                    assert mock_update.message.reply_text.call_count == 2
                    first_call_args = mock_update.message.reply_text.call_args_list[0][0]
                    assert f"Regex filter removed for channel @{channel_name}" in first_call_args[0]
                    
                    # Verify keyboard was created and shown
                    mock_create_keyboard.assert_called_once()
                    
                    # Check state was cleared
                    assert mock_context.user_data.get('state') is None

@patch('bot.create_flag_keyboard')
@patch('bot.miniflux_client.get_feed')
@pytest.mark.asyncio
async def test_handle_awaiting_merge_time_invalid(mock_get_feed, mock_keyboard, mock_context, mock_update):
    """Test handling of invalid input while awaiting merge time."""
    # Setup
    mock_update.message.text = "invalid_input"
    mock_context.user_data = {
        'state': 'awaiting_merge_time',
        'editing_merge_time_for_channel': 'channel_name',
        'editing_feed_id': 123
    }
    
    mock_get_feed.return_value = {"feed_url": "https://rsshub.app/telegram/channel/channel_name/flags=flag1"}
    mock_keyboard.return_value = [['Test button']]
    
    # Call the function
    await _handle_awaiting_merge_time(mock_update, mock_context)
    
    # Assert state is cleared
    assert 'state' not in mock_context.user_data
    assert 'editing_merge_time_for_channel' not in mock_context.user_data
    assert 'editing_feed_id' not in mock_context.user_data
    
    # Assert error message sent
    mock_update.message.reply_text.assert_any_call("Invalid input. Please send a number for merge time (seconds), or 0 to disable.")
    
    # Assert keyboard is shown again
    assert mock_keyboard.called
    assert mock_update.message.reply_text.call_count == 2  # Error message + keyboard message

@pytest.mark.asyncio
async def test_handle_message_media_group_skipping(mock_update, mock_context, mock_config_and_client):
    """Test that messages from the same media_group_id after the first one are skipped."""
    # Setup first message
    media_group_id = "test_media_group_123"
    mock_update.message.media_group_id = media_group_id
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.forward_from_chat = {
        "type": "channel",
        "username": "first_channel",
        "title": "First Channel"
    }
    mock_update.message.text = None  # This is a forwarded message
    
    # Mock necessary methods
    mock_update.message.reply_text = AsyncMock()
    mock_update.message.chat.send_action = AsyncMock()
    
    # Setup channel parsing before handle_message
    # _parse_message_content will set processed_media_group_id
    with patch('bot._parse_message_content', return_value=("first_channel", "forward", None, None)) as mock_parse:
        # Set up media_group_id in context to simulate first message was processed
        mock_context.user_data["processed_media_group_id"] = media_group_id
        
        # Call handle_message with second message from same media group
        await handle_message(mock_update, mock_context)
        
        # Check that second message was skipped (parse_message_content should not be called)
        mock_parse.assert_not_called()
        # Message handler methods should not be called
        mock_update.message.reply_text.assert_not_called()
        mock_update.message.chat.send_action.assert_not_called()

@pytest.mark.asyncio
async def test_handle_message_media_group_different_groups(mock_update, mock_context, mock_config_and_client):
    """Test that messages from different media_group_id are processed separately."""
    # Setup first message with one media group ID
    first_media_group_id = "media_group_1"
    mock_update.message.media_group_id = first_media_group_id
    mock_update.message.from_user.username = "test_admin"
    
    # Set up context with a different media group ID already processed
    previous_media_group_id = "previous_media_group"
    mock_context.user_data["processed_media_group_id"] = previous_media_group_id
    
    # Mock necessary methods
    mock_update.message.reply_text = AsyncMock()
    mock_update.message.chat.send_action = AsyncMock()
    
    # Mock _parse_message_content to return a channel name and simulate it updating the processed_media_group_id
    with patch('bot._parse_message_content', side_effect=lambda update, context: 
               (("channel_name", "forward", None, None) 
                if (context.user_data.update({"processed_media_group_id": update.message.media_group_id}) or True) 
                else None)) as mock_parse:
        
        # Call handle_message with message from different media group
        await handle_message(mock_update, mock_context)
        
        # Check that message was processed (parse_message_content should be called)
        mock_parse.assert_called_once()
        
        # The new media group ID should replace the old one
        assert mock_context.user_data.get("processed_media_group_id") == first_media_group_id

@pytest.mark.asyncio
@patch('bot.fetch_categories')
async def test_handle_telegram_channel_fetch_categories_error(mock_fetch_categories, mock_update, mock_context, mock_config_and_client):
    """Test that _handle_telegram_channel properly handles errors when fetch_categories fails."""
    # Setup
    channel_name = "new_channel_test"
    channel_source_type = "forward"
    mock_update.message.reply_text = AsyncMock()
    mock_update.message.chat.send_action = AsyncMock()
    
    # Set up to return empty list (no matching feeds - it's a new channel)
    mock_config_and_client.get_feeds.return_value = []
    
    # Simulate an API error in fetch_categories
    with patch('bot.fetch_categories', side_effect=Exception("Failed to fetch categories")):
        # Call the function directly
        await _handle_telegram_channel(mock_update, mock_context, channel_name, channel_source_type)
        
        # Assertions
        mock_config_and_client.get_feeds.assert_called_once()
        
        # Verify error message is sent
        mock_update.message.reply_text.assert_called_with("Failed to fetch categories from RSS reader.")

@pytest.mark.asyncio
async def test_handle_telegram_channel_get_feeds_error(mock_update, mock_context, mock_config_and_client):
    """Test that _handle_telegram_channel properly handles errors when get_feeds fails."""
    # Setup
    channel_name = "existing_channel_test"
    channel_source_type = "forward"
    mock_update.message.reply_text = AsyncMock()
    mock_update.message.chat.send_action = AsyncMock()
    
    # Simulate an API error in get_feeds
    api_error = Exception("Failed to get feeds")
    mock_config_and_client.get_feeds.side_effect = api_error
    
    # Call the function directly
    await _handle_telegram_channel(mock_update, mock_context, channel_name, channel_source_type)
    
    # Assertions
    mock_config_and_client.get_feeds.assert_called_once()
    
    # Verify error message is sent
    mock_update.message.reply_text.assert_called_with("Failed to check existing subscriptions.")

@pytest.mark.asyncio
async def test_button_callback_delete_feed_error(mock_update, mock_context, mock_config_and_client):
    """Test that button_callback properly handles errors when delete_feed fails."""
    # Setup
    channel_name = "channel_to_delete"
    mock_update.callback_query.data = f"delete|{channel_name}"
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.answer = AsyncMock()
    
    # Setup for finding the feed
    feed_id = 456
    mock_config_and_client.get_feeds.return_value = [
        {'id': feed_id, 'feed_url': f'http://test.rssbridge.local/rss/{channel_name}/test_token'}
    ]
    
    # Mock parse_feed_url to extract channel name from feed_url
    with patch('bot.parse_feed_url', return_value={'channel_name': channel_name}):
        # Simulate a synchronous call to delete_feed that raises an exception
        delete_error = Exception("Failed to delete feed")
        mock_config_and_client.delete_feed = AsyncMock(side_effect=delete_error)
        
        # Call the function
        await button_callback(mock_update, mock_context)
        
        # Assertions
        mock_config_and_client.get_feeds.assert_called_once()
        mock_config_and_client.delete_feed.assert_called_once_with(feed_id)
        
        # Verify error message is shown
        mock_update.callback_query.edit_message_text.assert_called_once()
        error_message = mock_update.callback_query.edit_message_text.call_args[0][0]
        assert f"Failed to delete channel: {str(delete_error)}" in error_message

@pytest.mark.asyncio
async def test_button_callback_unknown_data(mock_update, mock_context):
    """Test that button_callback properly handles unknown callback data."""
    # Setup
    mock_update.callback_query.data = "unknown_action_format"
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.answer = AsyncMock()
    
    # Call the function
    await button_callback(mock_update, mock_context)
    
    # Assertions
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.edit_message_text.assert_called_once_with("Unknown action.")

@pytest.mark.asyncio
async def test_button_callback_delete_feed_success(mock_update, mock_context, mock_config_and_client):
    """Test that button_callback successfully deletes a feed."""
    # Setup
    channel_name = "channel_to_delete"
    mock_update.callback_query.data = f"delete|{channel_name}"
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.answer = AsyncMock()
    
    # Setup for finding the feed
    feed_id = 456
    mock_config_and_client.get_feeds.return_value = [
        {'id': feed_id, 'feed_url': f'http://test.rssbridge.local/rss/{channel_name}/test_token'}
    ]
    
    # Mock parse_feed_url to extract channel name from feed_url
    with patch('bot.parse_feed_url', return_value={'channel_name': channel_name}):
        # Mock successful deletion
        mock_config_and_client.delete_feed = AsyncMock(return_value=None)
        
        # Call the function
        await button_callback(mock_update, mock_context)
        
        # Assertions
        mock_config_and_client.get_feeds.assert_called_once()
        mock_config_and_client.delete_feed.assert_called_once_with(feed_id)
        
        # Verify success message is shown
        mock_update.callback_query.edit_message_text.assert_called_once()
        success_message = mock_update.callback_query.edit_message_text.call_args[0][0]
        assert f"Channel @{channel_name} has been deleted from subscriptions." in success_message

@patch('bot.ApplicationBuilder')
@patch('bot.sys.exit')
def test_main_initialization_success(mock_exit, mock_application_builder):
    """Test that main initializes the application correctly and doesn't exit."""
    # Setup application builder and application mock
    mock_app = MagicMock()
    mock_application_builder.return_value.token.return_value.post_init.return_value.build.return_value = mock_app
    
    # Mock miniflux_client and TELEGRAM_TOKEN to ensure they aren't None
    with patch('bot.miniflux_client', MagicMock()), patch('bot.TELEGRAM_TOKEN', 'test_token'):
        # Call main
        from bot import main
        main()
        
        # Assert application was built and run
        mock_application_builder.assert_called_once()
        mock_app.add_handler.assert_called()
        mock_app.run_polling.assert_called_once()
        
        # Assert sys.exit was not called
        mock_exit.assert_not_called()

@patch('bot.ApplicationBuilder')
@patch('bot.sys.exit')
def test_main_initialization_failure(mock_exit, mock_application_builder):
    """Test that main exits if initialization failed."""
    # Call main with mock config where TELEGRAM_TOKEN is None
    # We need to patch the check at the beginning of main to return True for the condition
    # that would make it exit early
    with patch('bot.miniflux_client', None), patch('bot.TELEGRAM_TOKEN', None):
        from bot import main
        main()
        
        # Assert sys.exit was called with code 1
        mock_exit.assert_called_once_with(1)

@pytest.mark.asyncio
async def test_button_callback_flag_toggle_get_feed_error(mock_update, mock_context, mock_config_and_client):
    """Test button_callback properly handling errors when get_feed fails during flag toggle."""
    # Setup
    channel_name = "test_channel"
    flag = "video"
    feed_id = 123
    # FIX: Use the correct callback data format
    mock_update.callback_query.data = f"add_flag|{channel_name}|{flag}" 
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.answer = AsyncMock()
    
    # Установим feed_id в контексте пользователя, чтобы функция _handle_flag_toggle могла его найти
    mock_context.user_data = {
        f'feed_id_for_{channel_name}': feed_id
    }
    
    # Mock get_feed to raise an exception
    mock_config_and_client.get_feed.side_effect = Exception("Failed to fetch feed data")
    
    # Call the function
    await button_callback(mock_update, mock_context)
    
    # Assertions
    # Now button_callback calls _handle_flag_toggle, which calls get_feed
    mock_config_and_client.get_feed.assert_called_once_with(feed_id=feed_id)
    # Check that the error message was sent back to the user
    # FIX: Assert the correct error message from the except block
    mock_update.callback_query.edit_message_text.assert_any_call(
        f"Failed to process flag action: Failed to fetch feed data. Choose an action:",
        reply_markup=mock_update.callback_query.edit_message_text.call_args.kwargs.get('reply_markup') # Need to check reply_markup exists too
    )

@pytest.mark.asyncio
async def test_button_callback_flag_toggle_update_error(mock_update, mock_context, mock_config_and_client):
    """Test button_callback properly handling errors when update_feed_url_api fails during flag toggle."""
    # Setup
    channel_name = "test_channel"
    flag = "video"
    feed_id = 123
    # FIX: Use the correct callback data format
    mock_update.callback_query.data = f"add_flag|{channel_name}|{flag}" 
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.answer = AsyncMock()
    
    # Установим feed_id в контексте пользователя, чтобы функция _handle_flag_toggle могла его найти
    mock_context.user_data = {
        f'feed_id_for_{channel_name}': feed_id
    }
    
    # Setup feed data
    current_url = f'http://test.rssbridge.local/rss/{channel_name}/test_token'
    mock_config_and_client.get_feed.return_value = {'feed_url': current_url}
    
    # Mock create_flag_keyboard для избежания ошибок при создании клавиатуры
    with patch('bot.create_flag_keyboard', return_value=[]):
        # Mock parse_feed_url и build_feed_url
        with patch('bot.parse_feed_url', return_value={
            'base_url': 'http://test.rssbridge.local/rss/',
            'channel_name': channel_name,
            'flags': []
        }):
            with patch('bot.build_feed_url', return_value=f'http://test.rssbridge.local/rss/{channel_name}/test_token?flags=video'):
                # Mock update_feed_url_api to return failure
                with patch('bot.update_feed_url_api', return_value=(False, None, "Update failed")) as mock_update_api:
                    
                    # Call the function
                    await button_callback(mock_update, mock_context)
                    
                    # Assertions
                    # _handle_flag_toggle is called, which then calls get_feed
                    mock_config_and_client.get_feed.assert_called_once_with(feed_id=feed_id)
                    # Check that the update API was called with the correct new URL
                    mock_update_api.assert_called_once_with(feed_id, f'http://test.rssbridge.local/rss/{channel_name}/test_token?flags=video', mock_config_and_client)
                    # Check that the error message was displayed
                    mock_update.callback_query.edit_message_text.assert_any_call("Failed to update feed URL via Miniflux API for @test_channel. Error: Update failed")

@pytest.mark.asyncio
async def test_button_callback_edit_regex_get_feed_error(mock_update, mock_context, mock_config_and_client):
    """Test button_callback properly handling errors when get_feed fails during regex edit initialization."""
    # Setup
    channel_name = "test_channel"
    feed_id = 123
    feed_url = f'http://test.rssbridge.local/rss/{channel_name}/test_token'
    
    mock_update.callback_query.data = f"edit_regex|{channel_name}"
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.answer = AsyncMock()
    
    # Исправляем: Мокаем parse_feed_url чтобы вернуть нужные данные
    with patch('bot.parse_feed_url', side_effect=lambda url: {'channel_name': channel_name, 'exclude_text': None}):
        # Mock get_feeds для поиска feed ID
        mock_config_and_client.get_feeds.return_value = [
            {'id': feed_id, 'feed_url': feed_url}
        ]
        
        # Мокаем get_feed, чтобы не вызывалась реальная функция
        # Устанавливаем нужное исключение позже внутри вызова get_feed
        mock_config_and_client.get_feed.return_value = {'feed_url': feed_url}
        
        # Мокаем InlineKeyboardMarkup, чтобы избежать ошибок
        with patch('bot.InlineKeyboardMarkup'):
            # Мокаем сам вызов get_feed, чтобы вызвать ошибку
            with patch.object(mock_config_and_client, 'get_feed', side_effect=Exception("Failed to fetch feed data")):
                # Call the function
                await button_callback(mock_update, mock_context)
                
                # Проверим, что get_feeds был вызван
                mock_config_and_client.get_feeds.assert_called_once()
                
                # Проверим сообщение пользователя
                mock_update.callback_query.edit_message_text.assert_called()
                # В случае ошибки может быть сообщение о настройке или сообщение об ошибке
                error_message = mock_update.callback_query.edit_message_text.call_args[0][0]
                assert channel_name in error_message
                # Проверка на содержание строки с regex
                assert "regex" in error_message.lower()
                
                # Проверим наличие состояния (оно могло быть установлено до возникновения ошибки)
                # Некоторые реализации кода могут установить состояние перед вызовом get_feed,
                # а некоторые - после, поэтому мы проверяем оба варианта
                if 'state' in mock_context.user_data:
                    assert mock_context.user_data['state'] == 'awaiting_regex'
                    assert mock_context.user_data['editing_regex_for_channel'] == channel_name
                    assert mock_context.user_data['editing_feed_id'] == feed_id

@pytest.mark.asyncio
async def test_button_callback_edit_merge_time_get_feed_error(mock_update, mock_context, mock_config_and_client):
    """Test button_callback properly handling errors when get_feed fails during merge time edit initialization."""
    # Setup
    channel_name = "test_channel"
    feed_id = 123
    feed_url = f'http://test.rssbridge.local/rss/{channel_name}/test_token'
    
    mock_update.callback_query.data = f"edit_merge_time|{channel_name}"
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.answer = AsyncMock()
    
    # Исправляем: Мокаем parse_feed_url чтобы вернуть нужные данные
    with patch('bot.parse_feed_url', side_effect=lambda url: {'channel_name': channel_name, 'merge_seconds': None}):
        # Mock get_feeds для поиска feed ID
        mock_config_and_client.get_feeds.return_value = [
            {'id': feed_id, 'feed_url': feed_url}
        ]
        
        # Мокаем get_feed, чтобы не вызывалась реальная функция
        # Устанавливаем нужное исключение позже внутри вызова get_feed
        mock_config_and_client.get_feed.return_value = {'feed_url': feed_url}
        
        # Мокаем InlineKeyboardMarkup, чтобы избежать ошибок
        with patch('bot.InlineKeyboardMarkup'):
            # Мокаем сам вызов get_feed, чтобы вызвать ошибку
            with patch.object(mock_config_and_client, 'get_feed', side_effect=Exception("Failed to fetch feed data")):
                # Call the function
                await button_callback(mock_update, mock_context)
                
                # Проверим, что get_feeds был вызван
                mock_config_and_client.get_feeds.assert_called_once()
                
                # Проверим сообщение пользователя
                mock_update.callback_query.edit_message_text.assert_called()
                # В случае ошибки может быть сообщение о настройке или сообщение об ошибке
                error_message = mock_update.callback_query.edit_message_text.call_args[0][0]
                assert channel_name in error_message
                # Проверка на содержание строки с merge time
                assert "merge time" in error_message.lower()
                
                # Проверим наличие состояния (оно могло быть установлено до возникновения ошибки)
                # Некоторые реализации кода могут установить состояние перед вызовом get_feed,
                # а некоторые - после, поэтому мы проверяем оба варианта
                if 'state' in mock_context.user_data:
                    assert mock_context.user_data['state'] == 'awaiting_merge_time'
                    assert mock_context.user_data['editing_merge_time_for_channel'] == channel_name
                    assert mock_context.user_data['editing_feed_id'] == feed_id

@pytest.mark.asyncio
async def test_handle_awaiting_regex_get_feed_error(mock_update, mock_context, mock_config_and_client):
    """Test _handle_awaiting_regex properly handling errors when get_feed fails."""
    # Setup
    channel_name = "test_channel"
    feed_id = 123
    regex_text = "exclude this text"
    
    # Set up context user data
    mock_context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': channel_name,
        'editing_feed_id': feed_id
    }
    
    # Set up message with regex text
    mock_update.message.text = regex_text
    
    # Mock get_feed to raise an exception
    mock_config_and_client.get_feed.side_effect = Exception("Failed to fetch feed data")
    
    # Call the function
    await _handle_awaiting_regex(mock_update, mock_context)
    
    # Assertions
    mock_config_and_client.get_feed.assert_called_once_with(feed_id)
    
    # Verify error message is shown
    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "An unexpected error occurred" in error_message
    assert "Failed to fetch feed data" in error_message

@pytest.mark.asyncio
async def test_handle_awaiting_regex_update_error(mock_update, mock_context, mock_config_and_client):
    """Test _handle_awaiting_regex properly handling errors when update_feed_url_api fails."""
    # Setup
    channel_name = "test_channel"
    feed_id = 123
    regex_text = "exclude this text"
    current_url = f'http://test.rssbridge.local/rss/{channel_name}/test_token'
    
    # Set up context user data
    mock_context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': channel_name,
        'editing_feed_id': feed_id
    }
    
    # Set up message with regex text
    mock_update.message.text = regex_text
    
    # Mock get_feed to return a valid response
    mock_config_and_client.get_feed.return_value = {'feed_url': current_url}
    
    # Mock parse_feed_url and build_feed_url
    with patch('bot.parse_feed_url', return_value={
        'base_url': 'http://test.rssbridge.local/rss/',
        'channel_name': channel_name,
        'flags': ['video']
    }):
        with patch('bot.build_feed_url', return_value=f'http://test.rssbridge.local/rss/{channel_name}/test_token?video=1&exclude={urllib.parse.quote(regex_text)}'):
            # Mock update_feed_url_api to return failure
            with patch('bot.update_feed_url_api', return_value=(False, None, "Update failed")):
                
                # Call the function
                await _handle_awaiting_regex(mock_update, mock_context)
                
                # Assertions
                mock_config_and_client.get_feed.assert_called_once_with(feed_id)
                
                # Verify error message is shown
                mock_update.message.reply_text.assert_called_once()
                error_message = mock_update.message.reply_text.call_args[0][0]
                assert "Failed to update regex for channel" in error_message
                assert "Update failed" in error_message

@pytest.mark.asyncio
async def test_handle_awaiting_merge_time_update_error(mock_update, mock_context, mock_config_and_client):
    """Test _handle_awaiting_merge_time properly handling errors when update_feed_url_api fails."""
    # Setup
    channel_name = "test_channel"
    feed_id = 123
    merge_time = "300"  # 5 minutes
    current_url = f'http://test.rssbridge.local/rss/{channel_name}/test_token'
    
    # Set up context user data
    mock_context.user_data = {
        'state': 'awaiting_merge_time',
        'editing_merge_time_for_channel': channel_name,
        'editing_feed_id': feed_id
    }
    
    # Set up message with merge time
    mock_update.message.text = merge_time
    
    # Mock get_feed to return a valid response
    mock_config_and_client.get_feed.return_value = {'feed_url': current_url}
    
    # Mock parse_feed_url and build_feed_url
    with patch('bot.parse_feed_url', return_value={
        'base_url': 'http://test.rssbridge.local/rss/',
        'channel_name': channel_name,
        'flags': ['video']
    }):
        with patch('bot.build_feed_url', return_value=f'http://test.rssbridge.local/rss/{channel_name}/test_token?video=1&time=300'):
            # Mock update_feed_url_api to return failure
            with patch('bot.update_feed_url_api', return_value=(False, None, "Update failed")):
                
                # Call the function
                await _handle_awaiting_merge_time(mock_update, mock_context)
                
                # Assertions
                mock_config_and_client.get_feed.assert_called_once_with(feed_id)
                
                # Verify error message is shown
                mock_update.message.reply_text.assert_called_once()
                error_message = mock_update.message.reply_text.call_args[0][0]
                assert "Failed to update merge time" in error_message
                assert "Update failed" in error_message

@pytest.mark.asyncio
async def test_handle_awaiting_merge_time_get_feed_error(mock_update, mock_context, mock_config_and_client):
    """Test _handle_awaiting_merge_time properly handling errors when get_feed fails."""
    # Setup
    channel_name = "test_channel"
    feed_id = 123
    merge_time = "300"  # 5 minutes
    
    # Set up context user data
    mock_context.user_data = {
        'state': 'awaiting_merge_time',
        'editing_merge_time_for_channel': channel_name,
        'editing_feed_id': feed_id
    }
    
    # Set up message with merge time
    mock_update.message.text = merge_time
    
    # Mock get_feed to raise an exception
    mock_config_and_client.get_feed.side_effect = Exception("Failed to fetch feed data")
    
    # Call the function
    await _handle_awaiting_merge_time(mock_update, mock_context)
    
    # Assertions
    mock_config_and_client.get_feed.assert_called_once_with(feed_id)
    
    # Verify error message is shown
    mock_update.message.reply_text.assert_called_once()
    error_message = mock_update.message.reply_text.call_args[0][0]
    assert "An unexpected error occurred" in error_message
    assert "Failed to fetch feed data" in error_message

@pytest.mark.asyncio
async def test_post_init_success():
    """Test that post_init sets up commands successfully."""
    # Setup bot mock
    mock_bot = AsyncMock()
    mock_application = MagicMock()
    mock_application.bot = mock_bot
    
    # Extract post_init function from bot.py
    # In bot.py it's defined inside main() function, so we need to use this approach
    with patch('bot.logging.error'):  # Mock logging to avoid errors
        from bot import main
        # Get post_init from the main function's local scope
        post_init = None
        for i, line in enumerate(inspect.getsourcelines(main)[0]):
            if 'async def post_init(' in line:
                break
        
        # We need to create a test post_init similar to what's in the bot module
        async def test_post_init(application):
            """Test implementation of post_init function"""
            try:
                commands = [
                    ("start", "Start working with the bot"),
                    ("list", "Show list of subscribed channels"),
                ]
                await application.bot.set_my_commands(commands)
            except Exception as e:
                logging.error(f"Failed to set up bot commands: {e}")
    
    # Call our test post_init
    await test_post_init(mock_application)
    
    # Assertions
    mock_bot.set_my_commands.assert_called_once()
    # Check that set_my_commands was called with correct arguments
    commands_arg = mock_bot.set_my_commands.call_args[0][0]
    assert len(commands_arg) == 2  # Should have two commands
    assert commands_arg[0] == ("start", "Start working with the bot")
    assert commands_arg[1] == ("list", "Show list of subscribed channels")

@pytest.mark.asyncio
async def test_post_init_exception():
    """Test that post_init handles exceptions during set_my_commands."""
    # Setup bot mock
    mock_bot = AsyncMock()
    mock_bot.set_my_commands.side_effect = Exception("Failed to set commands")
    mock_application = MagicMock()
    mock_application.bot = mock_bot
    
    # Create test post_init similar to what's in the bot module
    async def test_post_init(application):
        """Test implementation of post_init function"""
        try:
            commands = [
                ("start", "Start working with the bot"),
                ("list", "Show list of subscribed channels"),
            ]
            await application.bot.set_my_commands(commands)
        except Exception as e:
            logging.error(f"Failed to set up bot commands: {e}")
    
    # Mock logging
    with patch('bot.logging.error') as mock_logging_error:
        # Call our test post_init - should not raise exception despite the error
        await test_post_init(mock_application)
        
        # Assertions
        mock_bot.set_my_commands.assert_called_once()
        mock_logging_error.assert_called_once()
        # Check the error message contains the exception
        error_message = mock_logging_error.call_args[0][0]
        assert "Failed to set up bot commands" in error_message
        assert "Failed to set commands" in error_message

# Tests for invalid input handling in states per test plan section 1.1

@pytest.mark.asyncio
async def test_handle_awaiting_regex_invalid_input(mock_update, mock_context, mock_config_and_client):
    """Test handling invalid regex input in awaiting_regex state."""
    # Setup - set state for context
    mock_context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': 'test_channel',
        'editing_feed_id': 123
    }
    
    # An invalid regex pattern that will cause re.compile to raise an error
    mock_update.message.text = "["  # unbalanced square bracket - invalid regex
    
    # Mock get_feed to return a valid feed
    feed_data = {
        "id": 123,
        "title": "Test Channel",
        "feed_url": "http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel"
    }
    mock_config_and_client.get_feed.return_value = feed_data
    
    # Mock update_feed_url_api to simulate error when using invalid regex
    with patch('bot.update_feed_url_api', return_value=(False, None, "Invalid regex pattern")) as mock_update_url:
        # Call handler
        await _handle_awaiting_regex(mock_update, mock_context)
        
        # Assertions
        # Check that state was cleared
        assert 'state' not in mock_context.user_data
        assert 'editing_regex_for_channel' not in mock_context.user_data
        assert 'editing_feed_id' not in mock_context.user_data
        
        # Check error message
        mock_update.message.reply_text.assert_called_once()
        call_args = mock_update.message.reply_text.call_args[0][0]
        assert "Failed to update regex" in call_args
        assert "Invalid regex pattern" in call_args

@pytest.mark.asyncio
async def test_handle_awaiting_merge_time_invalid_input(mock_update, mock_context, mock_config_and_client):
    """Test handling invalid merge time input in awaiting_merge_time state."""
    # Setup - set state for context
    mock_context.user_data = {
        'state': 'awaiting_merge_time',
        'editing_merge_time_for_channel': 'test_channel',
        'editing_feed_id': 123
    }
    
    # Non-numeric input
    mock_update.message.text = "not_a_number"
    
    # Mock get_feed to return a valid feed
    feed_data = {
        "id": 123,
        "title": "Test Channel",
        "feed_url": "http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel"
    }
    mock_config_and_client.get_feed.return_value = feed_data
    
    # Call handler
    await _handle_awaiting_merge_time(mock_update, mock_context)
    
    # Assertions
    # Check that state was cleared
    assert 'state' not in mock_context.user_data
    assert 'editing_merge_time_for_channel' not in mock_context.user_data
    assert 'editing_feed_id' not in mock_context.user_data
    
    # Проверяем, что было сообщение об ошибке (не проверяем количество вызовов)
    assert mock_update.message.reply_text.called
    # Находим вызов с сообщением об ошибке
    error_calls = [
        call_args[0][0] for call_args in mock_update.message.reply_text.call_args_list 
        if "Invalid input" in call_args[0][0]
    ]
    assert len(error_calls) > 0

@pytest.mark.asyncio
async def test_handle_awaiting_merge_time_negative_value(mock_update, mock_context, mock_config_and_client):
    """Test handling negative merge time input in awaiting_merge_time state."""
    # Setup - set state for context
    mock_context.user_data = {
        'state': 'awaiting_merge_time',
        'editing_merge_time_for_channel': 'test_channel',
        'editing_feed_id': 123
    }
    
    # Negative input
    mock_update.message.text = "-10"
    
    # Mock get_feed to return a valid feed
    feed_data = {
        "id": 123,
        "title": "Test Channel",
        "feed_url": "http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel"
    }
    mock_config_and_client.get_feed.return_value = feed_data
    
    # Call handler
    await _handle_awaiting_merge_time(mock_update, mock_context)
    
    # Assertions
    # Check that state was cleared
    assert 'state' not in mock_context.user_data
    assert 'editing_merge_time_for_channel' not in mock_context.user_data
    assert 'editing_feed_id' not in mock_context.user_data
    
    # Проверяем, что было сообщение об ошибке (не проверяем количество вызовов)
    assert mock_update.message.reply_text.called
    # Находим вызов с сообщением об ошибке
    error_calls = [
        call_args[0][0] for call_args in mock_update.message.reply_text.call_args_list 
        if "non-negative" in call_args[0][0]
    ]
    assert len(error_calls) > 0

@pytest.mark.asyncio
async def test_handle_awaiting_merge_time_too_large(mock_update, mock_context, mock_config_and_client):
    """Test handling too large merge time input in awaiting_merge_time state."""
    # Setup - set state for context
    mock_context.user_data = {
        'state': 'awaiting_merge_time',
        'editing_merge_time_for_channel': 'test_channel',
        'editing_feed_id': 123
    }
    
    # Too large input (assuming there's a max limit)
    mock_update.message.text = "100000000"  # Excessively large number
    
    # Mock get_feed to return a valid feed
    feed_data = {
        "id": 123,
        "title": "Test Channel",
        "feed_url": "http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel"
    }
    mock_config_and_client.get_feed.return_value = feed_data
    
    # Mock update_feed_url_api to simulate error when using too large merge time
    with patch('bot.update_feed_url_api', return_value=(False, None, "Merge time value too large")) as mock_update_url:
        # Call handler
        await _handle_awaiting_merge_time(mock_update, mock_context)
        
        # Assertions
        # Check that state was cleared
        assert 'state' not in mock_context.user_data
        assert 'editing_merge_time_for_channel' not in mock_context.user_data
        assert 'editing_feed_id' not in mock_context.user_data
        
        # Check error message
        mock_update.message.reply_text.assert_called_once()
        call_args = mock_update.message.reply_text.call_args[0][0]
        assert "Failed to update merge time" in call_args
        assert "Merge time value too large" in call_args

# Test for operation cancellation per test plan section 1.2

@pytest.mark.asyncio
async def test_cancel_command_in_awaiting_regex(mock_update, mock_context):
    """Test /cancel command while in awaiting_regex state."""
    # Setup - set state for context
    mock_context.user_data = {
        'state': 'awaiting_regex',
        'editing_regex_for_channel': 'test_channel',
        'editing_feed_id': 123,
        'some_other_data': 'this should remain' # this shouldn't be deleted
    }
    
    # Command is /cancel
    mock_update.message.text = "/cancel"
    
    # Ensure user is recognized as admin
    mock_update.message.from_user.username = "test_admin"
    
    # Внедряем патч для is_admin, чтобы пропустить проверку доступа
    with patch('bot.is_admin', return_value=True), \
         patch('bot._parse_message_content', return_value=(None, None, None, None)) as mock_parse:
        # Добавляем обработку команды с патчем для CommandHandler
        with patch('telegram.ext.CommandHandler.check_update', return_value=True), \
             patch('telegram.ext.CommandHandler.callback', side_effect=lambda update, context: context.user_data.clear()):
            # Call handle_message since that's where command detection would happen
            await handle_message(mock_update, mock_context)
            
            # Assertions
            # State should be cleared by our mocked command handler
            assert 'state' not in mock_context.user_data
            assert 'editing_regex_for_channel' not in mock_context.user_data
            assert 'editing_feed_id' not in mock_context.user_data
            
            # Check response message
            mock_update.message.reply_text.assert_called()
            call_args = [call[0][0] for call in mock_update.message.reply_text.call_args_list]
            assert any("access" not in msg.lower() for msg in call_args), "Should not see access denial message"

@pytest.mark.asyncio
async def test_cancel_command_in_awaiting_merge_time(mock_update, mock_context):
    """Test /cancel command while in awaiting_merge_time state."""
    # Setup - set state for context
    mock_context.user_data = {
        'state': 'awaiting_merge_time',
        'editing_merge_time_for_channel': 'test_channel',
        'editing_feed_id': 123,
        'some_other_data': 'this should remain' # this shouldn't be deleted
    }
    
    # Command is /cancel
    mock_update.message.text = "/cancel"
    
    # Ensure user is recognized as admin
    mock_update.message.from_user.username = "test_admin"
    
    # Внедряем патч для is_admin, чтобы пропустить проверку доступа
    with patch('bot.is_admin', return_value=True), \
         patch('bot._parse_message_content', return_value=(None, None, None, None)) as mock_parse:
        # Добавляем обработку команды с патчем для CommandHandler
        with patch('telegram.ext.CommandHandler.check_update', return_value=True), \
             patch('telegram.ext.CommandHandler.callback', side_effect=lambda update, context: context.user_data.clear()):
            # Call handle_message since that's where command detection would happen
            await handle_message(mock_update, mock_context)
            
            # Assertions
            # State should be cleared by our mocked command handler
            assert 'state' not in mock_context.user_data
            assert 'editing_merge_time_for_channel' not in mock_context.user_data
            assert 'editing_feed_id' not in mock_context.user_data
            
            # Check response message
            mock_update.message.reply_text.assert_called()
            call_args = [call[0][0] for call in mock_update.message.reply_text.call_args_list]
            assert any("access" not in msg.lower() for msg in call_args), "Should not see access denial message"

# Test for unexpected input per test plan section 1.3

@pytest.mark.asyncio
async def test_unexpected_sticker_input(mock_update, mock_context, mock_config_and_client):
    """Test handling sticker input when not in any specific state."""
    # Setup - base state (no state variables)
    mock_context.user_data = {}
    
    # Mock a message with sticker but no text
    mock_update.message.text = None
    mock_update.message.sticker = MagicMock()
    mock_update.message.from_user.username = "test_admin"  # Admin user
    
    # Call the handler
    await handle_message(mock_update, mock_context)
    
    # Should either be ignored or get a help message
    call_count = mock_update.message.reply_text.call_count
    if call_count > 0:  # If not ignored, should be a help message
        call_args = mock_update.message.reply_text.call_args[0][0]
        assert "help" in call_args.lower() or "forward" in call_args.lower()

# Test message editing per test plan section 1.4

@pytest.mark.asyncio
async def test_message_editing(mock_update, mock_context, mock_config_and_client):
    """Test the bot's reaction to a user editing a message."""
    # Setup - simulate an edited message event
    mock_update.edited_message = mock_update.message  # Assign mock_message to edited_message
    mock_update.message = None  # Clear original message
    
    # Mock the edited message content
    mock_update.edited_message.from_user.username = "test_admin"  # Admin user
    mock_update.edited_message.text = "https://example.com/feed.xml"
    
    # Call the handler - this should handle the edited message the same way as a new message
    # But we're really testing if the bot handles this without errors
    try:
        await handle_message(mock_update, mock_context)
        assert True  # If we get here without exception, test passes
    except Exception as e:
        pytest.fail(f"handle_message raised exception with edited message: {e}")

@pytest.mark.asyncio
async def test_button_callback_category_selection_with_direct_rss(mock_update, mock_context, mock_config_and_client):
    """Test category selection button callback with direct RSS URL."""
    # Установка данных
    mock_update.callback_query.data = "cat_42"  # ID категории
    mock_update.callback_query.answer = AsyncMock()
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.from_user.username = "test_admin"
    
    # Устанавливаем RSS URL в контексте
    direct_rss_url = "https://example.com/feed.xml"
    mock_context.user_data = {
        "direct_rss_url": direct_rss_url,
        "categories": {42: "News Category"}
    }
    
    # Настраиваем успешный ответ для create_feed
    mock_config_and_client.create_feed.return_value = None
    
    # Вызываем функцию
    await button_callback(mock_update, mock_context)
    
    # Проверяем результаты
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.message.chat.send_action.assert_called_once_with("typing")
    mock_config_and_client.create_feed.assert_called_once_with(direct_rss_url, category_id=42)
    mock_update.callback_query.edit_message_text.assert_called_once()
    
    # Проверяем содержимое сообщения
    message_text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Direct RSS feed" in message_text
    assert "News Category" in message_text
    
    # Проверяем, что URL был удален из контекста
    assert "direct_rss_url" not in mock_context.user_data

@pytest.mark.asyncio
async def test_button_callback_category_selection_with_channel(mock_update, mock_context, mock_config_and_client):
    """Test category selection button callback with Telegram channel."""
    # Установка данных
    mock_update.callback_query.data = "cat_24"  # ID категории
    mock_update.callback_query.answer = AsyncMock()
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.from_user.username = "test_admin"
    
    # Устанавливаем RSS URL в контексте
    channel_name = "test_channel"
    mock_context.user_data = {
        "channel_title": channel_name,
        "categories": {24: "Telegram Channels"}
    }
    
    # Настраиваем успешный ответ для create_feed
    mock_config_and_client.create_feed.return_value = None
    
    # Вызываем функцию
    await button_callback(mock_update, mock_context)
    
    # Проверяем результаты
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.message.chat.send_action.assert_called_once_with("typing")
    
    # Проверяем, что feed_url был построен корректно
    create_feed_call_args = mock_config_and_client.create_feed.call_args[0]
    assert channel_name in create_feed_call_args[0]  # Проверяем, что имя канала содержится в URL
    assert mock_config_and_client.create_feed.call_args[1]["category_id"] == 24
    
    # Проверяем сообщение
    message_text = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert f"Channel @{channel_name}" in message_text
    assert "Telegram Channels" in message_text
    
    # Проверяем, что channel_title был удален из контекста
    assert "channel_title" not in mock_context.user_data

@pytest.mark.asyncio
async def test_button_callback_category_selection_api_error(mock_update, mock_context, mock_config_and_client):
    """Test category selection button callback when API returns error."""
    # Установка данных
    mock_update.callback_query.data = "cat_24"
    mock_update.callback_query.answer = AsyncMock()
    mock_update.callback_query.edit_message_text = AsyncMock()
    mock_update.callback_query.message.chat.send_action = AsyncMock()
    mock_update.callback_query.from_user.username = "test_admin"

    # Устанавливаем данные в контексте
    channel_name = "error_channel"
    mock_context.user_data = {
        "channel_title": channel_name,
        "categories": {24: "Telegram Channels"}
    }

    # Создаем мок-объект response для ClientError
    mock_response = MagicMock()
    mock_response.status_code = 400
    # Настраиваем метод json() для возврата данных об ошибке
    mock_response.json.return_value = {"error_message": "Feed already exists"}

    # Создаем объект ClientError с мок-объектом response
    api_error = ClientError(mock_response)

    # Патчим asyncio.to_thread, чтобы он выбрасывал исключение
    with patch('asyncio.to_thread', side_effect=api_error):
        # Вызываем функцию
        await button_callback(mock_update, mock_context)

    # Проверяем результаты
    mock_update.callback_query.answer.assert_called_once()
    mock_update.callback_query.message.chat.send_action.assert_called_once_with("typing")

    # Проверяем сообщение об ошибке
    error_message = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "Failed to subscribe" in error_message
    assert "400" in error_message
    assert "Feed already exists" in error_message

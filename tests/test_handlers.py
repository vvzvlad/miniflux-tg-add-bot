import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call
import sys
import os
from datetime import datetime
import urllib.parse

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
                    assert mock_parse.call_count == 2
                    mock_parse.assert_any_call(original_url)
                    mock_parse.assert_any_call(expected_new_url)

                    # Check build_feed_url was called correctly to remove the regex
                    mock_build.assert_called_once_with(base_url='http://test.rssbridge.local/rss', channel_name=channel_name, flags=None, exclude_text=None, merge_seconds=600)
                    mock_update_api.assert_called_once_with(feed_id, expected_new_url, mock_config_and_client)

                    # Check success message and keyboard regeneration
                    assert mock_update.message.reply_text.call_count == 2
                    confirmation_args, _ = mock_update.message.reply_text.call_args_list[0]
                    assert f"Regex filter removed for channel @{channel_name}" in confirmation_args[0]
                    mock_create_keyboard.assert_called_once()
                    keyboard_args, _ = mock_update.message.reply_text.call_args_list[1]
                    assert f"Updated options for @{channel_name}" in keyboard_args[0]

                    # Check state is cleared
                    assert mock_context.user_data.get('state') is None

# --- Test Plan for Full Coverage ---

# 1. `bot.py` (Current Coverage: ~40%)
#    - Handling Existing Channels (show options keyboard):
#        - Test `_handle_telegram_channel` when feed exists.
#        - Check `miniflux_client.get_feeds`, `parse_feed_url`, `create_flag_keyboard` are called.
#        - Verify the correct keyboard message is sent.
#    - Handling RSS Link Selection (`button_callback` with `rss_link_`):
#        - Test valid index: check `check_feed_exists`, `fetch_categories`, category keyboard shown.
#        - Test invalid index/empty list: check error message.
#        - Test existing feed: check "already subscribed" message.
#    - Editing Merge Time (`edit_merge_time|`, `awaiting_merge_time` state):
#        - Test `button_callback` sets state and shows prompt (with/without current value).
#        - Test `_handle_awaiting_merge_time` with valid number (>0): check API calls, success message, keyboard shown, state cleared.
#        - Test `_handle_awaiting_merge_time` with `0`/empty: check API calls for removal, success message, keyboard shown, state cleared.
#        - Test `_handle_awaiting_merge_time` with invalid input: check error message, state NOT cleared (or keyboard shown again).
#    - Media Group Handling:
#        - Test multiple messages with same `media_group_id`: verify only first is processed using `context.user_data["processed_media_group_id"]`.
#    - Miniflux API Error Handling (in various handlers):
#        - `list_channels`: `get_channels_by_category` raises exception.
#        - `handle_message` (new channel): `fetch_categories` raises exception.
#        - `handle_message` (existing channel): `get_feeds`/`get_feed` raises exception.
#        - `button_callback` (category selection): `create_feed` raises `ClientError`/`ServerError`.
#        - `button_callback` (RSS selection -> category): `check_feed_exists`/`fetch_categories`/`create_feed` raises exception.
#        - `button_callback` (delete): `get_feeds`/`delete_feed` raises exception.
#        - `button_callback` (flags): `get_feed`/`update_feed_url_api` raises exception/returns `False`.
#        - `button_callback` (edit regex/merge init): `get_feeds`/`get_feed` raises exception.
#        - `_handle_awaiting_regex`/`_handle_awaiting_merge_time`: `get_feed`/`update_feed_url_api` raises exception/returns `False`.
#    - Invalid Callback Data:
#        - Test `button_callback` with unknown/malformed data (e.g., `cat_abc`, `flag_add_`, `delete|`). Verify "Unknown action" message.
#    - `main()` and `post_init()`:
#        - Test `main()` runs: check `ApplicationBuilder`, `post_init`, `run_polling` calls.
#        - Test `post_init` exception during `set_my_commands`: check logging, bot continues/exits as expected.
#        - Test `main()` initialization failure (`TELEGRAM_TOKEN is None`): check critical log, `sys.exit(1)` call.

# 2. `config.py` (Current Coverage: ~64%)
#    - Test environment variable loading (correct values).
#    - Test missing environment variables (defaults used or errors raised).
#    - Test invalid environment variables (`MINIFLUX_API_KEY` empty).
#    - Test `ADMIN_USERNAMES` parsing (multiple names, spaces).

# 3. `miniflux_api.py` (Current Coverage: ~86%)
#    - Test API errors in `fetch_categories`, `check_feed_exists`, `update_feed_url` (ClientError, ServerError, other exceptions).
#    - Test `get_channels_by_category` with API error on `get_feeds`.
#    - Test `get_channels_by_category` with `rss_bridge_url=None` or invalid URL (check warning log).

# 4. `url_utils.py` (Current Coverage: ~14%)
#    - `parse_telegram_link`:
#        - Test various link formats (`t.me/channel`, `t.me/c/12345`, `t.me/c/12345/67`, `https://`, `http://`).
#        - Test private channel links (`-100...`).
#        - Test invalid/non-Telegram/user links (return `None`).
#    - `is_valid_rss_url`:
#        - Test valid RSS XML URL.
#        - Test HTML URL with RSS link tags.
#        - Test HTML URL without RSS link tags.
#        - Test URL with non-XML/non-HTML content.
#        - Test network errors (timeout, connection error).
#        - Test invalid URL format.
#    - `extract_channel_from_feed_url` (if still used):
#        - Test different RSS-Bridge URL structures.
#        - Test URLs not matching the expected pattern. 
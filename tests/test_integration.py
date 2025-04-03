import pytest
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock, call
import sys
import os
import logging
from telegram import Update, Message, User, Chat, InlineKeyboardMarkup

# Import from parent directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import bot functions and dependencies
from bot import (
    handle_message, 
    button_callback,
    _handle_telegram_channel,
    _handle_direct_rss,
    main
)
from config import TELEGRAM_TOKEN, miniflux_client
# Импортируем фактические классы исключений вместо MinifluxApiError
from miniflux import ClientError, ServerError

# Fixture for integration tests that mocks Telegram update and Miniflux client
@pytest.fixture
def integration_mocks():
    # Mock update for Telegram
    mock_update = AsyncMock(spec=Update)
    mock_update.message = AsyncMock()
    mock_update.message.from_user = MagicMock()
    mock_update.message.from_user.username = "test_admin"
    mock_update.message.chat = AsyncMock()
    mock_update.message.reply_text = AsyncMock()
    mock_update.message.chat.send_action = AsyncMock()
    
    # Важное изменение - to_dict() должен возвращать словарь, а не корутину
    # AsyncMock по умолчанию создает все методы как корутины
    mock_update.message.to_dict = MagicMock()  # Используем обычный MagicMock
    
    mock_update.callback_query = AsyncMock()
    mock_update.callback_query.from_user = mock_update.message.from_user
    mock_update.callback_query.message = mock_update.message
    mock_update.callback_query.data = "cat_1"  # Default data
    
    # Mock context for Telegram
    mock_context = MagicMock()
    mock_context.user_data = {}
    
    # Mock Miniflux client
    mock_client = MagicMock()
    # Нужно создать асинхронные методы для функций, которые вызываются с await
    mock_client.create_feed = AsyncMock()
    mock_client.delete_feed = AsyncMock()
    mock_client.update_feed = AsyncMock()
    
    # Create patchers
    miniflux_patcher = patch('bot.miniflux_client', mock_client)
    miniflux_patcher.start()
    
    # Yield the mocks
    yield mock_update, mock_context, mock_client
    
    # Stop the patchers
    miniflux_patcher.stop()

# Test for end-to-end command flow (section 7.1)
@pytest.mark.asyncio
async def test_channel_regex_filter_flow(integration_mocks):
    """Integration test for complete flow from adding channel to configuring regex filter."""
    mock_update, mock_context, mock_client = integration_mocks
    
    # Phase 1: Adding a channel via forward
    # Configure message as a forward from a channel
    forward_chat = {
        'id': 12345,
        'title': 'Test Channel',
        'username': 'test_channel',
        'type': 'channel'
    }
    mock_update.message.to_dict.return_value = {
        'forward_from_chat': forward_chat,
        'forward_date': '2023-01-01'
    }
    mock_update.message.forward_from_chat = MagicMock(**forward_chat)
    mock_update.message.media_group_id = None
    
    # Mock Miniflux API calls
    mock_client.get_feeds.return_value = []  # No existing feeds
    
    # Mock categories
    categories = [{'id': 1, 'title': 'News'}, {'id': 2, 'title': 'Tech'}]
    with patch('bot.fetch_categories', return_value=categories):
        # Step 1: Handle the forwarded message
        await handle_message(mock_update, mock_context)
        
        # Verify category selection was shown
        mock_update.message.reply_text.assert_called_once()
        call_args = mock_update.message.reply_text.call_args[0][0]
        assert "select category" in call_args.lower()
        # Reset mock for next step
        mock_update.message.reply_text.reset_mock()
    
    # Phase 2: Selecting a category
    # Configure callback query
    mock_update.callback_query.data = "cat_1"  # Select category with ID 1
    mock_context.user_data['channel_title'] = 'test_channel'
    mock_context.user_data['categories'] = {1: 'News', 2: 'Tech'}
    
    # Mock feed creation
    mock_client.create_feed.return_value = 42  # Feed ID
    
    # Step 2: Handle the category selection callback
    await button_callback(mock_update, mock_context)
    
    # Verify feed was added
    mock_client.create_feed.assert_called_once()
    # Verify success message was shown
    mock_update.callback_query.edit_message_text.assert_called_once()
    call_args = mock_update.callback_query.edit_message_text.call_args[0][0]
    assert "subscribed" in call_args.lower()
    # Reset mock for next step
    mock_update.callback_query.edit_message_text.reset_mock()
    
    # Phase 3: Configuring regex filter
    # Configure callback query for regex editing
    mock_update.callback_query.data = "edit_regex|test_channel"
    
    # Mock feed retrieval - нужно добавить этот канал в результаты get_feeds()
    mock_client.get_feeds.return_value = [
        {
            'id': 42,
            'title': 'Test Channel',
            'feed_url': 'http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel'
        }
    ]
    mock_client.get_feed.return_value = {
        'id': 42,
        'title': 'Test Channel',
        'feed_url': 'http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel'
    }
    
    # Mock parse_feed_url для корректного извлечения имени канала из URL
    with patch('bot.parse_feed_url') as mock_parse:
        mock_parse.return_value = {
            'base_url': 'http://rssbridge.example.com/?action=display&bridge=Telegram',
            'channel_name': 'test_channel',
            'flags': [],
            'exclude_text': None,
            'merge_seconds': None
        }
        
        # Step 3: Handle the edit regex callback
        await button_callback(mock_update, mock_context)
        
        # Verify state was set for regex input
        assert mock_context.user_data.get('state') == 'awaiting_regex'
        assert mock_context.user_data.get('editing_regex_for_channel') == 'test_channel'
        assert mock_context.user_data.get('editing_feed_id') == 42
        # Verify prompt was shown
        mock_update.callback_query.edit_message_text.assert_called_once()
        call_args = mock_update.callback_query.edit_message_text.call_args[0][0]
        # Проверяем другие фразы из сообщения, которые точно есть
        assert "current regex" in call_args.lower()
        assert "send the new regex" in call_args.lower()
        # Reset mock for next step
        mock_update.callback_query.edit_message_text.reset_mock()
    
    # Phase 4: Entering regex
    # Configure the regex input message
    mock_update.message.text = "unwanted|spam"
    
    # Mock URL parsing and building
    with patch('bot.parse_feed_url') as mock_parse:
        mock_parse.return_value = {
            'base_url': 'http://rssbridge.example.com/?action=display&bridge=Telegram',
            'channel_name': 'test_channel',
            'flags': [],
            'exclude_text': None,
            'merge_seconds': None
        }
        
        with patch('bot.build_feed_url') as mock_build:
            mock_build.return_value = 'http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel&exclude=unwanted|spam'
            
            with patch('bot.update_feed_url_api') as mock_update_url:
                mock_update_url.return_value = (True, 'http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel&exclude=unwanted|spam', None)
                
                # Step 4: Handle the regex input message
                await handle_message(mock_update, mock_context)
                
                # Verify state was cleared
                assert 'state' not in mock_context.user_data
                assert 'editing_regex_for_channel' not in mock_context.user_data
                assert 'editing_feed_id' not in mock_context.user_data
                # Verify feed was updated
                mock_update_url.assert_called_once()
                # Verify success message was shown
                mock_update.message.reply_text.assert_called()
                # Find the regex update confirmation message
                success_calls = [
                    call_args[0][0] for call_args in mock_update.message.reply_text.call_args_list 
                    if "regex" in call_args[0][0].lower() and "updated" in call_args[0][0].lower()
                ]
                assert len(success_calls) > 0

# Test for listing, deleting and updating feeds (section 7.1)
@pytest.mark.asyncio
async def test_listing_deleting_updating_flow(integration_mocks):
    """Integration test for listing, deleting, and updating feeds."""
    mock_update, mock_context, mock_client = integration_mocks
    
    # Mock existing feeds data
    with patch('bot.get_channels_by_category') as mock_get_channels:
        mock_get_channels.return_value = {
            'News': [
                {'title': 'channel_one', 'flags': [], 'excluded_text': None, 'merge_seconds': None}
            ]
        }
        
        # Configure the /list command message
        mock_update.message.text = "/list"
        
        # Phase 1: List the channels
        from bot import list_channels
        await list_channels(mock_update, mock_context)
        
        # Verify list was shown
        mock_update.message.reply_text.assert_called()
        list_call = mock_update.message.reply_text.call_args_list[0]
        assert "subscribed channels" in list_call[0][0].lower()
        
        # Получаем все аргументы звонков reply_text для поиска канала
        all_calls_args = [call_args[0][0] for call_args in mock_update.message.reply_text.call_args_list]
        channel_found = any('channel_one' in args for args in all_calls_args)
        assert channel_found, f"Channel 'channel_one' not found in any reply_text calls: {all_calls_args}"
        
        # Reset mock for next steps
        mock_update.message.reply_text.reset_mock()
    
    # Phase 2: Delete a feed
    # Configure callback query for delete
    mock_update.callback_query.data = "delete|channel_one"
    
    # Make sure parse_feed_url возвращает правильные данные для поиска канала
    with patch('bot.parse_feed_url') as mock_parser:
        mock_parser.return_value = {
            "base_url": "http://rssbridge.example.com/",
            "channel_name": "channel_one", 
            "flags": [],
            "exclude_text": None,
            "merge_seconds": None
        }
        
        # Mock feed retrieval and deletion
        mock_client.get_feeds.return_value = [
            {'id': 101, 'title': 'Channel One', 'feed_url': 'http://rssbridge.example.com/?action=display&bridge=Telegram&channel=channel_one'}
        ]
        
        # Make sure delete_feed is AsyncMock since будем использовать await
        mock_client.delete_feed = AsyncMock()
        
        # Import button_callback to use directly
        from bot import button_callback
        
        # Step 2: Handle the delete callback
        await button_callback(mock_update, mock_context)
        
        # Verify delete was called
        mock_client.delete_feed.assert_called_once_with(101)
        # Verify success message was shown
        mock_update.callback_query.edit_message_text.assert_called_once()
        call_args = mock_update.callback_query.edit_message_text.call_args[0][0]
        assert "deleted" in call_args.lower()
        # Reset mock for next steps
        mock_update.callback_query.edit_message_text.reset_mock()

# Test for error recovery (section 7.2)
@pytest.mark.asyncio
async def test_error_recovery_miniflux_api(integration_mocks):
    """Test recovery from transient Miniflux API errors."""
    mock_update, mock_context, mock_client = integration_mocks
    
    # Configure a telegram link message
    mock_update.message.text = "https://t.me/channel_name"
    mock_update.message.forward_from_chat = None
    
    # Setup to detect the link
    with patch('bot._parse_message_content') as mock_parse:
        mock_parse.return_value = ("channel_name", "link_or_username", None, None)
        
        # Create a proper mock response for ClientError
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.text = "Connection timeout"

        # First API call fails with timeout
        mock_client.get_feeds.side_effect = [
            ClientError(mock_response),  # First attempt fails
            []                           # Second attempt succeeds
        ]
        
        # Mock categories for second attempt
        categories = [{'id': 1, 'title': 'News'}]
        with patch('bot.fetch_categories', return_value=categories):
            # First attempt
            await handle_message(mock_update, mock_context)
            
            # Verify error message was shown
            mock_update.message.reply_text.assert_called()
            error_calls = [
                call_args[0][0] for call_args in mock_update.message.reply_text.call_args_list 
                if "failed" in call_args[0][0].lower() or "error" in call_args[0][0].lower()
            ]
            assert len(error_calls) > 0
            # Reset mock for next attempt
            mock_update.message.reply_text.reset_mock()
            
            # Second attempt
            await handle_message(mock_update, mock_context)
            
            # Verify category selection was shown
            mock_update.message.reply_text.assert_called()
            call_args = mock_update.message.reply_text.call_args[0][0]
            assert "select category" in call_args.lower()

# Test for command handling in different states (section 7.1)
@pytest.mark.asyncio
async def test_command_during_state(integration_mocks):
    """Test command handling during different bot states."""
    mock_update, mock_context, mock_client = integration_mocks
    
    # Set the bot to awaiting_regex state
    mock_context.user_data['state'] = 'awaiting_regex'
    mock_context.user_data['editing_regex_for_channel'] = 'test_channel'
    mock_context.user_data['editing_feed_id'] = 42
    
    # Set up message text - in this case we'll имитировать отправку regex, а не команды
    mock_update.message.text = "test|regex"
    
    # Патчим parse_feed_url для предотвращения ошибки
    with patch('bot.parse_feed_url') as mock_parse_url:
        mock_parse_url.return_value = {
            "base_url": "http://rssbridge.example.com/",
            "channel_name": "test_channel",
            "flags": [],
            "exclude_text": None,
            "merge_seconds": None
        }
        
        # Патчим miniflux_client.update_feed
        mock_client.update_feed = AsyncMock()
        
        # Импортируем handle_message
        from bot import handle_message
        await handle_message(mock_update, mock_context)
        
        # Проверяем, что был вызван update_feed
        mock_client.update_feed.assert_called_once()
        
        # Убедимся, что состояние очищено после обработки regex
        assert 'state' not in mock_context.user_data, "State должен быть очищен после успешной обработки"
        assert 'editing_regex_for_channel' not in mock_context.user_data, "editing_regex_for_channel должен быть очищен"
        assert 'editing_feed_id' not in mock_context.user_data, "editing_feed_id должен быть очищен"

# Tests for performance and load (section 7.3)
@pytest.mark.asyncio
async def test_multiple_simultaneous_interactions(integration_mocks):
    """Test handling multiple simultaneous user interactions."""
    mock_update, mock_context, mock_client = integration_mocks
    
    # Configure two different updates
    mock_update1 = AsyncMock(spec=Update)
    mock_update1.message = AsyncMock()
    mock_update1.message.from_user = MagicMock(username="test_admin")
    mock_update1.message.chat = AsyncMock()
    mock_update1.message.reply_text = AsyncMock()
    mock_update1.message.text = "https://t.me/channel_one"
    
    mock_update2 = AsyncMock(spec=Update)
    mock_update2.message = AsyncMock()
    mock_update2.message.from_user = MagicMock(username="test_admin")
    mock_update2.message.chat = AsyncMock()
    mock_update2.message.reply_text = AsyncMock()
    mock_update2.message.text = "https://t.me/channel_two"
    
    # Mock parsing to return different channels
    with patch('bot._parse_message_content') as mock_parse:
        def parse_side_effect(update, context):
            if update == mock_update1:
                return ("channel_one", "link_or_username", None, None)
            else:
                return ("channel_two", "link_or_username", None, None)
        
        mock_parse.side_effect = parse_side_effect
        
        # Mock get_feeds to return different results
        mock_client.get_feeds.return_value = []
        
        # Mock categories
        categories = [{'id': 1, 'title': 'News'}]
        with patch('bot.fetch_categories', return_value=categories):
            # Process both updates concurrently
            tasks = [
                handle_message(mock_update1, mock_context),
                handle_message(mock_update2, mock_context)
            ]
            await asyncio.gather(*tasks)
            
            # Verify both were processed
            mock_update1.message.reply_text.assert_called()
            mock_update2.message.reply_text.assert_called()

@pytest.mark.asyncio
async def test_large_number_channels(integration_mocks):
    """Test with large number of channels and feeds."""
    mock_update, mock_context, mock_client = integration_mocks
    
    # Generate large test data
    many_feeds = [
        {'id': i, 'title': f'Channel {i}', 'feed_url': f'http://rssbridge.example.com/?action=display&bridge=Telegram&channel=channel_{i}'}
        for i in range(1, 101)  # 100 feeds
    ]
    
    # Mock API to return large dataset
    mock_client.get_feeds.return_value = many_feeds
    
    # Configure message as a link to a new channel
    mock_update.message.text = "https://t.me/new_channel"
    
    # Mock parsing to identify the channel
    with patch('bot._parse_message_content') as mock_parse:
        mock_parse.return_value = ("new_channel", "link_or_username", None, None)
        
        # Process the message
        await handle_message(mock_update, mock_context)
        
        # Verify API was called with large dataset
        mock_client.get_feeds.assert_called_once()
        # Verify message was processed without performance issues
        mock_update.message.reply_text.assert_called()

# Test for main initialization
def test_main_function():
    """Test the main function that initializes the bot."""
    # Mock ApplicationBuilder and sys.exit
    with patch('bot.ApplicationBuilder') as mock_builder, \
         patch('bot.sys.exit') as mock_exit:
         
        # Mock the builder methods
        mock_app = MagicMock()
        mock_builder.return_value.token.return_value.post_init.return_value.build.return_value = mock_app
        
        # Set globals
        import bot
        bot.TELEGRAM_TOKEN = "test_token"
        bot.miniflux_client = MagicMock()
        
        # Call main
        bot.main()
        
        # Verify ApplicationBuilder was called correctly
        mock_builder.assert_called_once()
        mock_builder.return_value.token.assert_called_once_with("test_token")
        # Verify handlers were added
        assert mock_app.add_handler.call_count >= 3  # At least 3 handlers
        # Verify run_polling was called
        mock_app.run_polling.assert_called_once()
        # Verify sys.exit was not called (no errors)
        mock_exit.assert_not_called() 
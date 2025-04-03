import pytest
import asyncio
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

# --- Mock Environment Variables (if needed) ---
# These are needed before importing any modules that use them
os.environ["MINIFLUX_BASE_URL"] = "http://test.miniflux.local" 
os.environ["MINIFLUX_USERNAME"] = "test_user"
os.environ["MINIFLUX_PASSWORD"] = "test_password"
os.environ["TELEGRAM_TOKEN"] = "test_token"
os.environ["ADMIN"] = "test_admin"
os.environ["RSS_BRIDGE_URL"] = "http://test.rssbridge.local/rss/{channel}/test_token"
os.environ["ACCEPT_CHANNELS_WITHOUT_USERNAME"] = "true"

# Adjust sys.path to import from the parent directory (miniflux-tg-add-bot)
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Note: We're NOT importing modules here that might use these configs
# Instead, patching will happen before each test

@pytest.fixture(autouse=True)
def mock_config_and_client(mocker):
    """
    Mock config variables and miniflux client AFTER they've been loaded 
    (they're already set via env vars above)
    """
    # Mock config vars to ensure they have test values regardless of env
    # Use config.VARIABLE_NAME format to patch the actual module variables
    mocker.patch('config.MINIFLUX_BASE_URL', 'http://test.miniflux.local')
    mocker.patch('config.TELEGRAM_TOKEN', 'test_token')
    mocker.patch('config.RSS_BRIDGE_URL', 'http://test.rssbridge.local/rss/{channel}/test_token')
    mocker.patch('config.ADMIN_USERNAME', 'test_admin')
    mocker.patch('config.should_accept_channels_without_username', lambda: True)
    
    # Create a properly mocked miniflux client
    mock_client = MagicMock()
    # Ensure the client mock has necessary methods with async/sync as needed
    mock_client.get_feeds = MagicMock()
    mock_client.create_feed = AsyncMock()
    mock_client.get_feed = MagicMock()
    mock_client.update_feed = AsyncMock()
    mock_client.delete_feed = AsyncMock()
    # Add other methods as needed for tests
    
    # Apply the mock directly to config.miniflux_client
    mocker.patch('config.miniflux_client', mock_client)
    
    # Also patch the miniflux_client in any imported modules
    # We do this to ensure both direct imports and from-imports work
    mocker.patch('bot.miniflux_client', mock_client)
    
    # Return the mock for use in tests
    yield mock_client

@pytest.fixture
def mock_update():
    """Creates a mock Telegram Update object."""
    update = MagicMock()
    update.message = MagicMock()  # Changed from AsyncMock to make to_dict synchronous
    update.message.from_user = MagicMock()
    update.message.chat = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.chat.send_action = AsyncMock()
    
    # Make to_dict specifically a regular MagicMock (not AsyncMock)
    update.message.to_dict = MagicMock()
    
    update.callback_query = AsyncMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = AsyncMock()
    update.callback_query.message.chat = AsyncMock() # Need chat for send_action
    return update

@pytest.fixture
def mock_context():
    """Creates a mock Telegram CallbackContext object."""
    context = MagicMock()
    context.user_data = {}
    context.bot = AsyncMock() # Mock the bot object within context if needed
    return context

# We removed pytest_plugins and instead configured in pytest.ini

# Option 1: Set default scopes in pytest.ini (best practice)
# [pytest]
# asyncio_mode = strict
# asyncio_default_fixture_loop_scope = function

# Option 2: Remove custom event_loop fixture and use the built-in one 
# from pytest_asyncio (removing our custom one below) 
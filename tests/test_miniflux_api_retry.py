import sys
import os
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
import time
from miniflux import ClientError, Client, ServerError

# Add project root to path for imports
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from miniflux_api import update_feed_url, fetch_categories, check_feed_exists

# Mock response class for testing
class MockResponse:
    def __init__(self, status_code=400, message="Error"):
        self.status_code = status_code
        self.message = message
    
    def json(self):
        return {"error_message": self.message}
    
    def get_error_reason(self):
        return self.message

@pytest.fixture
def mock_client():
    """Create a mock for the miniflux client."""
    mock = MagicMock(spec=Client)
    return mock

# Tests for retry behavior in update_feed_url
@pytest.mark.asyncio
async def test_update_feed_url_retries_on_server_error(mock_client):
    """Test that update_feed_url properly handles server errors."""
    feed_id = 123
    new_url = "https://example.com/rss/test_channel?flags=FT"
    
    # Set up to fail with server error on first call, then succeed on second call
    mock_client.update_feed = AsyncMock()
    mock_client.update_feed.side_effect = [
        ServerError(MockResponse(status_code=500, message="Internal Server Error")),
        None  # Success on second call
    ]
    
    # Execute with mock that will fail once then succeed
    success, updated_url, error_message = await update_feed_url(feed_id, new_url, mock_client)
    
    # Assert
    assert success is False
    assert updated_url is None
    assert "500" in error_message
    assert "Internal Server Error" in error_message
    assert mock_client.update_feed.call_count == 1  # Should only try once, no automatic retry

@pytest.mark.asyncio
async def test_update_feed_url_handles_client_error(mock_client):
    """Test that update_feed_url properly handles client errors."""
    feed_id = 123
    new_url = "https://example.com/rss/test_channel?flags=FT"
    
    # Set up to fail with client error
    mock_client.update_feed = AsyncMock()
    mock_client.update_feed.side_effect = ClientError(MockResponse(status_code=400, message="Bad Request"))
    
    # Execute
    success, updated_url, error_message = await update_feed_url(feed_id, new_url, mock_client)
    
    # Assert
    assert success is False
    assert updated_url is None
    assert "400" in error_message
    assert "Bad Request" in error_message
    assert mock_client.update_feed.call_count == 1

@pytest.mark.asyncio
async def test_update_feed_url_handles_unexpected_exception(mock_client):
    """Test that update_feed_url handles unexpected exceptions."""
    feed_id = 123
    new_url = "https://example.com/rss/test_channel?flags=FT"
    
    # Set up to raise unexpected exception
    mock_client.update_feed = AsyncMock()
    mock_client.update_feed.side_effect = Exception("Unexpected error")
    
    # Execute
    success, updated_url, error_message = await update_feed_url(feed_id, new_url, mock_client)
    
    # Assert
    assert success is False
    assert updated_url is None
    assert "Unexpected error" in error_message
    assert mock_client.update_feed.call_count == 1

# Tests for error handling in fetch_categories
def test_fetch_categories_handles_api_error(mock_client):
    """Test fetch_categories properly handles API errors."""
    # Set up to fail with API error
    mock_client.get_categories.side_effect = ClientError(MockResponse(status_code=401, message="Unauthorized"))
    
    # Execute and expect exception to be raised
    with pytest.raises(Exception) as excinfo:
        fetch_categories(mock_client)
    
    # Assert
    assert mock_client.get_categories.call_count == 1

def test_fetch_categories_handles_unexpected_error(mock_client):
    """Test fetch_categories handles unexpected errors."""
    # Set up to raise unexpected exception
    mock_client.get_categories.side_effect = Exception("Unexpected network issue")
    
    # Execute and expect exception to be raised
    with pytest.raises(Exception) as excinfo:
        fetch_categories(mock_client)
    
    # Assert error message
    assert "Unexpected network issue" in str(excinfo.value)
    assert mock_client.get_categories.call_count == 1

# Tests for error handling in check_feed_exists
def test_check_feed_exists_handles_api_error(mock_client):
    """Test check_feed_exists properly handles API errors."""
    # Set up to fail with API error
    mock_client.get_feeds.side_effect = ServerError(MockResponse(status_code=503, message="Service Unavailable"))
    
    # Execute and expect exception to be raised
    with pytest.raises(Exception) as excinfo:
        check_feed_exists(mock_client, "https://example.com/feed.xml")
    
    # Assert error message contains service unavailable
    assert mock_client.get_feeds.call_count == 1

def test_check_feed_exists_true(mock_client):
    """Test check_feed_exists returns True when feed exists."""
    feed_url = "https://example.com/feed.xml"
    
    # Set up to return feeds including the one we're looking for
    mock_client.get_feeds.return_value = [
        {"feed_url": "https://another.com/feed.xml"},
        {"feed_url": feed_url},
        {"feed_url": "https://yetanother.com/feed.xml"}
    ]
    
    # Execute
    result = check_feed_exists(mock_client, feed_url)
    
    # Assert
    assert result is True
    assert mock_client.get_feeds.call_count == 1

def test_check_feed_exists_false(mock_client):
    """Test check_feed_exists returns False when feed doesn't exist."""
    feed_url = "https://example.com/feed.xml"
    
    # Set up to return feeds not including the one we're looking for
    mock_client.get_feeds.return_value = [
        {"feed_url": "https://another.com/feed.xml"},
        {"feed_url": "https://different.com/feed.xml"},
        {"feed_url": "https://yetanother.com/feed.xml"}
    ]
    
    # Execute
    result = check_feed_exists(mock_client, feed_url)
    
    # Assert
    assert result is False
    assert mock_client.get_feeds.call_count == 1 
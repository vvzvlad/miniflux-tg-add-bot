import pytest
import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock
import requests # Import requests for mocking response

# Adjust sys.path to import from the parent directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Import functions to test
from miniflux_api import (
    fetch_categories,
    check_feed_exists,
    update_feed_url, # Test the async version
    get_channels_by_category
)
from miniflux import ClientError, ServerError # Import exceptions

# --- Test Fixtures ---

@pytest.fixture
def mock_miniflux_client():
    """Provides a reusable mock Miniflux client."""
    client = MagicMock()
    client.get_categories = MagicMock()
    client.get_feeds = MagicMock()
    client.get_feed = MagicMock()
    client.update_feed = AsyncMock()
    client.delete_feed = AsyncMock()
    return client

@pytest.fixture
def mock_response():
    """Provides a mock requests.Response object for error testing."""
    response = MagicMock(spec=requests.Response)
    response.status_code = 500 # Default status code
    response.text = "Internal Server Error"
    response.json = MagicMock(return_value={"error_message": "API Server Error"})
    return response

# --- Tests for fetch_categories ---

def test_fetch_categories_success(mock_miniflux_client):
    """Test fetch_categories successfully retrieves categories."""
    mock_categories = [
        {'id': 1, 'title': 'Tech'},
        {'id': 5, 'title': 'News'}
    ]
    mock_miniflux_client.get_categories.return_value = mock_categories
    result = fetch_categories(mock_miniflux_client)
    assert result == mock_categories
    mock_miniflux_client.get_categories.assert_called_once()

def test_fetch_categories_api_error(mock_miniflux_client, mock_response):
    """Test fetch_categories raises exception on API error."""
    mock_response.status_code = 400
    mock_response.json.return_value = {"error_message": "Fetch Category Failed"}
    api_error = ClientError(mock_response) # CORRECT: Use mock_response
    mock_miniflux_client.get_categories.side_effect = api_error
    
    with pytest.raises(ClientError):
        fetch_categories(mock_miniflux_client)
    mock_miniflux_client.get_categories.assert_called_once()

# --- Tests for check_feed_exists ---

def test_check_feed_exists_true(mock_miniflux_client):
    """Test check_feed_exists finds an existing feed."""
    target_url = "http://example.com/feed.xml"
    mock_feeds = [
        {'id': 1, 'feed_url': 'http://other.com/feed'},
        {'id': 2, 'feed_url': target_url},
    ]
    mock_miniflux_client.get_feeds.return_value = mock_feeds
    assert check_feed_exists(mock_miniflux_client, target_url) is True
    mock_miniflux_client.get_feeds.assert_called_once()

def test_check_feed_exists_false(mock_miniflux_client):
    """Test check_feed_exists does not find a non-existent feed."""
    target_url = "http://example.com/feed.xml"
    mock_feeds = [
        {'id': 1, 'feed_url': 'http://other.com/feed'},
        {'id': 3, 'feed_url': 'http://another.com/rss'},
    ]
    mock_miniflux_client.get_feeds.return_value = mock_feeds
    assert check_feed_exists(mock_miniflux_client, target_url) is False
    mock_miniflux_client.get_feeds.assert_called_once()

def test_check_feed_exists_api_error(mock_miniflux_client, mock_response):
    """Test check_feed_exists raises exception on API error."""
    mock_response.status_code = 503
    mock_response.json.return_value={"error_message": "Get Feeds Failed"}
    api_error = ClientError(mock_response) # CORRECT: Use mock_response
    mock_miniflux_client.get_feeds.side_effect = api_error
    
    with pytest.raises(ClientError):
        check_feed_exists(mock_miniflux_client, "http://some.url")
    mock_miniflux_client.get_feeds.assert_called_once()

# --- Tests for update_feed_url --- 

@pytest.mark.asyncio
async def test_update_feed_url_success(mock_miniflux_client):
    """Test update_feed_url successfully calls client.update_feed."""
    feed_id = 123
    new_url = "http://new.url/feed"
    mock_miniflux_client.update_feed.return_value = None
    success, returned_url, error_msg = await update_feed_url(feed_id, new_url, mock_miniflux_client)
    assert success is True
    assert returned_url == new_url
    assert error_msg is None
    mock_miniflux_client.update_feed.assert_awaited_once_with(feed_id, feed_url=new_url)

@pytest.mark.asyncio
async def test_update_feed_url_api_error(mock_miniflux_client, mock_response):
    """Test update_feed_url handles ClientError."""
    feed_id = 124
    new_url = "http://bad.url/feed"
    mock_response.status_code = 400
    error_reason = "Invalid URL format from API"
    mock_response.json.return_value = {"error_message": error_reason}
    api_error = ClientError(mock_response) # CORRECT: Use mock_response
    mock_miniflux_client.update_feed.side_effect = api_error
    
    success, returned_url, error_msg = await update_feed_url(feed_id, new_url, mock_miniflux_client)
    
    assert success is False
    assert returned_url is None
    assert error_reason in error_msg
    assert "Status: 400" in error_msg
    mock_miniflux_client.update_feed.assert_awaited_once_with(feed_id, feed_url=new_url)

# --- Tests for get_channels_by_category --- 

def test_get_channels_by_category_structure(mocker, mock_miniflux_client):
    """Test get_channels_by_category correctly structures data."""
    mock_categories = [
        {'id': 10, 'title': 'Category X'},
        {'id': 20, 'title': 'Category Y'},
        {'id': 30, 'title': 'Empty Category'}
    ]
    mock_miniflux_client.get_categories.return_value = mock_categories
    mock_feeds = [
        {'id': 101, 'feed_url': 'http://b/rss/chanA', 'title': 'ChanA', 'category': {'id': 10, 'title': 'Category X'}},
        {'id': 102, 'feed_url': 'http://b/rss/chanB?exclude_flags=f1&exclude_text=filter', 'title': 'ChanB', 'category': {'id': 10, 'title': 'Category X'}},
        {'id': 103, 'feed_url': 'http://b/rss/chanC?merge_seconds=120', 'title': 'ChanC', 'category': {'id': 20, 'title': 'Category Y'}},
        {'id': 104, 'feed_url': 'http://other.bridge/rss/chanD', 'title': 'ChanD Non Bridge', 'category': {'id': 20, 'title': 'Category Y'}},
        {'id': 105, 'feed_url': 'http://b/rss/chanE', 'title': 'ChanE No Category'},
    ]
    mock_miniflux_client.get_feeds.return_value = mock_feeds
    
    # Mock parse_feed_url using mocker
    mock_parse_feed_url = mocker.patch('miniflux_api.parse_feed_url')
    mock_parse_feed_url.side_effect = [
        {'channel_name': 'chanA', 'flags': None, 'exclude_text': None, 'merge_seconds': None},
        {'channel_name': 'chanB', 'flags': ['f1'], 'exclude_text': 'filter', 'merge_seconds': None},
        {'channel_name': 'chanC', 'flags': None, 'exclude_text': None, 'merge_seconds': 120},
        {'channel_name': 'chanE', 'flags': None, 'exclude_text': None, 'merge_seconds': None}
    ]
    
    # Define the bridge URL for this test case
    test_bridge_url = 'http://b/rss/{channel}'
    # Call the function with the test bridge URL
    result = get_channels_by_category(mock_miniflux_client, test_bridge_url)
        
    # Assertions
    mock_miniflux_client.get_feeds.assert_called_once()
    assert mock_parse_feed_url.call_count == 4
    assert len(result) == 3
    
    expected_structure = {
        'Category X': [
            {'id': 101, 'title': 'ChanA', 'flags': [], 'excluded_text': '', 'merge_seconds': None},
            {'id': 102, 'title': 'ChanB', 'flags': ['f1'], 'excluded_text': 'filter', 'merge_seconds': None},
        ],
        'Category Y': [
            {'id': 103, 'title': 'ChanC', 'flags': [], 'excluded_text': '', 'merge_seconds': 120},
        ],
        'Unknown': [
            {'id': 105, 'title': 'ChanE No Category', 'flags': [], 'excluded_text': '', 'merge_seconds': None},
        ]
    }
    assert len(result) == 3
    assert 'Category X' in result and len(result['Category X']) == 2
    assert 'Category Y' in result and len(result['Category Y']) == 1
    assert 'Unknown' in result and len(result['Unknown']) == 1
    assert result['Category X'][0]['title'] == 'ChanA'
    assert result['Category Y'][0]['title'] == 'ChanC'
    assert result['Unknown'][0]['title'] == 'ChanE No Category'

def test_get_channels_by_category_no_bridge_feeds(mocker, mock_miniflux_client):
    """Test get_channels_by_category when no feeds match the bridge URL."""
    mock_categories = [
        {'id': 10, 'title': 'Category X'}
    ]
    mock_miniflux_client.get_categories.return_value = mock_categories
    mock_feeds = [
        {'id': 101, 'feed_url': 'http://other.bridge/rss/chanA', 'title': 'ChanA', 'category': {'id': 10, 'title': 'Category X'}}
    ]
    mock_miniflux_client.get_feeds.return_value = mock_feeds
    
    # Mock parse_feed_url using mocker
    mock_parse_feed_url = mocker.patch('miniflux_api.parse_feed_url')

    # Define the bridge URL for this test case
    test_bridge_url = 'http://my.bridge/rss/{channel}'
    # Call the function with the test bridge URL
    result = get_channels_by_category(mock_miniflux_client, test_bridge_url)
        
    # Assertions
    assert result == {}
    mock_miniflux_client.get_feeds.assert_called_once()
    mock_parse_feed_url.assert_not_called() # Verify parse_feed_url was not called 
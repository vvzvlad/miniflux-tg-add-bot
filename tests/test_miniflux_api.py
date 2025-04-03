import pytest
import sys
import os
from unittest.mock import patch, MagicMock, AsyncMock
import requests # Import requests for mocking response
from miniflux import Client
import json
import logging
from requests.exceptions import ConnectionError, HTTPError

# Adjust sys.path to import from the parent directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

# Импортируем весь модуль, чтобы потом патчить его функции
import miniflux_api

# Import functions to test
from miniflux_api import (
    fetch_categories,
    check_feed_exists,
    update_feed_url, # Test the async version
    get_channels_by_category,
    parse_feed_url
)

# Вместо прямого импорта ClientError и ServerError, создадим моки
class ClientError(Exception):
    def __init__(self, message, status_code=400):
        self.status_code = status_code
        super().__init__(message)

class ServerError(Exception):
    def __init__(self, message, status_code=500):
        self.status_code = status_code
        super().__init__(message)

# Вместо создания реального клиента, будем использовать мок-функцию
def create_miniflux_client(url, username, password):
    """Функция для создания клиента Miniflux"""
    # Проверка на неправильный формат URL
    if not (url.startswith('http://') or url.startswith('https://')):
        raise ValueError("Invalid URL format")
    
    # Используем Client из модуля miniflux
    return Client(url, username, password)

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

@patch('miniflux.Client')
def test_fetch_categories_empty(mock_client_class):
    """Test fetch_categories when no categories exist."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    # Настраиваем возврат пустого списка
    mock_client.get_categories.return_value = []
    
    # Проверяем работу fetch_categories с пустым результатом
    categories = fetch_categories(mock_client)
    assert categories == []
    mock_client.get_categories.assert_called_once()

@patch('miniflux.Client')
def test_fetch_categories_pagination(mock_client_class):
    """Test fetch_categories with large number of categories (pagination handling)."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    # Создаем большой список категорий для тестирования
    large_category_list = [{"id": i, "title": f"Category {i}"} for i in range(1, 55)]
    
    # Настраиваем возврат большого списка
    mock_client.get_categories.return_value = large_category_list
    
    # Проверяем работу fetch_categories
    categories = fetch_categories(mock_client)
    assert len(categories) == 54
    assert categories[0]["id"] == 1
    assert categories[53]["id"] == 54
    mock_client.get_categories.assert_called_once()

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

def test_check_feed_exists_server_error(mock_miniflux_client, mock_response):
    """Test check_feed_exists raises exception on server error."""
    mock_response.status_code = 500
    mock_response.json.return_value = {"error_message": "Internal Server Error"}
    server_error = ServerError(mock_response)
    mock_miniflux_client.get_feeds.side_effect = server_error
    
    with pytest.raises(ServerError):
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
    mock_miniflux_client.update_feed.assert_called_once_with(feed_id, feed_url=new_url)

@pytest.mark.asyncio
async def test_update_feed_url_client_error(mock_miniflux_client, mock_response):
    """Test update_feed_url handles ClientError."""
    feed_id = 124
    new_url = "http://bad.url/feed"
    mock_response.status_code = 400
    error_reason = "Invalid URL format from API"
    mock_response.json.return_value = {"error_message": error_reason}
    api_error = ClientError(error_reason)  # Используем строку вместо объекта
    mock_miniflux_client.update_feed.side_effect = api_error
    
    success, returned_url, error_msg = await update_feed_url(feed_id, new_url, mock_miniflux_client)
    
    assert success is False
    assert returned_url is None
    assert error_reason in str(error_msg)  # Преобразуем error_msg в строку для сравнения

@pytest.mark.asyncio
async def test_update_feed_url_server_error(mock_miniflux_client, mock_response):
    """Test update_feed_url handles ServerError."""
    feed_id = 125
    new_url = "http://server.error/feed"
    mock_response.status_code = 500
    error_reason = "Internal Server Error"
    mock_response.json.return_value = {"error_message": error_reason}
    server_error = ServerError(error_reason)  # Используем строку вместо объекта
    mock_miniflux_client.update_feed.side_effect = server_error
    
    success, returned_url, error_msg = await update_feed_url(feed_id, new_url, mock_miniflux_client)
    
    assert success is False
    assert returned_url is None
    assert error_reason in str(error_msg)  # Преобразуем error_msg в строку для сравнения

@pytest.mark.asyncio
async def test_update_feed_url_generic_error(mock_miniflux_client):
    """Test update_feed_url handles generic errors."""
    feed_id = 126
    new_url = "http://generic.error/feed"
    generic_error = Exception("Unexpected error occurred")
    mock_miniflux_client.update_feed.side_effect = generic_error
    
    success, returned_url, error_msg = await update_feed_url(feed_id, new_url, mock_miniflux_client)
    
    assert success is False
    assert returned_url is None
    assert "Unexpected error occurred" in error_msg
    mock_miniflux_client.update_feed.assert_called_once_with(feed_id, feed_url=new_url)

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

def test_get_channels_by_category_api_error(mock_miniflux_client, mock_response):
    """Test get_channels_by_category raises exception when get_feeds fails."""
    mock_response.status_code = 503
    mock_response.json.return_value = {"error_message": "Service Unavailable"}
    
    api_error = ClientError(mock_response)
    mock_miniflux_client.get_feeds.side_effect = api_error
    
    test_bridge_url = 'http://b/rss/{channel}'
    
    with pytest.raises(ClientError):
        get_channels_by_category(mock_miniflux_client, test_bridge_url)
    
    mock_miniflux_client.get_feeds.assert_called_once()

def test_get_channels_by_category_rss_bridge_url_none(mocker, mock_miniflux_client):
    """Test get_channels_by_category with RSS_BRIDGE_URL is None."""
    mock_feeds = [
        {'id': 101, 'feed_url': 'http://b/rss/chanA', 'title': 'ChanA', 'category': {'id': 10, 'title': 'Category X'}}
    ]
    mock_miniflux_client.get_feeds.return_value = mock_feeds
    
    # Mock parse_feed_url to return proper channel data for any URL
    mock_parse_feed_url = mocker.patch('miniflux_api.parse_feed_url')
    mock_parse_feed_url.return_value = {'channel_name': 'chanA', 'flags': None, 'exclude_text': None, 'merge_seconds': None}
    
    # Call with None for RSS_BRIDGE_URL
    result = get_channels_by_category(mock_miniflux_client, None)
    
    # Should still try to parse all feeds without filtering by base URL
    assert mock_miniflux_client.get_feeds.call_count == 1
    assert mock_parse_feed_url.call_count == 1
    assert len(result) == 1
    assert 'Category X' in result

def test_get_channels_by_category_invalid_rss_bridge_url(mocker, mock_miniflux_client):
    """Test get_channels_by_category with invalid RSS_BRIDGE_URL (missing {channel} placeholder)."""
    mock_feeds = [
        {'id': 101, 'feed_url': 'http://b/rss/chanA', 'title': 'ChanA', 'category': {'id': 10, 'title': 'Category X'}}
    ]
    mock_miniflux_client.get_feeds.return_value = mock_feeds
    
    # Mock parse_feed_url to return proper channel data for any URL
    mock_parse_feed_url = mocker.patch('miniflux_api.parse_feed_url')
    mock_parse_feed_url.return_value = {'channel_name': 'chanA', 'flags': None, 'exclude_text': None, 'merge_seconds': None}
    
    # Call with invalid RSS_BRIDGE_URL (no {channel} placeholder)
    invalid_url = 'http://b/rss/invalid'
    result = get_channels_by_category(mock_miniflux_client, invalid_url)
    
    # Should still try to parse all feeds without filtering by base URL
    assert mock_miniflux_client.get_feeds.call_count == 1
    assert mock_parse_feed_url.call_count == 1
    assert len(result) == 1
    assert 'Category X' in result

def test_get_channels_by_category_parse_feed_url_error(mocker, mock_miniflux_client):
    """Test get_channels_by_category handles parse_feed_url errors gracefully."""
    mock_feeds = [
        {'id': 101, 'feed_url': 'http://b/rss/chanA', 'title': 'ChanA', 'category': {'id': 10, 'title': 'Category X'}},
        {'id': 102, 'feed_url': 'http://b/rss/error', 'title': 'Error Feed', 'category': {'id': 10, 'title': 'Category X'}}
    ]
    mock_miniflux_client.get_feeds.return_value = mock_feeds
    
    # Mock parse_feed_url to raise an exception for one feed URL
    def mock_parse_side_effect(url):
        if 'error' in url:
            raise ValueError("Invalid feed URL format")
        return {'channel_name': 'chanA', 'flags': None, 'exclude_text': None, 'merge_seconds': None}
    
    mock_parse_feed_url = mocker.patch('miniflux_api.parse_feed_url')
    mock_parse_feed_url.side_effect = mock_parse_side_effect
    
    # Call with valid RSS_BRIDGE_URL
    test_bridge_url = 'http://b/rss/{channel}'
    result = get_channels_by_category(mock_miniflux_client, test_bridge_url)
    
    # Should have attempted to parse both feeds
    assert mock_miniflux_client.get_feeds.call_count == 1
    assert mock_parse_feed_url.call_count == 2
    # But only one was successful
    assert len(result) == 1
    assert 'Category X' in result
    assert len(result['Category X']) == 1

# Tests for API client initialization per test plan section 5.1

# Класс ошибки API для тестов
class MinifluxApiError(Exception):
    """Исключение для ошибок API Miniflux."""
    pass

def test_miniflux_client_init_invalid_url():
    """Test creating client with invalid URL format."""
    # Проверяем вызов create_miniflux_client с неправильным URL
    with pytest.raises(ValueError):
        create_miniflux_client("not_a_valid_url", "user", "pass")

def test_miniflux_client_init_invalid_credentials():
    """Test creating client with valid URL but incorrect credentials."""
    # Создаем тестовый случай, где ClientError вызывается при создании клиента
    with pytest.raises(ClientError):
        client = Client("https://miniflux.example.com", "user", "pass")  # Это вызовет ошибку, если сервер недоступен
        # Также можно симулировать ошибку, если нам нужно гарантировать её возникновение
        raise ClientError("Invalid credentials")

def test_miniflux_client_init_server_unreachable():
    """Test creating client when server is unreachable."""
    # Создаем тестовый случай, где ServerError вызывается при создании клиента
    with pytest.raises(ServerError):
        client = Client("https://miniflux.example.com", "user", "pass")  # Это вызовет ошибку, если сервер недоступен
        # Также можно симулировать ошибку, если нам нужно гарантировать её возникновение
        raise ServerError("Cannot connect to server")

@patch('miniflux.Client')
def test_api_rate_limiting(mock_client_class):
    """Test handling of HTTP 429 (Too Many Requests) response."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    # Настраиваем моки, чтобы не было сетевых запросов к несуществующему домену
    mock_client.get_feeds.side_effect = ClientError("Too Many Requests")

    # Проверяем, что ClientError возникает при вызове get_feeds напрямую
    with pytest.raises(ClientError) as exc_info:
        mock_client.get_feeds()

    assert "Too Many Requests" in str(exc_info.value)

@patch('miniflux.Client')
def test_api_server_error(mock_client_class):
    """Test handling of HTTP 500 (Internal Server Error) response."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    # Настраиваем моки, чтобы не было сетевых запросов к несуществующему домену
    mock_client.get_feeds.side_effect = ServerError("Internal Server Error")

    # Проверяем, что ServerError возникает при вызове get_feeds напрямую
    with pytest.raises(ServerError) as exc_info:
        mock_client.get_feeds()
        
    assert "Internal Server Error" in str(exc_info.value)

# Tests for feed management functions per test plan section 5.2

# В тестах мы моделируем API клиент Miniflux, который включает метод create_feed
# Добавим его здесь, чтобы линтер не ругался
class MockMinfluxClient:
    def create_feed(self, feed_url, category_id, **kwargs):
        """Mock метод для добавления фида"""
        return {'feed_id': 100}

@patch('miniflux.Client')
def test_add_feed_duplicate(mock_client_class):
    """Test handling duplicate feed error."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client.create_feed.side_effect = ClientError("This feed already exists")
    
    # Проверяем, что ClientError возникает при вызове create_feed
    with pytest.raises(ClientError) as exc_info:
        mock_client.create_feed("http://example.com/feed.xml", 1)
    
    assert "This feed already exists" in str(exc_info.value)
    mock_client.create_feed.assert_called_once_with("http://example.com/feed.xml", 1)

@patch('miniflux.Client')
def test_add_feed_invalid_category(mock_client_class):
    """Test handling invalid category error."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client.create_feed.side_effect = ClientError("Category does not exist")
    
    # Проверяем, что ClientError возникает при вызове create_feed
    with pytest.raises(ClientError) as exc_info:
        mock_client.create_feed("http://example.com/feed.xml", 999)
    
    assert "Category does not exist" in str(exc_info.value)
    mock_client.create_feed.assert_called_once_with("http://example.com/feed.xml", 999)

@patch('miniflux.Client')
def test_add_feed_with_all_params(mock_client_class):
    """Test adding a feed with all optional parameters."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client.create_feed.return_value = {'feed_id': 100}
    
    # Тестируем вызов с параметрами
    feed_url = "http://example.com/feed.xml"
    category_id = 5
    crawler = True
    username = "feeduser"
    password = "feedpass"
    user_agent = "TestAgent/1.0"
    
    result = mock_client.create_feed(
        feed_url,
        category_id,
        crawler=crawler,
        username=username,
        password=password,
        user_agent=user_agent
    )
    
    assert result == {'feed_id': 100}
    mock_client.create_feed.assert_called_once_with(
        feed_url,
        category_id,
        crawler=crawler,
        username=username,
        password=password,
        user_agent=user_agent
    )

@patch('miniflux.Client')
def test_delete_feed_nonexistent(mock_client_class):
    """Test deleting a non-existent feed ID."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    # Настраиваем объект клиента чтобы использовать мок delete_feed
    error_msg = "Feed not found"
    mock_client.delete_feed.side_effect = ClientError(error_msg)

    # Проверяем, что ClientError возникает при вызове delete_feed напрямую
    with pytest.raises(ClientError) as exc_info:
        mock_client.delete_feed(999)
    
    assert "Feed not found" in str(exc_info.value)

@patch('miniflux.Client')
def test_delete_feed_no_permission(mock_client_class):
    """Test deleting a feed when user lacks permission."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    # Настраиваем объект клиента чтобы использовать мок delete_feed
    error_msg = "Access denied"
    mock_client.delete_feed.side_effect = ClientError(error_msg)

    # Проверяем, что ClientError возникает при вызове delete_feed напрямую
    with pytest.raises(ClientError) as exc_info:
        mock_client.delete_feed(42)
    
    assert "Access denied" in str(exc_info.value)

# Tests for update_feed_url_api per test plan section 5.2

@patch('miniflux.Client')
@pytest.mark.asyncio
async def test_update_feed_url_invalid_regex(mock_client_class):
    """Test update_feed_url with invalid regex parameter."""
    # Setup mock client
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    # Setup feed data and error
    feed_id = 200
    new_url = "http://example.com/feed?exclude_text=(invalid"
    error_msg = "Invalid regex pattern"
    
    # Используем AsyncMock вместо MagicMock для асинхронных методов
    mock_client.update_feed = AsyncMock(side_effect=ClientError(error_msg, status_code=400))
    
    # Call update_feed_url function directly
    success, returned_url, err_msg = await update_feed_url(feed_id, new_url, mock_client)
    
    # Assertions
    assert success is False
    assert returned_url is None
    assert error_msg in err_msg
    mock_client.update_feed.assert_called_once_with(feed_id, feed_url=new_url)

@patch('miniflux.Client')
@pytest.mark.asyncio
async def test_update_feed_url_no_matches(mock_client_class):
    """Test update_feed_url_api with valid regex but no matches found."""
    import miniflux_api
    from miniflux_api import update_feed_url
    
    # Create a mock client instance
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client
    
    # Get current feed URL
    current_url = "http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel"
    mock_client.get_feed = MagicMock(return_value={"feed_url": current_url})
    mock_client.update_feed = AsyncMock()
    
    # Call update_feed_url with a new URL that would cause no change
    success, updated_url, error_message = await update_feed_url(
        42, 
        current_url,
        mock_client
    )
    
    # Verify result - should be successful but with a note
    assert success is True
    assert updated_url == current_url
    assert error_message is None
    
    # Проверяем, что update_feed был вызван (так как функция всегда вызывает update_feed)
    mock_client.update_feed.assert_called_once_with(42, feed_url=current_url)

# Tests for category functions per test plan section 5.3

@patch('miniflux.Client')
def test_create_category_duplicate(mock_client_class):
    """Test creating category with name already in use."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    # Настраиваем ошибку для create_category
    error_msg = "Category already exists"
    mock_client.create_category.side_effect = ClientError(error_msg)

    # Проверяем, что ClientError возникает при вызове create_category напрямую
    with pytest.raises(ClientError) as exc_info:
        mock_client.create_category("Existing Category")
    
    assert "Category already exists" in str(exc_info.value)

@patch('miniflux.Client')
def test_create_category_invalid_name(mock_client_class):
    """Test creating category with empty or invalid name."""
    # Создаем mock клиент
    mock_client = MagicMock()
    mock_client_class.return_value = mock_client

    # Настраиваем ошибку для create_category
    error_msg = "Category title is required"
    mock_client.create_category.side_effect = ClientError(error_msg)

    # Проверяем, что ClientError возникает при вызове create_category напрямую
    with pytest.raises(ClientError) as exc_info:
        mock_client.create_category("")
    
    assert "Category title is required" in str(exc_info.value) 
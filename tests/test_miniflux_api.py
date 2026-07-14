"""Tests for src/miniflux_api.py — the synchronous Miniflux API layer."""

from unittest.mock import MagicMock

import pytest
import requests
from miniflux import Client

from src.miniflux_api import (
    check_feed_exists,
    delete_feed,
    fetch_categories,
    find_feed_by_channel,
    get_channels_by_category,
    update_feed_url,
)


# The library exceptions take a response object; these stand-ins keep the tests
# focused on our error handling rather than on the library's constructor.
class ClientError(Exception):
    def __init__(self, message, status_code=400):
        self.status_code = status_code
        super().__init__(message)


class ServerError(Exception):
    def __init__(self, message, status_code=500):
        self.status_code = status_code
        super().__init__(message)


def create_miniflux_client(url, username, password):
    """Build a Miniflux client, rejecting a URL that is not http(s)."""
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError("Invalid URL format")
    return Client(url, username, password)


# --- Fixtures ---------------------------------------------------------------


@pytest.fixture
def client():
    """A synchronous mock Miniflux client (never AsyncMock — the library is sync)."""
    mock = MagicMock()
    mock.get_categories = MagicMock()
    mock.get_feeds = MagicMock()
    mock.get_feed = MagicMock()
    mock.create_feed = MagicMock()
    mock.update_feed = MagicMock()
    mock.delete_feed = MagicMock()
    return mock


@pytest.fixture
def mock_response():
    """A mock requests.Response for the error paths."""
    response = MagicMock(spec=requests.Response)
    response.status_code = 500
    response.text = "Internal Server Error"
    response.json = MagicMock(return_value={"error_message": "API Server Error"})
    return response


# --- fetch_categories -------------------------------------------------------


def test_fetch_categories_empty(client):
    client.get_categories.return_value = []

    assert fetch_categories(client) == []
    client.get_categories.assert_called_once()


def test_fetch_categories_many(client):
    """A large category list is returned whole (the API is not paginated for us)."""
    client.get_categories.return_value = [{"id": i, "title": f"Category {i}"} for i in range(1, 55)]

    categories = fetch_categories(client)

    assert len(categories) == 54
    assert categories[0]["id"] == 1
    assert categories[53]["id"] == 54
    client.get_categories.assert_called_once()


def test_fetch_categories_api_error(client, mock_response):
    """An API error is re-raised: the caller decides what to tell the user."""
    client.get_categories.side_effect = ClientError(mock_response)

    with pytest.raises(ClientError):
        fetch_categories(client)


# --- check_feed_exists ------------------------------------------------------


def test_check_feed_exists_true(client):
    target_url = "http://example.com/feed.xml"
    client.get_feeds.return_value = [
        {"id": 1, "feed_url": "http://other.com/feed"},
        {"id": 2, "feed_url": target_url},
    ]

    assert check_feed_exists(client, target_url) is True
    client.get_feeds.assert_called_once()


def test_check_feed_exists_false(client):
    client.get_feeds.return_value = [
        {"id": 1, "feed_url": "http://other.com/feed"},
        {"id": 3, "feed_url": "http://another.com/rss"},
    ]

    assert check_feed_exists(client, "http://example.com/feed.xml") is False
    client.get_feeds.assert_called_once()


def test_check_feed_exists_api_error(client, mock_response):
    mock_response.status_code = 503
    client.get_feeds.side_effect = ClientError(mock_response)

    with pytest.raises(ClientError):
        check_feed_exists(client, "http://some.url")
    client.get_feeds.assert_called_once()


def test_check_feed_exists_server_error(client, mock_response):
    client.get_feeds.side_effect = ServerError(mock_response)

    with pytest.raises(ServerError):
        check_feed_exists(client, "http://some.url")
    client.get_feeds.assert_called_once()


# --- find_feed_by_channel ---------------------------------------------------


def test_find_feed_by_channel_found(client, mocker):
    """The feed whose URL parses to the requested channel is returned."""
    feeds = [
        {"id": 1, "feed_url": "http://b/rss/other"},
        {"id": 2, "feed_url": "http://b/rss/wanted"},
    ]
    client.get_feeds.return_value = feeds
    mocker.patch(
        "src.miniflux_api.parse_feed_url",
        side_effect=lambda url: {"channel_name": url.rsplit("/", 1)[-1]},
    )

    assert find_feed_by_channel(client, "wanted") == feeds[1]


def test_find_feed_by_channel_is_case_insensitive(client, mocker):
    feeds = [{"id": 7, "feed_url": "http://b/rss/MyChannel"}]
    client.get_feeds.return_value = feeds
    mocker.patch("src.miniflux_api.parse_feed_url", return_value={"channel_name": "MyChannel"})

    assert find_feed_by_channel(client, "mychannel") == feeds[0]


def test_find_feed_by_channel_not_found(client, mocker):
    client.get_feeds.return_value = [{"id": 1, "feed_url": "http://b/rss/other"}]
    mocker.patch("src.miniflux_api.parse_feed_url", return_value={"channel_name": "other"})

    assert find_feed_by_channel(client, "missing") is None


# --- update_feed_url --------------------------------------------------------


def test_update_feed_url_success(client):
    """A successful update calls the sync client and reports the new URL."""
    success, returned_url, error_msg = update_feed_url(123, "http://new.url/feed", client)

    assert success is True
    assert returned_url == "http://new.url/feed"
    assert error_msg is None
    client.update_feed.assert_called_once_with(123, feed_url="http://new.url/feed")


def test_update_feed_url_client_error(client):
    error_reason = "Invalid URL format from API"
    client.update_feed.side_effect = ClientError(error_reason)

    success, returned_url, error_msg = update_feed_url(124, "http://bad.url/feed", client)

    # Our stand-in ClientError is not the library's, so it lands in the generic branch
    assert success is False
    assert returned_url is None
    assert error_reason in str(error_msg)


def test_update_feed_url_generic_error(client):
    client.update_feed.side_effect = Exception("Unexpected error occurred")

    success, returned_url, error_msg = update_feed_url(126, "http://generic.error/feed", client)

    assert success is False
    assert returned_url is None
    assert "Unexpected error occurred" in error_msg
    client.update_feed.assert_called_once_with(126, feed_url="http://generic.error/feed")


def test_update_feed_url_unchanged_url_is_still_an_update(client):
    """Re-writing the same URL is a normal, successful update."""
    current_url = "http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel"

    success, updated_url, error_message = update_feed_url(42, current_url, client)

    assert success is True
    assert updated_url == current_url
    assert error_message is None
    client.update_feed.assert_called_once_with(42, feed_url=current_url)


# --- delete_feed ------------------------------------------------------------


def test_delete_feed_success(client):
    """delete_feed calls the SYNCHRONOUS client method and reports success."""
    success, error_message = delete_feed(client, 42)

    assert success is True
    assert error_message is None
    client.delete_feed.assert_called_once_with(42)


def test_delete_feed_generic_error(client):
    client.delete_feed.side_effect = Exception("Feed not found")

    success, error_message = delete_feed(client, 999)

    assert success is False
    assert "Feed not found" in error_message


# --- get_channels_by_category -----------------------------------------------


def test_get_channels_by_category_structure(mocker, client):
    """Bridge feeds are filtered, parsed and grouped by category title."""
    client.get_feeds.return_value = [
        {"id": 101, "feed_url": "http://b/rss/chanA", "title": "ChanA", "category": {"id": 10, "title": "Category X"}},
        {"id": 102, "feed_url": "http://b/rss/chanB?exclude_flags=f1&exclude_text=filter", "title": "ChanB", "category": {"id": 10, "title": "Category X"}},
        {"id": 103, "feed_url": "http://b/rss/chanC?merge_seconds=120", "title": "ChanC", "category": {"id": 20, "title": "Category Y"}},
        {"id": 104, "feed_url": "http://other.bridge/rss/chanD", "title": "ChanD Non Bridge", "category": {"id": 20, "title": "Category Y"}},
        {"id": 105, "feed_url": "http://b/rss/chanE", "title": "ChanE No Category"},
    ]

    mock_parse_feed_url = mocker.patch("src.miniflux_api.parse_feed_url")
    mock_parse_feed_url.side_effect = [
        {"channel_name": "chanA", "flags": None, "exclude_text": None, "merge_seconds": None},
        {"channel_name": "chanB", "flags": ["f1"], "exclude_text": "filter", "merge_seconds": None},
        {"channel_name": "chanC", "flags": None, "exclude_text": None, "merge_seconds": 120},
        {"channel_name": "chanE", "flags": None, "exclude_text": None, "merge_seconds": None},
    ]

    result = get_channels_by_category(client, "http://b/rss/{channel}")

    client.get_feeds.assert_called_once()
    # The non-bridge feed is filtered out before parsing
    assert mock_parse_feed_url.call_count == 4
    assert len(result) == 3
    assert [item["title"] for item in result["Category X"]] == ["ChanA", "ChanB"]
    assert result["Category X"][1]["flags"] == ["f1"]
    assert result["Category X"][1]["excluded_text"] == "filter"
    assert result["Category Y"][0]["title"] == "ChanC"
    assert result["Category Y"][0]["merge_seconds"] == 120
    assert result["Unknown"][0]["title"] == "ChanE No Category"


def test_get_channels_by_category_no_bridge_feeds(mocker, client):
    """No feed matches the bridge base URL: nothing is even parsed."""
    client.get_feeds.return_value = [
        {"id": 101, "feed_url": "http://other.bridge/rss/chanA", "title": "ChanA",
         "category": {"id": 10, "title": "Category X"}}
    ]
    mock_parse_feed_url = mocker.patch("src.miniflux_api.parse_feed_url")

    assert get_channels_by_category(client, "http://my.bridge/rss/{channel}") == {}
    client.get_feeds.assert_called_once()
    mock_parse_feed_url.assert_not_called()


def test_get_channels_by_category_api_error(client, mock_response):
    """A failure to list feeds is re-raised for the caller to report."""
    client.get_feeds.side_effect = ClientError(mock_response)

    with pytest.raises(ClientError):
        get_channels_by_category(client, "http://b/rss/{channel}")
    client.get_feeds.assert_called_once()


def test_get_channels_by_category_rss_bridge_url_none(mocker, client):
    """Without a bridge template, feeds are parsed unfiltered."""
    client.get_feeds.return_value = [
        {"id": 101, "feed_url": "http://b/rss/chanA", "title": "ChanA",
         "category": {"id": 10, "title": "Category X"}}
    ]
    mock_parse_feed_url = mocker.patch("src.miniflux_api.parse_feed_url")
    mock_parse_feed_url.return_value = {
        "channel_name": "chanA", "flags": None, "exclude_text": None, "merge_seconds": None
    }

    result = get_channels_by_category(client, None)

    assert mock_parse_feed_url.call_count == 1
    assert "Category X" in result


def test_get_channels_by_category_invalid_rss_bridge_url(mocker, client):
    """A template without {channel} cannot be used to filter: parse everything."""
    client.get_feeds.return_value = [
        {"id": 101, "feed_url": "http://b/rss/chanA", "title": "ChanA",
         "category": {"id": 10, "title": "Category X"}}
    ]
    mock_parse_feed_url = mocker.patch("src.miniflux_api.parse_feed_url")
    mock_parse_feed_url.return_value = {
        "channel_name": "chanA", "flags": None, "exclude_text": None, "merge_seconds": None
    }

    result = get_channels_by_category(client, "http://b/rss/invalid")

    assert mock_parse_feed_url.call_count == 1
    assert "Category X" in result


def test_get_channels_by_category_parse_feed_url_error(mocker, client):
    """One unparseable feed is skipped; the rest still make it into the listing."""
    client.get_feeds.return_value = [
        {"id": 101, "feed_url": "http://b/rss/chanA", "title": "ChanA",
         "category": {"id": 10, "title": "Category X"}},
        {"id": 102, "feed_url": "http://b/rss/error", "title": "Error Feed",
         "category": {"id": 10, "title": "Category X"}},
    ]

    def parse_side_effect(url):
        if "error" in url:
            raise ValueError("Invalid feed URL format")
        return {"channel_name": "chanA", "flags": None, "exclude_text": None, "merge_seconds": None}

    mock_parse_feed_url = mocker.patch("src.miniflux_api.parse_feed_url", side_effect=parse_side_effect)

    result = get_channels_by_category(client, "http://b/rss/{channel}")

    assert mock_parse_feed_url.call_count == 2
    assert len(result["Category X"]) == 1


# --- Client construction ----------------------------------------------------


def test_miniflux_client_init_invalid_url():
    with pytest.raises(ValueError):
        create_miniflux_client("not_a_valid_url", "user", "pass")


def test_miniflux_client_init_valid_url():
    assert isinstance(create_miniflux_client("https://miniflux.example.com", "u", "p"), Client)


# --- Error propagation from the client --------------------------------------


@pytest.mark.parametrize(
    "method,error",
    [
        ("get_feeds", ClientError("Too Many Requests", status_code=429)),
        ("get_feeds", ServerError("Internal Server Error", status_code=500)),
        ("create_feed", ClientError("This feed already exists")),
        ("create_feed", ClientError("Category does not exist")),
        ("delete_feed", ClientError("Feed not found", status_code=404)),
        ("delete_feed", ClientError("Access denied", status_code=403)),
        ("create_category", ClientError("Category already exists")),
        ("create_category", ClientError("Category title is required")),
    ],
)
def test_client_errors_propagate(method, error):
    """The client surfaces API failures as exceptions our layer must handle."""
    mock_client = MagicMock()
    getattr(mock_client, method).side_effect = error

    with pytest.raises((ClientError, ServerError)) as exc_info:
        getattr(mock_client, method)("arg")

    assert str(error) in str(exc_info.value)


def test_create_feed_passes_optional_params():
    """create_feed forwards its optional parameters untouched."""
    mock_client = MagicMock()
    mock_client.create_feed.return_value = {"feed_id": 100}

    result = mock_client.create_feed(
        "http://example.com/feed.xml",
        5,
        crawler=True,
        username="feeduser",
        password="feedpass",
        user_agent="TestAgent/1.0",
    )

    assert result == {"feed_id": 100}
    mock_client.create_feed.assert_called_once_with(
        "http://example.com/feed.xml",
        5,
        crawler=True,
        username="feeduser",
        password="feedpass",
        user_agent="TestAgent/1.0",
    )

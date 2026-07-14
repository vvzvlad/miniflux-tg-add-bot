"""Error-handling behaviour of src/miniflux_api.py against the real miniflux exceptions.

These calls are not retried: one attempt, then a structured error for the caller.
"""

from unittest.mock import MagicMock

import pytest
from miniflux import Client, ClientError, ServerError

from src.miniflux_api import check_feed_exists, delete_feed, fetch_categories, update_feed_url


class MockResponse:
    """Enough of a requests.Response for miniflux.ClientError to work with."""

    def __init__(self, status_code=400, message="Error"):
        self.status_code = status_code
        self.message = message

    def json(self):
        return {"error_message": self.message}


@pytest.fixture
def mock_client():
    """A synchronous mock of the miniflux client (the library is not async)."""
    return MagicMock(spec=Client)


# --- update_feed_url --------------------------------------------------------


def test_update_feed_url_handles_server_error(mock_client):
    """A server error is reported once, with its status code — no retry."""
    mock_client.update_feed = MagicMock(
        side_effect=ServerError(MockResponse(status_code=500, message="Internal Server Error"))
    )

    success, updated_url, error_message = update_feed_url(
        123, "https://example.com/rss/test_channel?flags=FT", mock_client
    )

    assert success is False
    assert updated_url is None
    assert "500" in error_message
    assert "Internal Server Error" in error_message
    assert mock_client.update_feed.call_count == 1


def test_update_feed_url_handles_client_error(mock_client):
    mock_client.update_feed = MagicMock(
        side_effect=ClientError(MockResponse(status_code=400, message="Bad Request"))
    )

    success, updated_url, error_message = update_feed_url(
        123, "https://example.com/rss/test_channel?flags=FT", mock_client
    )

    assert success is False
    assert updated_url is None
    assert "400" in error_message
    assert "Bad Request" in error_message
    assert mock_client.update_feed.call_count == 1


def test_update_feed_url_handles_unexpected_exception(mock_client):
    mock_client.update_feed = MagicMock(side_effect=Exception("Unexpected error"))

    success, updated_url, error_message = update_feed_url(
        123, "https://example.com/rss/test_channel?flags=FT", mock_client
    )

    assert success is False
    assert updated_url is None
    assert "Unexpected error" in error_message
    assert mock_client.update_feed.call_count == 1


# --- delete_feed ------------------------------------------------------------


def test_delete_feed_handles_client_error(mock_client):
    """delete_feed reports a structured failure instead of raising."""
    mock_client.delete_feed = MagicMock(
        side_effect=ClientError(MockResponse(status_code=404, message="Feed not found"))
    )

    success, error_message = delete_feed(mock_client, 42)

    assert success is False
    assert "404" in error_message
    assert "Feed not found" in error_message
    assert mock_client.delete_feed.call_count == 1


def test_delete_feed_handles_server_error(mock_client):
    mock_client.delete_feed = MagicMock(
        side_effect=ServerError(MockResponse(status_code=500, message="Internal Server Error"))
    )

    success, error_message = delete_feed(mock_client, 42)

    assert success is False
    assert "500" in error_message


def test_delete_feed_success(mock_client):
    """The happy path calls the sync client exactly once with the feed id."""
    mock_client.delete_feed = MagicMock(return_value=None)

    success, error_message = delete_feed(mock_client, 42)

    assert success is True
    assert error_message is None
    mock_client.delete_feed.assert_called_once_with(42)


# --- fetch_categories -------------------------------------------------------


def test_fetch_categories_handles_api_error(mock_client):
    """An API error propagates: fetch_categories does not swallow it."""
    mock_client.get_categories.side_effect = ClientError(
        MockResponse(status_code=401, message="Unauthorized")
    )

    with pytest.raises(ClientError):
        fetch_categories(mock_client)

    assert mock_client.get_categories.call_count == 1


def test_fetch_categories_handles_unexpected_error(mock_client):
    mock_client.get_categories.side_effect = Exception("Unexpected network issue")

    with pytest.raises(Exception, match="Unexpected network issue"):
        fetch_categories(mock_client)

    assert mock_client.get_categories.call_count == 1


# --- check_feed_exists ------------------------------------------------------


def test_check_feed_exists_handles_api_error(mock_client):
    mock_client.get_feeds.side_effect = ServerError(
        MockResponse(status_code=503, message="Service Unavailable")
    )

    with pytest.raises(ServerError):
        check_feed_exists(mock_client, "https://example.com/feed.xml")

    assert mock_client.get_feeds.call_count == 1


def test_check_feed_exists_true(mock_client):
    feed_url = "https://example.com/feed.xml"
    mock_client.get_feeds.return_value = [
        {"feed_url": "https://another.com/feed.xml"},
        {"feed_url": feed_url},
        {"feed_url": "https://yetanother.com/feed.xml"},
    ]

    assert check_feed_exists(mock_client, feed_url) is True
    assert mock_client.get_feeds.call_count == 1


def test_check_feed_exists_false(mock_client):
    mock_client.get_feeds.return_value = [
        {"feed_url": "https://another.com/feed.xml"},
        {"feed_url": "https://different.com/feed.xml"},
    ]

    assert check_feed_exists(mock_client, "https://example.com/feed.xml") is False
    assert mock_client.get_feeds.call_count == 1

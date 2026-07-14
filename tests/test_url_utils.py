"""Tests for src/url_utils.py."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.settings import settings
from src.url_utils import (
    TELEGRAM_LINK_RE,
    extract_channel_from_feed_url,
    extract_rss_links_from_html,
    is_valid_rss_url,
    parse_telegram_link,
)

BRIDGE_URL = "http://test.rssbridge.local/rss/{channel}/test_token"


@pytest.fixture(autouse=True)
def bridge_url(monkeypatch):
    """extract_channel_from_feed_url reads the template from settings at call time."""
    monkeypatch.setattr(settings, "rss_bridge_url", BRIDGE_URL)


# --- parse_telegram_link ----------------------------------------------------

def test_parse_telegram_link_channel_only():
    """A channel link without a message id."""
    assert parse_telegram_link("https://t.me/alex_levitas") == "alex_levitas"
    assert parse_telegram_link("http://t.me/alex_levitas") == "alex_levitas"
    assert parse_telegram_link("t.me/alex_levitas") == "alex_levitas"
    assert parse_telegram_link("https://t.me/alex_levitas/") == "alex_levitas"


def test_parse_telegram_link_channel_with_message():
    """A channel link with a message id."""
    assert parse_telegram_link("https://t.me/alex_levitas/1029") == "alex_levitas"
    assert parse_telegram_link("http://t.me/alex_levitas/1029") == "alex_levitas"
    assert parse_telegram_link("t.me/alex_levitas/1029") == "alex_levitas"
    assert parse_telegram_link("https://t.me/alex_levitas/1029/") == "alex_levitas"


def test_parse_telegram_link_numeric_channel_id():
    """A numeric (negative) channel id is a valid channel identifier."""
    assert parse_telegram_link("t.me/-1002069358234/1951") == "-1002069358234"
    assert parse_telegram_link("https://t.me/-1002069358234") == "-1002069358234"


def test_parse_telegram_link_private_channel_c_form():
    """The t.me/c/<id>/<msg> private-channel form yields the numeric id."""
    assert parse_telegram_link("t.me/c/1234567890/55") == "1234567890"
    assert parse_telegram_link("https://t.me/c/1234567890") == "1234567890"


def test_parse_telegram_link_inside_surrounding_text():
    """The link is found anywhere inside the message text, not only at the start."""
    assert parse_telegram_link("Check this out https://t.me/durov/123 — nice") == "durov"
    assert parse_telegram_link("look: t.me/durov") == "durov"


def test_parse_telegram_link_with_query_params():
    """A trailing query string is tolerated (the old code returned None here)."""
    assert parse_telegram_link("https://t.me/channel_name?start=123") == "channel_name"
    assert parse_telegram_link("https://t.me/c/1234567890?start=123") == "1234567890"


def test_parse_telegram_link_with_fragments():
    """A trailing fragment is tolerated (the old code returned None here)."""
    assert parse_telegram_link("https://t.me/channel_name#section") == "channel_name"
    assert parse_telegram_link("https://t.me/c/1234567890#section") == "1234567890"


def test_parse_telegram_link_joinchat_is_not_a_channel():
    """t.me/joinchat/<hash> is an invite link, not a channel: it must not match."""
    assert parse_telegram_link("t.me/joinchat/AAAAAEkk2WdoDrB4") is None
    assert parse_telegram_link("https://t.me/joinchat/abc123") is None


def test_parse_telegram_link_invite_links():
    """t.me/+<hash> invite links yield no channel."""
    assert parse_telegram_link("t.me/+joinchatlink") is None
    assert parse_telegram_link("https://t.me/+AbCdEfGhIjK") is None


def test_parse_telegram_link_preview_prefix_is_not_a_channel():
    """t.me/s/<channel> is the web-preview form, not a channel path we support."""
    assert parse_telegram_link("https://t.me/s/channel_name") is None


def test_parse_telegram_link_empty_input():
    assert parse_telegram_link("") is None
    assert parse_telegram_link(None) is None


def test_parse_telegram_link_plain_text():
    assert parse_telegram_link("plain text") is None
    assert parse_telegram_link("channelname") is None


def test_parse_telegram_link_other_platform_urls():
    assert parse_telegram_link("https://example.com") is None
    assert parse_telegram_link("https://twitter.com/username") is None
    assert parse_telegram_link("https://web.telegram.org/z/#839762") is None


def test_parse_telegram_link_malformed_urls():
    """A bare domain or a hyphenated (thus invalid) username yields nothing."""
    assert parse_telegram_link("https://channel_name") is None
    # Hyphens are not valid in Telegram usernames
    assert parse_telegram_link("https://t.me/channel_name-with-hyphens") is None
    # Underscores are
    assert parse_telegram_link("https://t.me/channel_name_with_underscores") == "channel_name_with_underscores"


def test_parse_telegram_link_is_case_sensitive_on_domain():
    """The domain match is case-sensitive, as the regex is written."""
    assert parse_telegram_link("https://T.ME/CHANNEL_NAME") is None


def test_parse_telegram_link_scheme_is_case_insensitive():
    """The URL scheme is matched case-insensitively (mobile auto-capitalization)."""
    assert parse_telegram_link("Https://t.me/foo") == "foo"
    assert parse_telegram_link("HTTPS://t.me/foo") == "foo"
    assert parse_telegram_link("HTTP://t.me/bar") == "bar"
    # The host stays case-sensitive even with an uppercased scheme.
    assert parse_telegram_link("https://T.ME/foo") is None


def test_parse_telegram_link_empty_channel():
    assert parse_telegram_link("https://t.me/") is None


def test_parse_telegram_link_accepts_valid_forms():
    """Well-formed t.me links (scheme or at a text boundary) yield the channel/id."""
    assert parse_telegram_link("https://t.me/channel/123") == "channel"
    assert parse_telegram_link("t.me/-1002069358234/1951") == "-1002069358234"
    assert parse_telegram_link("t.me/channel") == "channel"
    assert parse_telegram_link("look https://t.me/foo/12 here") == "foo"
    assert parse_telegram_link("https://t.me/foo?single") == "foo"
    assert parse_telegram_link("https://t.me/c/1234567890/55") == "1234567890"
    assert parse_telegram_link("http://t.me/abc") == "abc"
    assert parse_telegram_link("https://t.me/foo#frag") == "foo"
    assert parse_telegram_link("please add t.me/mychan") == "mychan"


def test_parse_telegram_link_rejects_other_me_domains():
    """A ".me" host label ending in "t" must not be mistaken for a t.me link."""
    assert parse_telegram_link("format.me/page") is None
    assert parse_telegram_link("root.me/123") is None
    assert parse_telegram_link("https://list.me/abc") is None
    assert parse_telegram_link("check start.me/foo please") is None
    assert parse_telegram_link("connect.me/user") is None


def test_parse_telegram_link_rejects_embedded():
    """A t.me glued inside another URL's path (or a non-channel form) yields nothing."""
    assert parse_telegram_link("https://example.com/t.me/foo") is None
    assert parse_telegram_link("https://example.com/rss.xml") is None
    assert parse_telegram_link("t.me/joinchat/AAAA") is None
    # A bare "@username" is handled separately, not by parse_telegram_link.
    assert parse_telegram_link("@channelname") is None


def test_telegram_link_re_is_exported():
    """The compiled regex is part of the module API (used for direct matching)."""
    assert TELEGRAM_LINK_RE.search("https://t.me/durov").group(1) == "durov"


# --- extract_channel_from_feed_url ------------------------------------------

def test_extract_channel_from_feed_url_matching_bridge():
    """A feed URL built from the configured template yields its channel."""
    feed_url = "http://test.rssbridge.local/rss/test_channel/test_token"
    assert extract_channel_from_feed_url(feed_url) == "test_channel"


def test_extract_channel_from_feed_url_with_query_params():
    """Query parameters after the channel segment are ignored."""
    feed_url = "http://test.rssbridge.local/rss/test_channel/test_token?exclude_flags=a,b"
    assert extract_channel_from_feed_url(feed_url) == "test_channel"


def test_extract_channel_from_feed_url_url_encoded():
    """URL-encoded characters in the channel segment are decoded."""
    feed_url = "http://test.rssbridge.local/rss/%40test_channel/test_token"
    assert extract_channel_from_feed_url(feed_url) == "@test_channel"


def test_extract_channel_from_feed_url_numeric_id():
    feed_url = "http://test.rssbridge.local/rss/-1002069358234/test_token"
    assert extract_channel_from_feed_url(feed_url) == "-1002069358234"


def test_extract_channel_from_feed_url_mismatched_url():
    """A feed URL from another host does not match the configured bridge."""
    assert extract_channel_from_feed_url("http://different.domain.com/rss/test_channel") is None


def test_extract_channel_from_feed_url_not_matching_pattern(monkeypatch):
    """URLs that resemble the bridge but do not match its base prefix yield None."""
    monkeypatch.setattr(
        settings,
        "rss_bridge_url",
        "http://rssbridge.example.com/?action=display&bridge=Telegram&channel={channel}",
    )
    # Missing action=display
    assert extract_channel_from_feed_url(
        "http://rssbridge.example.com/?bridge=Telegram&channel=test_channel"
    ) is None
    # Different parameter name
    assert extract_channel_from_feed_url(
        "http://rssbridge.example.com/?action=display&bridge=Telegram&user=test_channel"
    ) is None
    # Different domain
    assert extract_channel_from_feed_url(
        "http://different-domain.com/?action=display&bridge=Telegram&channel=test_channel"
    ) is None


def test_extract_channel_from_feed_url_query_template():
    """A query-style bridge template also resolves the channel."""
    monkeypatch_url = "http://rssbridge.example.com/?action=display&bridge=Telegram&channel={channel}"
    with patch.object(settings, "rss_bridge_url", monkeypatch_url):
        feed_url = "http://rssbridge.example.com/?action=display&bridge=Telegram&channel=test_channel"
        assert extract_channel_from_feed_url(feed_url) == "test_channel"


def test_extract_channel_from_feed_url_empty_input():
    assert extract_channel_from_feed_url(None) is None
    assert extract_channel_from_feed_url("") is None


# --- extract_rss_links_from_html --------------------------------------------

def test_extract_rss_links_from_html_with_links():
    html_content = """
    <html>
        <head>
            <link rel="alternate" type="application/rss+xml" title="RSS Feed" href="/rss.xml">
            <link rel="alternate" type="application/atom+xml" title="Atom Feed" href="atom.xml">
        </head>
        <body>Some content</body>
    </html>
    """
    result = extract_rss_links_from_html(html_content, "https://example.com")

    assert result == [
        {"title": "RSS Feed", "href": "https://example.com/rss.xml"},
        {"title": "Atom Feed", "href": "https://example.com/atom.xml"},
    ]


def test_extract_rss_links_from_html_no_links():
    html_content = """
    <html>
        <head><link rel="stylesheet" href="style.css"></head>
        <body>Some content</body>
    </html>
    """
    assert extract_rss_links_from_html(html_content, "https://example.com") == []


def test_extract_rss_links_from_html_exception():
    """A parser blow-up is contained: an empty list, never an exception."""
    with patch("src.url_utils.BeautifulSoup", side_effect=Exception("Test error")):
        assert extract_rss_links_from_html("some html", "https://example.com") == []


# --- is_valid_rss_url -------------------------------------------------------

@patch("requests.head")
def test_is_valid_rss_url_direct_feed_head(mock_head):
    """A direct feed is recognized from the HEAD Content-Type."""
    test_url = "https://example.com/feed.xml"
    mock_head.return_value = MagicMock(
        status_code=200, headers={"Content-Type": "application/rss+xml"}
    )

    assert is_valid_rss_url(test_url) == (True, test_url)
    mock_head.assert_called_once()


@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_direct_feed_get(mock_head, mock_get):
    """HEAD says HTML, GET says feed: the GET verdict wins."""
    test_url = "https://example.com/feed.xml"
    mock_head.return_value = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    mock_get.return_value = MagicMock(
        status_code=200, headers={"Content-Type": "application/rss+xml"}
    )

    assert is_valid_rss_url(test_url) == (True, test_url)
    mock_head.assert_called_once()
    mock_get.assert_called_once()


@patch("src.url_utils.extract_rss_links_from_html")
@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_html_with_links(mock_head, mock_get, mock_extract):
    """An HTML page with feed links returns the links."""
    test_url = "https://example.com/page.html"
    expected_links = [{"title": "RSS Feed", "href": "https://example.com/rss.xml"}]

    mock_head.return_value = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    mock_get.return_value = MagicMock(
        status_code=200, headers={"Content-Type": "text/html"}, text="<html>Some HTML</html>"
    )
    mock_extract.return_value = expected_links

    assert is_valid_rss_url(test_url) == (False, expected_links)
    mock_extract.assert_called_once_with("<html>Some HTML</html>", test_url)


@patch("src.url_utils.extract_rss_links_from_html")
@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_html_no_links(mock_head, mock_get, mock_extract):
    """An HTML page without feed links returns an empty list."""
    test_url = "https://example.com/page.html"
    mock_head.return_value = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    mock_get.return_value = MagicMock(
        status_code=200, headers={"Content-Type": "text/html"}, text="<html>Some HTML</html>"
    )
    mock_extract.return_value = []

    assert is_valid_rss_url(test_url) == (False, [])


@patch("requests.head")
def test_is_valid_rss_url_head_error(mock_head):
    """A failing HEAD falls through to GET; if that fails too, nothing is found."""
    mock_head.side_effect = requests.exceptions.RequestException("Connection error")

    with patch("requests.get", side_effect=requests.exceptions.RequestException("Connection error")):
        assert is_valid_rss_url("https://example.com/feed.xml") == (False, [])
    mock_head.assert_called_once()


@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_get_error(mock_head, mock_get):
    mock_head.return_value = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    mock_get.side_effect = requests.exceptions.RequestException("Connection error")

    assert is_valid_rss_url("https://example.com/feed.xml") == (False, [])
    mock_get.assert_called_once()


@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_unexpected_content_type(mock_head, mock_get):
    """A non-feed, non-HTML Content-Type stops the check after HEAD."""
    mock_head.return_value = MagicMock(status_code=200, headers={"Content-Type": "image/jpeg"})

    assert is_valid_rss_url("https://example.com/image.jpg") == (False, [])
    mock_get.assert_not_called()


@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_json_content_type(mock_head, mock_get):
    mock_head.return_value = MagicMock(status_code=200, headers={"Content-Type": "application/json"})

    assert is_valid_rss_url("https://example.com/api.json") == (False, [])
    mock_get.assert_not_called()


@pytest.mark.parametrize(
    "exception",
    [
        requests.exceptions.Timeout("Connection timed out"),
        requests.exceptions.ConnectionError("Connection refused"),
        requests.exceptions.TooManyRedirects("Too many redirects"),
        requests.exceptions.SSLError("SSL Certificate Verification Failed"),
        requests.exceptions.HTTPError("404 Not Found"),
        requests.exceptions.InvalidURL("Invalid URL format"),
    ],
)
def test_is_valid_rss_url_request_exceptions(exception):
    """Every requests-level failure is contained and reported as 'nothing found'."""
    with patch("requests.head", side_effect=exception), \
         patch("requests.get", side_effect=exception):
        assert is_valid_rss_url("https://example.com/feed.xml") == (False, [])


@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_no_content_type(mock_head, mock_get):
    """A missing Content-Type on HEAD still tries a GET."""
    mock_head.return_value = MagicMock(status_code=200, headers={})
    mock_get.return_value = MagicMock(
        status_code=200, headers={}, text="<html><body>Not an RSS feed</body></html>"
    )

    with patch("src.url_utils.extract_rss_links_from_html", return_value=[]):
        assert is_valid_rss_url("https://example.com/unknown-type") == (False, [])

    mock_head.assert_called_once()
    mock_get.assert_called_once()


@patch("requests.get")
@patch("requests.head")
def test_is_valid_rss_url_general_exception(mock_head, mock_get):
    """An unexpected error during link extraction is contained."""
    mock_head.return_value = MagicMock(status_code=200, headers={"Content-Type": "text/html"})
    mock_get.return_value = MagicMock(
        status_code=200, headers={"Content-Type": "text/html"}, text="<html>Some HTML</html>"
    )

    with patch("src.url_utils.extract_rss_links_from_html", side_effect=Exception("Unexpected error")):
        assert is_valid_rss_url("https://example.com/problematic-page") == (False, [])

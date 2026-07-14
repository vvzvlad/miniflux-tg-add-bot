"""Edge cases for src/url_utils.py: odd URLs, odd HTML, odd Telegram links."""

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.url_utils import extract_rss_links_from_html, is_valid_rss_url, parse_telegram_link


@pytest.mark.parametrize("url,expected", [
    # Non-HTTP/HTTPS schemes
    ("ftp://example.com/file.xml", (False, [])),
    ("mailto:user@example.com", (False, [])),
    ("telnet://example.com", (False, [])),
    ("data:text/plain;base64,SGVsbG8sIFdvcmxkIQ==", (False, [])),

    # Malformed URLs
    ("http:example.com", (False, [])),
    ("https:/example.com", (False, [])),
    ("http//example.com", (False, [])),
    ("example..com/rss", (False, [])),

    # URLs with unusual characters or encodings
    ("https://example.com/feed%20with%20spaces.xml", (True, "https://example.com/feed%20with%20spaces.xml")),
    ("https://example.com/rss/feed?q=test&format=xml#fragment", (True, "https://example.com/rss/feed?q=test&format=xml#fragment")),
    ("https://user:pass@example.com/secure-feed.xml", (True, "https://user:pass@example.com/secure-feed.xml")),

    # Internationalized Domain Names (IDNs) — the request fails, nothing is found
    ("https://München.de/feed.xml", (False, [])),
    ("https://правительство.рф/feed.xml", (False, [])),

    # XSS attack attempts
    ("javascript:alert('XSS')", (False, [])),
    ("data:text/html,<script>alert('XSS')</script>", (False, [])),

    # Empty or None
    ("", (False, [])),
    (None, (False, [])),
])
def test_is_valid_rss_url_edge_cases(url, expected):
    """URL validation across schemes, malformed input and hostile input."""
    with patch("src.url_utils.requests.head") as mock_head, \
         patch("src.url_utils.requests.get") as mock_get:
        if url and (url.startswith("http://") or url.startswith("https://")):
            response = MagicMock(
                status_code=200, headers={"Content-Type": "application/rss+xml"}
            )
            if "München" in str(url) or "правительство" in str(url):
                # An IDN host the transport cannot resolve
                mock_head.side_effect = requests.exceptions.RequestException("IDN Error")
                mock_get.side_effect = requests.exceptions.RequestException("IDN Error")
            else:
                mock_head.return_value = response
                mock_get.return_value = response
        else:
            mock_head.side_effect = requests.exceptions.RequestException("Invalid URL")
            mock_get.side_effect = requests.exceptions.RequestException("Invalid URL")

        assert is_valid_rss_url(url) == expected


@pytest.mark.parametrize("url,expected", [
    # Standard channel URL
    ("t.me/channel_name", "channel_name"),
    ("https://t.me/channel_name", "channel_name"),

    # Channel URL with post ID
    ("t.me/channel_name/123", "channel_name"),
    ("https://t.me/channel_name/123", "channel_name"),

    # A trailing query string / fragment is tolerated
    ("https://t.me/channel_name?query=value", "channel_name"),
    ("https://t.me/channel_name#anchor", "channel_name"),

    # Private channel forms
    ("https://t.me/c/1234567890/55", "1234567890"),
    ("https://t.me/-1002069358234/1951", "-1002069358234"),

    # Invite links are not channels
    ("https://t.me/+AbCdEfGhIjK", None),
    ("https://t.me/joinchat/AbCdEfGhIjK", None),

    # The web-preview prefix is not a channel path
    ("https://t.me/s/channel_name", None),

    # Non-Telegram URL
    ("https://example.com", None),

    # Malformed Telegram URL
    ("https://t.me/", None),

    # Special characters in the channel name
    ("https://t.me/channel_name-with-hyphens", None),
    ("https://t.me/channel_name_with_underscores", "channel_name_with_underscores"),

    # The domain match is case-sensitive
    ("https://T.ME/CHANNEL_NAME", None),
])
def test_parse_telegram_link_variations(url, expected):
    """Variations of Telegram links, checked against the real regex."""
    assert parse_telegram_link(url) == expected


@pytest.mark.parametrize("html_content,expected_links", [
    # No links
    ("<html><body>No RSS links here</body></html>", []),

    # Link tag with an absolute href
    ('''<html><head>
        <link rel="alternate" type="application/rss+xml" title="RSS Feed" href="https://example.com/feed.xml">
      </head></html>''',
     [{"title": "RSS Feed", "href": "https://example.com/feed.xml"}]),

    # Multiple link tags with relative URLs
    ('''<html><head>
        <link rel="alternate" type="application/rss+xml" title="Main Feed" href="/feed.xml">
        <link rel="alternate" type="application/atom+xml" title="Atom Feed" href="/atom.xml">
      </head></html>''',
     [{"title": "Main Feed", "href": "https://example.com/feed.xml"},
      {"title": "Atom Feed", "href": "https://example.com/atom.xml"}]),

    # A link without a title attribute gets the default title
    ('''<html><head>
        <link rel="alternate" type="application/rss+xml" href="/rss">
      </head></html>''',
     [{"title": "RSS/Atom Feed", "href": "https://example.com/rss"}]),

    # Relative URLs of different shapes
    ('''<html><head>
        <link rel="alternate" type="application/rss+xml" title="Feed 1" href="feed.xml">
        <link rel="alternate" type="application/rss+xml" title="Feed 2" href="./feed2.xml">
        <link rel="alternate" type="application/rss+xml" title="Feed 3" href="../feed3.xml">
      </head></html>''',
     [{"title": "Feed 1", "href": "https://example.com/feed.xml"},
      {"title": "Feed 2", "href": "https://example.com/feed2.xml"},
      {"title": "Feed 3", "href": "https://example.com/feed3.xml"}]),
])
def test_extract_rss_links_variations(html_content, expected_links):
    """Finding RSS links in different HTML structures."""
    result = extract_rss_links_from_html(html_content, "https://example.com")

    assert sorted(result, key=lambda item: item["href"]) == sorted(
        expected_links, key=lambda item: item["href"]
    )

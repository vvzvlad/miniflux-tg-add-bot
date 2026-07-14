"""Tests for src/url_constructor.py."""

import urllib.parse

import pytest

from src.url_constructor import build_feed_url, parse_feed_url

# --- parse_feed_url ---------------------------------------------------------


@pytest.mark.parametrize(
    "feed_url, mock_channel, expected_result",
    [
        # Simple URL, no params
        (
            "http://test.bridge/rss/channel1",
            "channel1",
            {
                "base_url": "http://test.bridge/rss/channel1",
                "channel_name": "channel1",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": None,
            },
        ),
        # URL with all parameters
        (
            "http://test.bridge/rss/channel2?exclude_flags=flag1,flag2&exclude_text=filter%20me&merge_seconds=300",
            "channel2",
            {
                "base_url": "http://test.bridge/rss/channel2",
                "channel_name": "channel2",
                "flags": ["flag1", "flag2"],
                "exclude_text": "filter me",
                "merge_seconds": 300,
            },
        ),
        # Only flags
        (
            "http://test.bridge/rss/channel3?exclude_flags=nofilter",
            "channel3",
            {
                "base_url": "http://test.bridge/rss/channel3",
                "channel_name": "channel3",
                "flags": ["nofilter"],
                "exclude_text": None,
                "merge_seconds": None,
            },
        ),
        # Only exclude_text
        (
            "http://test.bridge/rss/channel4?exclude_text=pattern",
            "channel4",
            {
                "base_url": "http://test.bridge/rss/channel4",
                "channel_name": "channel4",
                "flags": None,
                "exclude_text": "pattern",
                "merge_seconds": None,
            },
        ),
        # Only merge_seconds
        (
            "http://test.bridge/rss/channel5?merge_seconds=60",
            "channel5",
            {
                "base_url": "http://test.bridge/rss/channel5",
                "channel_name": "channel5",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": 60,
            },
        ),
        # Empty exclude_flags parameter must not become ['']
        (
            "http://test.bridge/rss/channel6?exclude_flags=&exclude_text=test",
            "channel6",
            {
                "base_url": "http://test.bridge/rss/channel6",
                "channel_name": "channel6",
                "flags": None,
                "exclude_text": "test",
                "merge_seconds": None,
            },
        ),
        # Invalid merge_seconds falls back to None
        (
            "http://test.bridge/rss/channel7?merge_seconds=abc",
            "channel7",
            {
                "base_url": "http://test.bridge/rss/channel7",
                "channel_name": "channel7",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": None,
            },
        ),
        # Numeric channel ID
        (
            "http://test.bridge/rss/-10012345?merge_seconds=10",
            "-10012345",
            {
                "base_url": "http://test.bridge/rss/-10012345",
                "channel_name": "-10012345",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": 10,
            },
        ),
        # exclude_text with URL-encoded Russian characters ('|' is %7C)
        (
            "http://test.bridge/rss/channelRus?exclude_text=%D1%80%D0%B5%D0%BA%D0%BB%D0%B0%D0%BC%D0%B0%7C%D1%81%D0%BF%D0%B0%D0%BC%7C%D1%81%D0%B1%D0%BE%D1%80%7C%D0%BF%D0%BE%D0%B4%D0%BF%D0%B8%D1%81%D0%BA%D0%B0",
            "channelRus",
            {
                "base_url": "http://test.bridge/rss/channelRus",
                "channel_name": "channelRus",
                "flags": None,
                "exclude_text": "реклама|спам|сбор|подписка",
                "merge_seconds": None,
            },
        ),
    ],
)
def test_parse_feed_url(mocker, feed_url, mock_channel, expected_result):
    """parse_feed_url decomposes a bridge feed URL into its parts."""
    # The channel extraction depends on the configured bridge template; stub it out
    # so these cases stay focused on the query-string parsing.
    mock_extract = mocker.patch("src.url_constructor.extract_channel_from_feed_url")
    mock_extract.return_value = mock_channel

    result = parse_feed_url(feed_url)

    assert result == expected_result
    mock_extract.assert_called_once_with(feed_url)


# --- build_feed_url ---------------------------------------------------------


@pytest.mark.parametrize(
    "base_url, channel_name, flags, exclude_text, merge_seconds, expected_url",
    [
        # No optional parameters
        (
            "http://test.bridge/rss/channel1", "channel1",
            None, None, None,
            "http://test.bridge/rss/channel1",
        ),
        # All parameters provided
        (
            "http://test.bridge/rss/channel2", "channel2",
            ["flag1", "flag2"], "filter+me", 300,
            "http://test.bridge/rss/channel2?exclude_flags=flag1,flag2&exclude_text=filter%2Bme&merge_seconds=300",
        ),
        # Only flags
        (
            "http://test.bridge/rss/channel3", "channel3",
            ["nofilter"], None, None,
            "http://test.bridge/rss/channel3?exclude_flags=nofilter",
        ),
        # Only exclude_text (needs encoding)
        (
            "http://test.bridge/rss/channel4", "channel4",
            None, "a=b&c=d", None,
            "http://test.bridge/rss/channel4?exclude_text=a%3Db%26c%3Dd",
        ),
        # Only merge_seconds
        (
            "http://test.bridge/rss/channel5", "channel5",
            None, None, 60,
            "http://test.bridge/rss/channel5?merge_seconds=60",
        ),
        # Empty flags list is omitted
        (
            "http://test.bridge/rss/channel6", "channel6",
            [], "test", None,
            "http://test.bridge/rss/channel6?exclude_text=test",
        ),
        # merge_seconds = 0 is omitted (it means "disabled")
        (
            "http://test.bridge/rss/channel7", "channel7",
            ["f1"], None, 0,
            "http://test.bridge/rss/channel7?exclude_flags=f1",
        ),
        # A base URL that already carries a query string
        (
            "http://test.bridge/rss/channel8?existing=param", "channel8",
            ["newflag"], None, None,
            "http://test.bridge/rss/channel8?existing=param&exclude_flags=newflag",
        ),
        # An empty exclude_text is omitted
        (
            "http://test.bridge/rss/channel9", "channel9",
            None, "", 120,
            "http://test.bridge/rss/channel9?merge_seconds=120",
        ),
        # exclude_text with Russian characters
        (
            "http://test.bridge/rss/channel10", "channel10",
            None, "русский текст", None,
            "http://test.bridge/rss/channel10?exclude_text=%D1%80%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9%20%D1%82%D0%B5%D0%BA%D1%81%D1%82",
        ),
        # exclude_text with Russian characters and a pipe (must encode as %7C)
        (
            "http://test.bridge/rss/channelRusPipe", "channelRusPipe",
            None, "реклама|спам|сбор|подписка", None,
            "http://test.bridge/rss/channelRusPipe?exclude_text=%D1%80%D0%B5%D0%BA%D0%BB%D0%B0%D0%BC%D0%B0%7C%D1%81%D0%BF%D0%B0%D0%BC%7C%D1%81%D0%B1%D0%BE%D1%80%7C%D0%BF%D0%BE%D0%B4%D0%BF%D0%B8%D1%81%D0%BA%D0%B0",
        ),
    ],
)
def test_build_feed_url(base_url, channel_name, flags, exclude_text, merge_seconds, expected_url):
    """build_feed_url renders the query string exactly as the bridge expects it."""
    result = build_feed_url(base_url, channel_name, flags, exclude_text, merge_seconds)

    expected_parsed = urllib.parse.urlparse(expected_url)
    result_parsed = urllib.parse.urlparse(result)

    assert result_parsed.scheme == expected_parsed.scheme
    assert result_parsed.netloc == expected_parsed.netloc
    assert result_parsed.path == expected_parsed.path
    # Compare the raw query string: the exact format matters (commas, not %2C)
    assert result_parsed.query == expected_parsed.query


def test_parse_and_build_round_trip(mocker):
    """A URL parsed and rebuilt keeps its flags, regex and merge time."""
    mocker.patch("src.url_constructor.extract_channel_from_feed_url", return_value="chan")
    original = "http://test.bridge/rss/chan?exclude_flags=fwd,video&exclude_text=spam&merge_seconds=300"

    parsed = parse_feed_url(original)
    rebuilt = build_feed_url(
        base_url=parsed["base_url"],
        channel_name=parsed["channel_name"],
        flags=parsed["flags"],
        exclude_text=parsed["exclude_text"],
        merge_seconds=parsed["merge_seconds"],
    )

    assert parse_feed_url(rebuilt) == parsed

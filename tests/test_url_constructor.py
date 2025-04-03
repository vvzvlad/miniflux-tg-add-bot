import pytest
import sys
import os
from unittest.mock import patch
import urllib.parse

# Adjust sys.path to import from the parent directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, project_root)

from url_constructor import parse_feed_url, build_feed_url

# --- Tests for parse_feed_url ---

@pytest.mark.parametrize(
    "feed_url, mock_channel, expected_result",
    [
        # Test case 1: Simple URL, no params
        (
            "http://test.bridge/rss/channel1",
            "channel1",
            {
                "base_url": "http://test.bridge/rss/channel1",
                "channel_name": "channel1",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": None,
            }
        ),
        # Test case 2: URL with all parameters
        (
            "http://test.bridge/rss/channel2?exclude_flags=flag1,flag2&exclude_text=filter%20me&merge_seconds=300",
            "channel2",
            {
                "base_url": "http://test.bridge/rss/channel2",
                "channel_name": "channel2",
                "flags": ["flag1", "flag2"],
                "exclude_text": "filter me",
                "merge_seconds": 300,
            }
        ),
        # Test case 3: URL with only flags
        (
            "http://test.bridge/rss/channel3?exclude_flags=nofilter",
            "channel3",
            {
                "base_url": "http://test.bridge/rss/channel3",
                "channel_name": "channel3",
                "flags": ["nofilter"],
                "exclude_text": None,
                "merge_seconds": None,
            }
        ),
        # Test case 4: URL with only exclude_text
        (
            "http://test.bridge/rss/channel4?exclude_text=pattern",
            "channel4",
            {
                "base_url": "http://test.bridge/rss/channel4",
                "channel_name": "channel4",
                "flags": None,
                "exclude_text": "pattern",
                "merge_seconds": None,
            }
        ),
        # Test case 5: URL with only merge_seconds
        (
            "http://test.bridge/rss/channel5?merge_seconds=60",
            "channel5",
            {
                "base_url": "http://test.bridge/rss/channel5",
                "channel_name": "channel5",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": 60,
            }
        ),
        # Test case 6: Empty exclude_flags parameter
        (
            "http://test.bridge/rss/channel6?exclude_flags=&exclude_text=test",
             "channel6",
            {
                "base_url": "http://test.bridge/rss/channel6",
                "channel_name": "channel6",
                "flags": None, # Should be None, not ['']
                "exclude_text": "test",
                "merge_seconds": None,
            }
        ),
        # Test case 7: Invalid merge_seconds
        (
            "http://test.bridge/rss/channel7?merge_seconds=abc",
            "channel7",
            {
                "base_url": "http://test.bridge/rss/channel7",
                "channel_name": "channel7",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": None, # Should default to None
            }
        ),
        # Test case 8: URL with numeric channel ID
        (
            "http://test.bridge/rss/-10012345?merge_seconds=10",
            "-10012345",
            {
                "base_url": "http://test.bridge/rss/-10012345",
                "channel_name": "-10012345",
                "flags": None,
                "exclude_text": None,
                "merge_seconds": 10,
            }
        ),
        # Test case 9: exclude_text with URL-encoded Russian characters
        (
            "http://test.bridge/rss/channelRus?exclude_text=%D1%80%D0%B5%D0%BA%D0%BB%D0%B0%D0%BC%D0%B0%7C%D1%81%D0%BF%D0%B0%D0%BC%7C%D1%81%D0%B1%D0%BE%D1%80%7C%D0%BF%D0%BE%D0%B4%D0%BF%D0%B8%D1%81%D0%BA%D0%B0", # URL-encoded '|' is %7C
            "channelRus",
            {
                "base_url": "http://test.bridge/rss/channelRus",
                "channel_name": "channelRus",
                "flags": None,
                "exclude_text": "реклама|спам|сбор|подписка", # Expected decoded string
                "merge_seconds": None,
            }
        ),
    ]
)
def test_parse_feed_url(mocker, feed_url, mock_channel, expected_result):
    """Tests the parse_feed_url function with various inputs."""
    # Mock the dependency using mocker fixture
    mock_extract = mocker.patch('url_constructor.extract_channel_from_feed_url')
    mock_extract.return_value = mock_channel
    
    result = parse_feed_url(feed_url)
    
    # Assert the result matches the expected dictionary
    assert result == expected_result
    # Assert the dependency was called correctly
    mock_extract.assert_called_once_with(feed_url)

# --- Tests for build_feed_url ---

@pytest.mark.parametrize(
    "base_url, channel_name, flags, exclude_text, merge_seconds, expected_url",
    [
        # Test case 1: No optional parameters
        (
            "http://test.bridge/rss/channel1", "channel1",
            None, None, None,
            "http://test.bridge/rss/channel1"
        ),
        # Test case 2: All parameters provided
        (
            "http://test.bridge/rss/channel2", "channel2",
            ["flag1", "flag2"], "filter+me", 300,
            "http://test.bridge/rss/channel2?exclude_flags=flag1,flag2&exclude_text=filter%2Bme&merge_seconds=300"
        ),
        # Test case 3: Only flags
        (
            "http://test.bridge/rss/channel3", "channel3",
            ["nofilter"], None, None,
            "http://test.bridge/rss/channel3?exclude_flags=nofilter"
        ),
        # Test case 4: Only exclude_text (needs encoding)
        (
            "http://test.bridge/rss/channel4", "channel4",
            None, "a=b&c=d", None,
            "http://test.bridge/rss/channel4?exclude_text=a%3Db%26c%3Dd"
        ),
        # Test case 5: Only merge_seconds
        (
            "http://test.bridge/rss/channel5", "channel5",
            None, None, 60,
            "http://test.bridge/rss/channel5?merge_seconds=60"
        ),
        # Test case 6: Empty flags list
        (
            "http://test.bridge/rss/channel6", "channel6",
            [], "test", None,
            "http://test.bridge/rss/channel6?exclude_text=test"
        ),
        # Test case 7: merge_seconds = 0 (should not be included)
        (
            "http://test.bridge/rss/channel7", "channel7",
            ["f1"], None, 0,
            "http://test.bridge/rss/channel7?exclude_flags=f1"
        ),
        # Test case 8: Base URL already has query params
        (
            "http://test.bridge/rss/channel8?existing=param", "channel8",
            ["newflag"], None, None,
            "http://test.bridge/rss/channel8?existing=param&exclude_flags=newflag"
        ),
        # Test case 9: exclude_text is empty string (should be omitted)
        (
            "http://test.bridge/rss/channel9", "channel9",
            None, "", 120, # Empty string for exclude_text
            "http://test.bridge/rss/channel9?merge_seconds=120" # exclude_text parameter should be omitted
        ),
        # Test case 10: exclude_text with Russian characters
        (
            "http://test.bridge/rss/channel10", "channel10",
            None, "русский текст", None,
            "http://test.bridge/rss/channel10?exclude_text=%D1%80%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9%20%D1%82%D0%B5%D0%BA%D1%81%D1%82" # Check URL encoding
        ),
        # Test case 11: exclude_text with Russian characters and pipe symbol
        (
            "http://test.bridge/rss/channelRusPipe", "channelRusPipe",
            None, "реклама|спам|сбор|подписка", None,
            "http://test.bridge/rss/channelRusPipe?exclude_text=%D1%80%D0%B5%D0%BA%D0%BB%D0%B0%D0%BC%D0%B0%7C%D1%81%D0%BF%D0%B0%D0%BC%7C%D1%81%D0%B1%D0%BE%D1%80%7C%D0%BF%D0%BE%D0%B4%D0%BF%D0%B8%D1%81%D0%BA%D0%B0" # Ensure | is encoded as %7C
        ),
    ]
)
def test_build_feed_url(base_url, channel_name, flags, exclude_text, merge_seconds, expected_url):
    """Tests the build_feed_url function with various inputs."""
    result = build_feed_url(base_url, channel_name, flags, exclude_text, merge_seconds)
    # We might need to parse the query strings to compare them reliably
    # as the order of parameters might differ.
    expected_parsed = urllib.parse.urlparse(expected_url)
    result_parsed = urllib.parse.urlparse(result)
    
    # Compare base URL parts
    assert result_parsed.scheme == expected_parsed.scheme
    assert result_parsed.netloc == expected_parsed.netloc
    assert result_parsed.path == expected_parsed.path

    # Compare raw query strings to ensure exact format (including commas vs %2C)
    assert result_parsed.query == expected_parsed.query 
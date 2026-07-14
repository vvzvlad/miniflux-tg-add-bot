"""The /list command must respect Telegram's message length limit."""

from unittest.mock import patch

import pytest

from src.handlers.commands import MAX_MESSAGE_LENGTH, list_channels


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


async def test_message_chunking_for_long_list(mock_update, mock_context):
    """A category too large for one message is split across several."""
    many_feeds = [
        {
            "id": i,
            "title": f"Feed{i}_" + "x" * 200,
            "flags": [],
            "excluded_text": None,
            "merge_seconds": None,
        }
        for i in range(1, 100)
    ]
    for i in range(20):
        many_feeds[i]["flags"] = ["some_flag", "another_flag", "third_flag", "fourth_flag"]
    for i in range(30, 50):
        many_feeds[i]["excluded_text"] = f"excluded_pattern_{i}_" + "y" * 100

    with patch(
        "src.handlers.commands.get_channels_by_category",
        return_value={"TestCategory": many_feeds},
    ):
        await list_channels(mock_update, mock_context)

    call_count = mock_update.message.reply_text.call_count
    # Header + at least two chunks
    assert call_count >= 3, f"Expected at least 3 calls to reply_text, got {call_count}"

    intro_text = mock_update.message.reply_text.call_args_list[0][0][0]
    assert "Subscribed channels by category" in intro_text

    first_chunk = mock_update.message.reply_text.call_args_list[1][0][0]
    assert "TestCategory" in first_chunk

    last_chunk = mock_update.message.reply_text.call_args_list[-1][0][0]
    assert "(continued)" in last_chunk

    for call in mock_update.message.reply_text.call_args_list:
        assert len(call[0][0]) <= MAX_MESSAGE_LENGTH


async def test_no_chunking_for_short_list(mock_update, mock_context):
    """A short listing fits in a single message per category."""
    few_feeds = [
        {"id": i, "title": f"Feed{i}", "flags": [], "excluded_text": None, "merge_seconds": None}
        for i in range(1, 5)
    ]

    with patch(
        "src.handlers.commands.get_channels_by_category",
        return_value={"TestCategory": few_feeds},
    ):
        await list_channels(mock_update, mock_context)

    # Header + exactly one category message
    assert mock_update.message.reply_text.call_count == 2

    intro_text = mock_update.message.reply_text.call_args_list[0][0][0]
    assert "Subscribed channels by category" in intro_text

    message_text = mock_update.message.reply_text.call_args_list[1][0][0]
    assert "TestCategory" in message_text
    for i in range(1, 5):
        assert f"Feed{i}" in message_text


async def test_chunk_never_holds_only_a_continuation_header(mock_update, mock_context):
    """The tail chunk is dropped when it would carry nothing but the header."""
    # One line just under the limit: the split must not leave an empty trailing chunk
    feeds = [
        {"id": 1, "title": "a" * (MAX_MESSAGE_LENGTH - 100), "flags": [], "excluded_text": None,
         "merge_seconds": None},
        {"id": 2, "title": "b" * (MAX_MESSAGE_LENGTH - 100), "flags": [], "excluded_text": None,
         "merge_seconds": None},
    ]

    with patch("src.handlers.commands.get_channels_by_category", return_value={"Cat": feeds}):
        await list_channels(mock_update, mock_context)

    for call in mock_update.message.reply_text.call_args_list:
        text = call[0][0]
        assert text.strip() not in ("📁 Cat (continued)", "📁 Cat")
        assert len(text) <= MAX_MESSAGE_LENGTH

"""Regression tests for the 14 bugs fixed in the restructuring.

Each test here is the proof that a specific "glitch" is gone. They are written to
fail loudly if the corresponding bug is reintroduced.
"""

import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import BadRequest

import src.handlers.keyboards as keyboards
from src.handlers.callbacks import _handle_delete_channel, _handle_flag_toggle, button_callback
from src.handlers.common import safe_edit_message
from src.handlers.keyboards import build_options_view, create_flag_keyboard, get_available_flags

# Bound at import time, before the autouse patch_available_flags fixture replaces
# the module attribute — this stays the genuine function.
from src.handlers.keyboards import fetch_available_flags as real_fetch_available_flags
from src.handlers.messages import handle_message
from src.settings import settings

BRIDGE_URL = "http://test.rssbridge.local/rss/{channel}/test_token"


@pytest.fixture(autouse=True)
def use_admin_settings(admin_settings):
    return admin_settings


def feed_url_for(channel: str, query: str = "") -> str:
    return BRIDGE_URL.replace("{channel}", channel) + query


# --- Bug: `await client.delete_feed(...)` on a sync client ('bot glitches') ---


async def test_delete_flow_reports_success_and_calls_sync_delete(mock_query, mock_context, mock_miniflux_client):
    """_handle_delete_channel must call the SYNCHRONOUS delete_feed and report success.

    The old code did `await miniflux_client.delete_feed(...)`. Because the test
    client was an AsyncMock, that stray await "worked" in tests and hid a
    production TypeError. With a plain MagicMock client, a re-introduced await
    would raise, and the success message below would never appear.
    """
    channel = "channel_to_delete"
    mock_miniflux_client.get_feeds.return_value = [{"id": 777, "feed_url": feed_url_for(channel)}]

    await _handle_delete_channel(mock_query, channel)

    mock_miniflux_client.delete_feed.assert_called_once_with(777)
    # It is a plain MagicMock, so the return value is NOT awaitable
    assert not hasattr(mock_miniflux_client.delete_feed.return_value, "__await__")

    mock_query.edit_message_text.assert_called_once()
    message = mock_query.edit_message_text.call_args[0][0]
    assert f"Channel @{channel} has been deleted from subscriptions." in message
    # It must be the success message, not an error
    assert "Failed" not in message


async def test_delete_feed_client_method_is_not_async(mock_miniflux_client):
    """Guard: the fixture client's delete_feed is a sync MagicMock, never AsyncMock."""
    result = mock_miniflux_client.delete_feed(1)
    assert not hasattr(result, "__await__"), "delete_feed must be a sync MagicMock"


# --- Bug: buttons break after every restart (empty user_data) ----------------


async def test_flag_toggle_works_with_empty_user_data(mock_query, mock_context, mock_miniflux_client):
    """_handle_flag_toggle must resolve the feed via find_feed_by_channel.

    user_data does not survive a bot restart, so the handler cannot rely on a
    cached feed_id. It must look the feed up by channel name every time.
    """
    channel = "test_channel"
    mock_context.user_data = {}  # As if the bot had just restarted
    mock_miniflux_client.get_feeds.return_value = [{"id": 5, "feed_url": feed_url_for(channel)}]

    with patch("src.handlers.callbacks.update_feed_url", return_value=(True, "url", None)) as mock_update_api:
        await _handle_flag_toggle(mock_query, mock_context, "add", "video", channel)

    # The feed was found from Miniflux, not from user_data
    mock_miniflux_client.get_feeds.assert_called_once()
    mock_update_api.assert_called_once()
    assert "added" in mock_query.edit_message_text.call_args[0][0]


# --- Bug: safe_edit_message must swallow only "not modified" -----------------


async def test_safe_edit_message_swallows_not_modified():
    """Pressing the same button twice raises "not modified"; that is not an error."""
    query = MagicMock()
    query.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))

    # Must not raise
    await safe_edit_message(query, "same text")


async def test_safe_edit_message_reraises_other_bad_request():
    """Any other BadRequest is a real error and must propagate."""
    query = MagicMock()
    query.edit_message_text = AsyncMock(side_effect=BadRequest("Message to edit not found"))

    with pytest.raises(BadRequest, match="Message to edit not found"):
        await safe_edit_message(query, "text")


# --- Bug: the flag toggle edited the message twice ---------------------------


async def test_flag_toggle_edits_message_exactly_once(mock_query, mock_context, mock_miniflux_client):
    """Each _handle_flag_toggle branch edits the message exactly once (was twice)."""
    channel = "test_channel"
    mock_miniflux_client.get_feeds.return_value = [{"id": 5, "feed_url": feed_url_for(channel)}]

    with patch("src.handlers.callbacks.update_feed_url", return_value=(True, "url", None)):
        await _handle_flag_toggle(mock_query, mock_context, "add", "video", channel)

    assert mock_query.edit_message_text.call_count == 1


async def test_flag_toggle_edits_once_on_update_failure(mock_query, mock_context, mock_miniflux_client):
    channel = "test_channel"
    mock_miniflux_client.get_feeds.return_value = [{"id": 5, "feed_url": feed_url_for(channel)}]

    with patch("src.handlers.callbacks.update_feed_url", return_value=(False, None, "err")):
        await _handle_flag_toggle(mock_query, mock_context, "add", "video", channel)

    assert mock_query.edit_message_text.call_count == 1


# --- Bug: a failed flag fetch fabricated a fake "no_get_flags" flag -----------


def test_fetch_available_flags_returns_empty_on_failure():
    """A failed bridge fetch must return [] — never a fabricated flag button."""
    with patch("src.handlers.keyboards.requests.get", side_effect=Exception("bridge down")):
        result = real_fetch_available_flags(BRIDGE_URL)

    assert result == []
    assert "no_get_flags" not in result


def test_fetch_available_flags_returns_empty_on_bad_status():
    response = MagicMock()
    response.status_code = 503
    with patch("src.handlers.keyboards.requests.get", return_value=response):
        assert real_fetch_available_flags(BRIDGE_URL) == []


async def test_keyboard_has_no_flag_buttons_when_flags_unavailable(mock_context):
    """With no flags, the keyboard carries only the edit/delete rows and a warning note."""
    keyboards._flags_cache = None
    with patch("src.handlers.keyboards.fetch_available_flags", return_value=[]):
        markup, note = await build_options_view("chan", [], None)

    # No add/remove flag buttons at all
    flag_buttons = [
        button
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and ("add_flag|" in button.callback_data or "remove_flag|" in button.callback_data)
    ]
    assert flag_buttons == []
    assert note == keyboards.FLAGS_UNAVAILABLE_NOTE
    assert "unavailable" in note.lower()
    keyboards._flags_cache = None


async def test_build_options_view_note_empty_when_flags_present(mock_context):
    keyboards._flags_cache = None
    with patch("src.handlers.keyboards.fetch_available_flags", return_value=["video", "fwd"]):
        markup, note = await build_options_view("chan", [], None)

    assert note == ""
    flag_buttons = [
        button
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data and "flag|" in button.callback_data
    ]
    assert flag_buttons  # flags are rendered as buttons
    keyboards._flags_cache = None


# --- Bug: double reply (error + generic help) --------------------------------


async def test_forward_from_group_produces_single_error_reply(mock_update, mock_context):
    """A forward from a group replies with ONE error, not error + help text."""
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": 1, "title": "A Group", "type": "group"}
    }

    await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    message = mock_update.message.reply_text.call_args[0][0]
    assert "forward a message from a channel" in message.lower()
    # The generic help text must NOT be appended
    assert "direct RSS feed URL" not in message


async def test_channel_without_username_produces_single_error_reply(mock_update, mock_context, monkeypatch):
    """A private channel with the flag off replies with ONE error, not two messages."""
    monkeypatch.setattr(settings, "accept_channels_without_username", False)
    mock_update.message.to_dict.return_value = {
        "forward_from_chat": {"id": -100999, "title": "Private", "type": "channel"}
    }

    await handle_message(mock_update, mock_context)

    mock_update.message.reply_text.assert_called_once()
    assert "public username" in mock_update.message.reply_text.call_args[0][0]


# --- Bug: merge-time retry lost its state ------------------------------------


@pytest.mark.parametrize("bad_input", ["not_a_number", "-10", "abc123"])
async def test_merge_time_invalid_input_keeps_state(mock_update, mock_context, mock_miniflux_client, bad_input):
    """Invalid/negative merge time must keep the state so the user can just retry."""
    from src.handlers.messages import _handle_awaiting_merge_time

    mock_context.user_data = {
        "state": "awaiting_merge_time",
        "editing_merge_time_for_channel": "chan",
        "editing_feed_id": 9,
    }
    mock_update.message.text = bad_input
    mock_miniflux_client.get_feed.return_value = {"feed_url": feed_url_for("chan")}

    await _handle_awaiting_merge_time(mock_update, mock_context)

    assert mock_context.user_data["state"] == "awaiting_merge_time"
    assert mock_context.user_data["editing_merge_time_for_channel"] == "chan"
    assert mock_context.user_data["editing_feed_id"] == 9


# --- Bug: a direct RSS URL was lost after a failed subscribe -----------------


async def test_direct_rss_url_survives_failed_subscribe(mock_update, mock_context, mock_miniflux_client):
    """When create_feed fails, direct_rss_url must remain so the user can retry."""
    mock_update.callback_query.data = "cat_1"
    mock_context.user_data = {
        "direct_rss_url": "https://example.com/feed.xml",
        "categories": {1: "News"},
    }

    response = MagicMock()
    response.status_code = 400
    response.json.return_value = {"error_message": "boom"}
    from miniflux import ClientError

    mock_miniflux_client.create_feed.side_effect = ClientError(response)

    await button_callback(mock_update, mock_context)

    assert mock_context.user_data["direct_rss_url"] == "https://example.com/feed.xml"


async def test_channel_title_survives_failed_subscribe(mock_update, mock_context, mock_miniflux_client):
    """Symmetric guarantee for a Telegram channel subscription."""
    mock_update.callback_query.data = "cat_1"
    mock_context.user_data = {"channel_title": "chan", "categories": {1: "News"}}

    response = MagicMock()
    response.status_code = 400
    response.json.return_value = {"error_message": "boom"}
    from miniflux import ClientError

    mock_miniflux_client.create_feed.side_effect = ClientError(response)

    await button_callback(mock_update, mock_context)

    assert mock_context.user_data["channel_title"] == "chan"


# --- Bug: blocking sync calls must not be awaited (no RuntimeWarning) ---------


async def test_no_coroutine_never_awaited_warning_in_delete(mock_query, mock_context, mock_miniflux_client):
    """The delete path must produce no 'coroutine was never awaited' RuntimeWarning."""
    mock_miniflux_client.get_feeds.return_value = [{"id": 1, "feed_url": feed_url_for("chan")}]

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        await _handle_delete_channel(mock_query, "chan")


# --- create_flag_keyboard is async and renders toggles -----------------------


async def test_create_flag_keyboard_is_async_and_renders_toggles():
    """create_flag_keyboard is now a coroutine returning per-flag toggle rows."""
    keyboard = await create_flag_keyboard(
        "chan", current_flags=["video"], available_flags=["video", "fwd"]
    )

    labels = [button.text for row in keyboard for button in row]
    # An enabled flag offers "Remove", a disabled one offers "Add"
    assert any('Remove "video"' in label for label in labels)
    assert any('Add "fwd"' in label for label in labels)
    # The fixed action rows are always present
    assert any("Delete channel" in label for label in labels)
    assert any("Edit Regex" in label for label in labels)


async def test_get_available_flags_caches(monkeypatch):
    """get_available_flags fetches once and serves the cache on the next call."""
    keyboards._flags_cache = None
    calls = {"n": 0}

    def fake_fetch(_url):
        calls["n"] += 1
        return ["video"]

    monkeypatch.setattr(keyboards, "fetch_available_flags", fake_fetch)

    first = await get_available_flags()
    second = await get_available_flags()

    assert first == second == ["video"]
    assert calls["n"] == 1
    keyboards._flags_cache = None

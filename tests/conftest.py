"""Shared test fixtures.

The environment variables must be set BEFORE anything from `src` is imported:
`src.settings` builds its `settings` object at import time and exits(1) when a
required variable is missing.
"""

import os

# --- Required environment (must precede every `src.*` import) ---------------
os.environ.setdefault("TELEGRAM_TOKEN", "test_token")
os.environ.setdefault("ADMIN", "test_admin")
os.environ.setdefault("MINIFLUX_BASE_URL", "http://test.miniflux.local")
os.environ.setdefault("MINIFLUX_USERNAME", "test_user")
os.environ.setdefault("MINIFLUX_PASSWORD", "test_password")
os.environ.setdefault("RSS_BRIDGE_URL", "http://test.rssbridge.local/rss/{channel}/test_token")
os.environ.setdefault("ACCEPT_CHANNELS_WITHOUT_USERNAME", "true")
os.environ.setdefault("LOG_LEVEL", "INFO")

from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

import src.handlers.keyboards as keyboards  # noqa: E402
from src.settings import settings  # noqa: E402

# The flags the fake RSS bridge reports in tests.
TEST_AVAILABLE_FLAGS = ["fwd", "video", "stream", "donat", "clown", "poll"]

# The RSS bridge template configured for the tests (mirrors RSS_BRIDGE_URL above).
TEST_RSS_BRIDGE_URL = "http://test.rssbridge.local/rss/{channel}/test_token"


@pytest.fixture
def mock_miniflux_client():
    """A **synchronous** mock of the Miniflux client.

    Every method is a plain MagicMock, never an AsyncMock: `miniflux.Client` is a
    synchronous library. Mocking its methods as AsyncMock used to hide a real bug
    (`await client.delete_feed(...)` in production code) — with a MagicMock such a
    stray `await` raises TypeError and the test fails loudly, which is the point.
    """
    client = MagicMock()
    client.get_feeds = MagicMock(return_value=[])
    client.get_feed = MagicMock(return_value={})
    client.create_feed = MagicMock(return_value=None)
    client.update_feed = MagicMock(return_value=None)
    client.delete_feed = MagicMock(return_value=None)
    client.get_categories = MagicMock(return_value=[])
    return client


@pytest.fixture(autouse=True)
def patch_get_client(mock_miniflux_client):
    """Hand the mock client to every module that resolves it via get_client().

    The client is no longer a module global: handlers call get_client() at call
    time, so it must be patched in each module where the name is used.
    """
    targets = [
        "src.miniflux_api.get_client",
        "src.handlers.callbacks.get_client",
        "src.handlers.commands.get_client",
        "src.handlers.messages.get_client",
    ]
    patchers = [patch(target, return_value=mock_miniflux_client) for target in targets]
    for patcher in patchers:
        patcher.start()
    yield mock_miniflux_client
    for patcher in patchers:
        patcher.stop()


@pytest.fixture(autouse=True)
def patch_available_flags():
    """Serve a fixed flag list instead of calling the real RSS bridge.

    The flag cache is module-level, so it is cleared around every test to keep the
    tests independent of each other.
    """
    keyboards._flags_cache = None
    with patch(
        "src.handlers.keyboards.fetch_available_flags",
        return_value=list(TEST_AVAILABLE_FLAGS),
    ) as mock_fetch:
        yield mock_fetch
    keyboards._flags_cache = None


@pytest.fixture
def admin_settings(monkeypatch):
    """Settings with predictable values for the handler tests."""
    monkeypatch.setattr(settings, "admin", "test_admin")
    monkeypatch.setattr(settings, "rss_bridge_url", TEST_RSS_BRIDGE_URL)
    monkeypatch.setattr(settings, "miniflux_base_url", "http://test.miniflux.local")
    monkeypatch.setattr(settings, "accept_channels_without_username", True)
    return settings


@pytest.fixture
def mock_update():
    """A mock Telegram Update carrying both a message and a callback query."""
    update = MagicMock()

    # message.to_dict() must stay synchronous: the parser calls it directly.
    update.message = MagicMock()
    update.message.from_user = MagicMock()
    update.message.from_user.username = "test_admin"
    update.message.text = None
    update.message.media_group_id = None
    update.message.to_dict = MagicMock(return_value={})
    update.message.reply_text = AsyncMock()
    update.message.chat = MagicMock()
    update.message.chat.id = 12345
    update.message.chat.send_action = AsyncMock()

    update.callback_query = MagicMock()
    update.callback_query.data = ""
    update.callback_query.from_user = MagicMock()
    update.callback_query.from_user.username = "test_admin"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.chat = MagicMock()
    update.callback_query.message.chat.id = 12345
    update.callback_query.message.chat.send_action = AsyncMock()

    update.effective_chat = MagicMock()
    update.effective_chat.id = 12345

    return update


@pytest.fixture
def mock_context():
    """A mock Telegram CallbackContext with a real (mutable) user_data dict."""
    context = MagicMock()
    context.user_data = {}
    context.bot = AsyncMock()
    context.error = None
    return context


@pytest.fixture
def mock_query(mock_update):
    """Just the callback query — the flag/delete/edit handlers take it directly."""
    return mock_update.callback_query

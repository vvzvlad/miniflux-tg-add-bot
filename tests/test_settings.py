"""Tests for src/settings.py and src/config_errors.py.

Replaces the old tests/test_config.py: `load_config()` / `initialize_miniflux_client()`
are gone; configuration is now a pydantic-settings model validated at startup.
"""

from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

import src.miniflux_api as miniflux_api
from src.config_errors import load_settings_or_exit
from src.settings import (
    Settings,
    is_admin,
    settings,
    should_accept_channels_without_username,
)

# Captured at import time — the autouse patch_get_client fixture only replaces the
# module attribute while a test runs, so this is the genuine function.
REAL_GET_CLIENT = miniflux_api.get_client

VALID_ENV = {
    "TELEGRAM_TOKEN": "test_telegram_token",
    "ADMIN": "test_admin",
    "MINIFLUX_BASE_URL": "http://miniflux.example.com",
    "MINIFLUX_API_KEY": "test_api_key",
    "RSS_BRIDGE_URL": "http://bridge.example.com/rss/{channel}/token",
}


@pytest.fixture
def clean_env(monkeypatch):
    """Start from a known environment: only the variables a test sets are visible."""
    for name in (
        "TELEGRAM_TOKEN",
        "ADMIN",
        "MINIFLUX_BASE_URL",
        "MINIFLUX_API_KEY",
        "MINIFLUX_USERNAME",
        "MINIFLUX_PASSWORD",
        "RSS_BRIDGE_URL",
        "ACCEPT_CHANNELS_WITHOUT_USERNAME",
        "LOG_LEVEL",
    ):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _set_env(monkeypatch, **overrides):
    env = dict(VALID_ENV)
    env.update(overrides)
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)


# --- Loading a valid configuration ------------------------------------------

def test_settings_minimal_valid(clean_env):
    """A minimal valid configuration loads, with defaults for the optional fields."""
    _set_env(clean_env)
    loaded = Settings(_env_file=None)

    assert loaded.telegram_token == "test_telegram_token"
    assert loaded.admin == "test_admin"
    assert loaded.miniflux_base_url == "http://miniflux.example.com"
    assert loaded.miniflux_api_key == "test_api_key"
    assert loaded.rss_bridge_url == "http://bridge.example.com/rss/{channel}/token"
    # Optional fields fall back to their defaults
    assert loaded.accept_channels_without_username is False
    assert loaded.log_level == "INFO"


def test_settings_username_password(clean_env):
    """Username/password is accepted in place of an API key."""
    _set_env(
        clean_env,
        MINIFLUX_API_KEY=None,
        MINIFLUX_USERNAME="test_user",
        MINIFLUX_PASSWORD="test_password",
    )
    loaded = Settings(_env_file=None)

    assert loaded.miniflux_api_key is None
    assert loaded.miniflux_username == "test_user"
    assert loaded.miniflux_password == "test_password"


def test_settings_all_fields(clean_env):
    """Every field can be provided explicitly."""
    _set_env(clean_env, ACCEPT_CHANNELS_WITHOUT_USERNAME="true", LOG_LEVEL="DEBUG")
    loaded = Settings(_env_file=None)

    assert loaded.accept_channels_without_username is True
    assert loaded.log_level == "DEBUG"


# --- Rejecting an invalid configuration -------------------------------------

@pytest.mark.parametrize(
    "missing",
    ["TELEGRAM_TOKEN", "ADMIN", "MINIFLUX_BASE_URL", "RSS_BRIDGE_URL"],
)
def test_settings_missing_required_variable(clean_env, missing):
    """A missing required variable must fail at construction, never silently default."""
    _set_env(clean_env, **{missing: None})

    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_missing_miniflux_credentials(clean_env):
    """No API key and no username/password pair at all is a configuration error."""
    _set_env(
        clean_env,
        MINIFLUX_API_KEY=None,
        MINIFLUX_USERNAME=None,
        MINIFLUX_PASSWORD=None,
    )

    with pytest.raises(ValidationError, match="Miniflux credentials are missing"):
        Settings(_env_file=None)


def test_settings_partial_miniflux_credentials(clean_env):
    """A username without a password is not a usable credential pair."""
    _set_env(clean_env, MINIFLUX_API_KEY=None, MINIFLUX_USERNAME="test_user")

    with pytest.raises(ValidationError, match="Miniflux credentials are missing"):
        Settings(_env_file=None)


def test_settings_rss_bridge_url_without_placeholder(clean_env):
    """RSS_BRIDGE_URL is a template: without {channel} it cannot build a feed URL."""
    _set_env(clean_env, RSS_BRIDGE_URL="http://bridge.example.com/rss/")

    with pytest.raises(ValidationError, match=r"\{channel\}"):
        Settings(_env_file=None)


# --- load_settings_or_exit --------------------------------------------------

def test_load_settings_or_exit_returns_settings(clean_env):
    """A valid configuration is returned unchanged."""
    _set_env(clean_env)
    loaded = load_settings_or_exit(lambda: Settings(_env_file=None))
    assert loaded.telegram_token == "test_telegram_token"


def test_load_settings_or_exit_exits_on_missing_variable(clean_env, capsys):
    """A missing variable exits(1) and names the offending variable — no traceback."""
    _set_env(clean_env, TELEGRAM_TOKEN=None)

    with pytest.raises(SystemExit) as exc_info:
        load_settings_or_exit(lambda: Settings(_env_file=None))

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "TELEGRAM_TOKEN" in stderr
    assert "Missing required variable(s)" in stderr


def test_load_settings_or_exit_exits_on_invalid_value(clean_env, capsys):
    """An invalid value is reported under 'Invalid value(s)'."""
    _set_env(clean_env, RSS_BRIDGE_URL="http://bridge.example.com/rss/")

    with pytest.raises(SystemExit) as exc_info:
        load_settings_or_exit(lambda: Settings(_env_file=None))

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "Invalid value(s)" in stderr


def test_load_settings_or_exit_propagates_other_errors():
    """A non-ValidationError is not swallowed."""
    def broken_factory():
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        load_settings_or_exit(broken_factory)


# --- is_admin ---------------------------------------------------------------

def test_is_admin_valid(monkeypatch):
    monkeypatch.setattr(settings, "admin", "admin_user")
    assert is_admin("admin_user") is True


def test_is_admin_invalid(monkeypatch):
    monkeypatch.setattr(settings, "admin", "admin_user")
    assert is_admin("not_admin") is False


def test_is_admin_none(monkeypatch):
    monkeypatch.setattr(settings, "admin", "admin_user")
    assert is_admin(None) is False


# --- should_accept_channels_without_username --------------------------------

def test_should_accept_channels_without_username_true(monkeypatch):
    monkeypatch.setattr(settings, "accept_channels_without_username", True)
    assert should_accept_channels_without_username() is True


def test_should_accept_channels_without_username_false(monkeypatch):
    monkeypatch.setattr(settings, "accept_channels_without_username", False)
    assert should_accept_channels_without_username() is False


@pytest.mark.parametrize("raw,expected", [("true", True), ("TRUE", True), ("1", True),
                                          ("false", False), ("FALSE", False), ("0", False)])
def test_accept_channels_without_username_parsing(clean_env, raw, expected):
    """The boolean flag is parsed case-insensitively from the environment."""
    _set_env(clean_env, ACCEPT_CHANNELS_WITHOUT_USERNAME=raw)
    assert Settings(_env_file=None).accept_channels_without_username is expected


# --- get_client (replaces the old initialize_miniflux_client tests) ---------

@pytest.fixture
def reset_client_cache():
    """get_client() caches its client in a module global; reset it around the test."""
    miniflux_api._client = None
    yield
    miniflux_api._client = None


def test_get_client_with_api_key(monkeypatch, reset_client_cache):
    """An API key builds an api_key client."""
    monkeypatch.setattr(settings, "miniflux_base_url", "http://miniflux.example.com")
    monkeypatch.setattr(settings, "miniflux_api_key", "test_api_key")

    with patch("miniflux.Client") as mock_client_class:
        REAL_GET_CLIENT()

    mock_client_class.assert_called_once_with(
        "http://miniflux.example.com", api_key="test_api_key"
    )


def test_get_client_with_username_password(monkeypatch, reset_client_cache):
    """No API key falls back to username/password."""
    monkeypatch.setattr(settings, "miniflux_base_url", "http://miniflux.example.com")
    monkeypatch.setattr(settings, "miniflux_api_key", None)
    monkeypatch.setattr(settings, "miniflux_username", "test_user")
    monkeypatch.setattr(settings, "miniflux_password", "test_password")

    with patch("miniflux.Client") as mock_client_class:
        REAL_GET_CLIENT()

    mock_client_class.assert_called_once_with(
        "http://miniflux.example.com", username="test_user", password="test_password"
    )


def test_get_client_is_cached(monkeypatch, reset_client_cache):
    """The client is built once and reused: it is a cached singleton."""
    monkeypatch.setattr(settings, "miniflux_api_key", "test_api_key")

    with patch("miniflux.Client", return_value=MagicMock()) as mock_client_class:
        first = REAL_GET_CLIENT()
        second = REAL_GET_CLIENT()

    assert first is second
    mock_client_class.assert_called_once()

"""Application settings — the single configuration entry point.

Every value comes from the environment (or a .env file). Credentials and the
addresses of our own services have no defaults: a missing variable must fail at
startup with a readable message instead of letting the bot run half-broken.
"""

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.config_errors import load_settings_or_exit


class Settings(BaseSettings):
    """Runtime configuration read from environment variables / .env."""

    # Telegram
    telegram_token: str
    admin: str
    # Optional self-hosted Telegram Bot API server (telegram-bot-api), used where
    # api.telegram.org is not directly reachable. Set the bare server root, e.g.
    # http://internal.lc:8081 — the bot appends /bot and /file/bot itself. Unset
    # -> the public https://api.telegram.org.
    telegram_api_server: str | None = None

    # Miniflux
    miniflux_base_url: str
    miniflux_api_key: str | None = None
    miniflux_username: str | None = None
    miniflux_password: str | None = None

    # RSS-Bridge
    rss_bridge_url: str

    # Behaviour
    accept_channels_without_username: bool = False
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("rss_bridge_url")
    @classmethod
    def _validate_rss_bridge_url(cls, value: str) -> str:
        """The bridge URL is a template: the channel name is substituted into it."""
        if "{channel}" not in value:
            raise ValueError("RSS_BRIDGE_URL must contain the '{channel}' placeholder")
        return value

    @model_validator(mode="after")
    def _validate_miniflux_credentials(self) -> "Settings":
        """Either an API key or a username/password pair is required.

        The message names the variables explicitly: model-level errors are printed
        without a field name by config_errors.load_settings_or_exit().
        """
        has_api_key = bool(self.miniflux_api_key)
        has_basic_auth = bool(self.miniflux_username) and bool(self.miniflux_password)
        if not has_api_key and not has_basic_auth:
            raise ValueError(
                "Miniflux credentials are missing. "
                "Set MINIFLUX_API_KEY, or both MINIFLUX_USERNAME and MINIFLUX_PASSWORD"
            )
        return self


settings = load_settings_or_exit(Settings)


def is_admin(username: str | None) -> bool:
    """Check whether the given Telegram username is the configured admin."""
    return username is not None and username == settings.admin


def should_accept_channels_without_username() -> bool:
    """Whether channels without a public username may be subscribed."""
    return settings.accept_channels_without_username

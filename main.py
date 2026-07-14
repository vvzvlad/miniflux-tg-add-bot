"""Entry point: configure logging and run the bot."""

import logging

from src.bot import run
from src.settings import settings


def configure_logging() -> None:
    """Configure stdlib logging once, at startup."""
    level_name = settings.log_level.upper()
    level = logging.getLevelName(level_name)
    known_level = isinstance(level, int)

    logging.basicConfig(
        level=level if known_level else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    if not known_level:
        logging.warning(f"Unknown LOG_LEVEL '{settings.log_level}', falling back to INFO.")

    # These libraries are very chatty at INFO level
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram.utils.request").setLevel(logging.WARNING)


def main() -> None:
    configure_logging()
    run()


if __name__ == "__main__":
    main()

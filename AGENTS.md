# Agent Instructions — miniflux-tg-add-bot

## What this project is
A Telegram bot that subscribes Telegram channels and RSS feeds into a self-hosted
[Miniflux](https://miniflux.app/) instance. Telegram channels have no native RSS, so the
bot builds a feed URL through an external RSS-Bridge (`RSS_BRIDGE_URL`, which must contain
the `{channel}` placeholder) and adds it to Miniflux via the Miniflux API. It also manages
existing feeds: flags, exclude-regex and merge-time.

The bot works in **polling** mode — it has no inbound port.

## Project structure
```
main.py                  # thin entry point over src/
src/
├── settings.py          # pydantic-settings, the single config entry point
├── config_errors.py     # ValidationError -> clear message + exit(1)
├── bot.py               # application assembly (handler registration, run_polling)
├── miniflux_api.py      # Miniflux API client
├── url_utils.py         # URL parsing / feed discovery
├── url_constructor.py   # building the RSS-Bridge feed URL
└── handlers/
    ├── commands.py      # /start, /list
    ├── messages.py      # message parsing + state handlers
    ├── callbacks.py     # inline button callbacks
    ├── keyboards.py     # keyboard construction
    └── common.py        # shared helpers
tests/                   # pytest
data/                    # runtime state (gitignored, mounted as a docker volume)
```

## Setup
All routine actions go through the `Makefile` — run `make help` to list targets.
```bash
make install           # create .venv and install dev/test deps
cp .env.example .env   # then fill in the values  (shortcut: make env)
```

## Running tests
```bash
make test              # runs .venv/bin/pytest
```

## Running the app
```bash
make run               # runs .venv/bin/python main.py
```

## Conventions
- All mutable state goes under `data/`.
- All config comes from ENV / `.env` (see `.env.example`), read through `Settings`.
- Credentials and addresses of our own services (the Telegram token, the Miniflux URL and
  its API key / username+password, the RSS-Bridge URL, the admin username) go ONLY into
  `.env` — never into code, and never passed via inline env vars on the command line.
- No default/example credentials in code; a missing required ENV var → fail at startup.
  A default address is allowed ONLY for public third-party APIs — self-hosted services
  (Miniflux, the RSS-Bridge) have no default.
- Code comments are in English.
- All repeated actions (env setup, tests, run) go through `make` targets — add or extend a
  target instead of running ad-hoc commands.
- Python always runs inside a local `.venv`, created automatically by `make` on first use
  (`make test` / `make run` bootstrap it) — never the system Python.
- Tests are required for new code; in CI `build` depends on `test`, so red tests block the
  image push.
- No `EXPOSE` in the Dockerfile — the bot polls Telegram and exposes no port.

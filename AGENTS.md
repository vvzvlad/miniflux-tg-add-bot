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

## Setup and local run
All routine actions go through the `Makefile` (`make help` lists targets).
```bash
make install           # create .venv and install dev/test deps
make env               # create .env from .env.example, then fill in the values
make run               # .venv/bin/python main.py
make lint              # ruff + mypy
```

## Tests and deploy (CI)
- Tests run in CI: `.gitea/workflows/tests.yml` on pull requests to `master`, and the
  `test` job of `.gitea/workflows/image-check-publish.yml` on pushes to `master`.
  `make test` runs the same pytest suite.
- `image-check-publish.yml` builds the image only after green tests and pushes
  `gitea.vvzvlad.xyz/projects/miniflux-tg-add-bot:latest` and `:<sha>`; watchtower
  deploys `:latest`. `docker-compose.yml` is the deploy template (placeholders only).
  That workflow must never get a `pull_request` trigger: a PR build would push `:latest`.

## Conventions
- All mutable state goes under `data/`.
- All config comes from ENV / `.env` (see `.env.example`), read through `Settings`.
- Credentials and addresses of our own services (the Telegram token, the Miniflux URL and
  its API key / username+password, the RSS-Bridge URL, the admin username) go ONLY into
  `.env` — never into code, and never passed via inline env vars on the command line.
- No default/example credentials in code; a missing required ENV var → fail at startup.
  A default address is allowed ONLY for public third-party APIs — self-hosted services
  (Miniflux, the RSS-Bridge) have no default.
- `miniflux.Client` is synchronous: never `await` its methods; tests mock it with
  `MagicMock`, not `AsyncMock`.
- Code comments are in English.
- All repeated actions (env setup, tests, run) go through `make` targets — add or extend a
  target instead of running ad-hoc commands.
- Python always runs inside a local `.venv`, created by `make` on first use — never the
  system Python.
- Tests are required for new code.
- No `EXPOSE` in the Dockerfile — the bot polls Telegram and exposes no port.

## Limits for agents
- Tests: at most three times the code (`wc -l` of `tests/` against the code). Over the limit,
  still write the test you need, and in the same change go through the old tests: drop or merge
  duplicates and tests of implementation rather than behaviour.
- Comments explain the code as it is and why it is built this way. History ("used to", "after
  the review", "since the incident") goes into the commit message.
- This file stays under ~150 lines: only what an agent would get wrong without it. No decision
  history, incident logs or retelling of the code.

# miniflux-tg-add-bot

A Telegram bot that subscribes Telegram channels and RSS feeds into a self-hosted
[Miniflux](https://miniflux.app/) instance.

Telegram channels have no native RSS, so the bot builds a feed URL through an external
RSS-Bridge (e.g. [pyrogram-bridge](https://github.com/vvzvlad/pyrogram-bridge) or
[RSSHub](https://github.com/DIYgod/RSSHub)) and adds the resulting feed to Miniflux
through the Miniflux API.

The bot runs in polling mode — it needs no inbound port and no public hostname.

## Features

Send the bot any of these and it will offer a list of your Miniflux categories; pick one
and the feed is subscribed:

- a **forwarded post** from a channel;
- a **`@channel`** username;
- a **`t.me/...` link**;
- a **direct RSS/Atom URL**;
- a **web page URL** — the bot discovers the feed links on the page and lets you choose.

On top of subscribing, the bot manages existing feeds:

- toggle **flags** on a feed;
- set an **exclude-regex** to filter out unwanted entries;
- set a **merge-time** window;
- **`/list`** — show all current subscriptions grouped by Miniflux category;
- **`/start`** — usage help.

Only the single Telegram username configured in `ADMIN` may use the bot.

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `TELEGRAM_TOKEN` | yes | — | Bot token from [@BotFather](https://t.me/BotFather) |
| `MINIFLUX_BASE_URL` | yes | — | URL of your Miniflux instance, e.g. `http://miniflux.example.com` |
| `MINIFLUX_API_KEY` | one of | — | Miniflux API key… |
| `MINIFLUX_USERNAME` | one of | — | …or Miniflux username + password |
| `MINIFLUX_PASSWORD` | one of | — | Password for `MINIFLUX_USERNAME` |
| `RSS_BRIDGE_URL` | yes | — | RSS-Bridge feed URL template; **must contain the `{channel}` placeholder**, e.g. `http://bridge.example.com/rss/{channel}` |
| `ADMIN` | yes | — | The only Telegram username allowed to use the bot |
| `ACCEPT_CHANNELS_WITHOUT_USERNAME` | no | `false` | Accept channels that have no public username (the RSS-Bridge must support this; RSSHub does not) |
| `LOG_LEVEL` | no | `INFO` | Logging level |

Authenticate against Miniflux with **either** `MINIFLUX_API_KEY` **or**
`MINIFLUX_USERNAME` + `MINIFLUX_PASSWORD`. Missing required variables make the bot fail at
startup with a clear message.

## Local development

Everything routine is wrapped in the `Makefile` (`make help` lists all targets):

```bash
make install                # create .venv + install dev/test deps
make env                    # copy .env.example -> .env, then fill in the values
make test                   # run the pytest suite
make run                    # run the bot
```

`make test` and `make run` create and reuse a local `.venv` automatically — the system
Python is never used.

## Deployment

The image is built and pushed to `ghcr.io` by CI on every push to `master` (tests must be
green first) and is never built on the server. Deploy it with the provided
[`docker-compose.yml`](docker-compose.yml):

```bash
docker compose up -d
```

Fill in the `environment:` block (the committed values are placeholders) using the table
above. [Watchtower](https://github.com/containrrr/watchtower) picks up new `:latest` images
automatically — the compose file already carries the
`com.centurylinklabs.watchtower.enable: "true"` label.

Runtime state lives in `/app/data` inside the container, backed by the named volume
declared in the compose file, so it survives restarts and image updates.

## Usage

1. Forward a post from a channel to the bot (or send `@channel` / a `t.me` link / a feed
   URL / a page URL).
2. The bot fetches the categories from Miniflux and shows them as a keyboard.
3. You pick a category; the bot builds the feed URL by substituting the channel into
   `RSS_BRIDGE_URL`'s `{channel}` placeholder.
4. The bot adds the subscription to Miniflux and reports the result.

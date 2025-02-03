# TG2RSS Subscription Bot

## Create bot and get token

1)Go to https://t.me/BotFather  
2)Create new bot  
3)Get token  
4)Set token env TELEGRAM_TOKEN  

## Install miniflux

Bot used miniflux (https://miniflux.app/) API for adding subscriptions.

```docker-compose
volumes:
  miniflux-db:
  
services:
  miniflux:
    image: miniflux/miniflux:latest
    container_name: miniflux
    networks:
      - docker_main_net
    depends_on:
      miniflux-db:
        condition: service_healthy
    environment:
      - DATABASE_URL=postgres://miniflux:secret@miniflux-db/miniflux?sslmode=disable
      - RUN_MIGRATIONS=1
      - CREATE_ADMIN=1
      - ADMIN_USERNAME=bla-bla
      - ADMIN_PASSWORD=bla-bla
      - BASE_URL=https://miniflux.bla-bla.com

    restart: unless-stopped
    labels:
      traefik.enable: "true"
      traefik.http.routers.miniflux.rule: Host(`miniflux.bla-bla.com`)
      traefik.http.services.miniflux.loadBalancer.server.port: 8080
      traefik.http.routers.miniflux.entrypoints: websecure
      traefik.http.routers.miniflux.tls: true
        
  miniflux-db:
    image: postgres:17-alpine
    container_name: miniflux-db
    networks:
      - docker_main_net
    environment:
      - POSTGRES_USER=miniflux
      - POSTGRES_PASSWORD=secret
      - POSTGRES_DB=miniflux
    volumes:
      - miniflux-db:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "pg_isready", "-U", "miniflux"]
      interval: 10s
      start_period: 30s
```

Use env variables MINIFLUX_BASE_URL(BASE_URL), MINIFLUX_USERNAME(ADMIN_USERNAME), MINIFLUX_PASSWORD(ADMIN_PASSWORD) in docker-compose.yml file


## Install bridge 
Use any tg2rss bridge:
https://github.com/vvzvlad/pyrogram-bridge  
https://github.com/DIYgod/RSSHub  


You need get rss subscription url from RSSHub and set it in env variable RSS_BRIDGE_URL  
E.g. https://rsshub.example.com/telegram/channel/channel_name or https://pgbridge.example.com/rss/channel_name(need non-bot tg account!)  

https://rsshub.example.com/telegram/channel/channel_name need response valid rss-xml feed  

Use in RSS_BRIDGE_URL url without channel_name: https://rsshub.example.com/telegram/channel/  


## Install bot

```docker-compose
services:
    tg2rss-subscription-bot:
    image: ghcr.io/vvzvlad/tg2rss-subscription-bot:latest
    container_name: tg2rss-subscription-bot
    restart: unless-stopped
    environment:
      TZ: Europe/Moscow
      TELEGRAM_TOKEN: bla-bla
      MINIFLUX_BASE_URL: https://miniflux.example.com
      MINIFLUX_USERNAME: admin
      MINIFLUX_PASSWORD: bla-bla
      RSS_BRIDGE_URL: https://rsshub.example.com/telegram/channel/
      ADMIN: admin_username
      ACCEPT_CHANNELS_WITOUT_USERNAME: true
    labels:
      com.centurylinklabs.watchtower.enable: "true"
```

ADMIN env variable is username of one user who can add subscriptions

## Use bot

1)Forward message to bot from channel  
2)Bot will get categorues from miniflux and send to user keyboard with categories  
3)User select category and bot will create subscription url (on RSS_BRIDGE_URL)  
4)Bot add subscription to miniflux and send message to channel with subscription status  


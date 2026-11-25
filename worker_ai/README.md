# AI review assistant: worker_ai

A background service that replies to reviews. It never touches the site's database directly; everything goes through the HTTP API, so it can run separately, on another server or in its own container.

## What the service does

- Every `WORKER_POLL_INTERVAL` seconds (10 by default) it fetches reviews with the `new` status from the site.
- Detects the review's tone and writes a reply with OpenAI. If there's no key or the request fails, tone is detected by marker words and the reply comes from prepared templates.
- Adds the reply as a separate comment under the review and marks both the review and the reply as processed.
- Sends a Telegram alert about the new review.
- Ignores its own replies, so no auto-reply loops appear.

## Files

- `config.py`: settings from `.env`.
- `client.py`: HTTP client for the site's API.
- `models.py`: data models.
- `processor.py`: the OpenAI request and the fallback logic.
- `state.py`: local memory of what has already been processed and alerted.
- `telegram_bot.py`: Telegram alerts.
- `worker.py`: the main loop.
- `Dockerfile`, `docker-compose.yml`: running in a container.

## Settings

Copy `.env.example` to `.env`:

```env
TARGET_SITE_URL=http://127.0.0.1:8000
WORKER_API_TOKEN=change-me
WORKER_POLL_INTERVAL=10
STATE_FILE_PATH=data/state.json
OPENAI_API_KEY=
OPENAI_MODEL=gpt-4.1-mini
AI_AUTHOR_NAME=AI Support
TELEGRAM_BOT_TOKEN=
TELEGRAM_USER_CHAT_ID=
```

`WORKER_API_TOKEN` must match the value in `site/server/.env`, otherwise the site won't let the service update reviews. If the server has no domain name, `TARGET_SITE_URL` can be an IP address, for example `http://192.168.1.20:8000`.

## Running it

In a container, from the `worker_ai` folder:

```bash
docker compose up --build
```

Without a container:

```bash
pip install -r requirements.txt
python worker.py
```

## Telegram alerts

1. Create a bot with `@BotFather` and get the `TELEGRAM_BOT_TOKEN`.
2. Open a private chat with the bot and press `/start`, otherwise the bot can't write to you.
3. Find that chat's id and put it in `TELEGRAM_USER_CHAT_ID`.

The older `TELEGRAM_CHAT_ID` setting still works, but `TELEGRAM_USER_CHAT_ID` is better for personal alerts. If Telegram isn't configured, the service keeps replying to reviews, just without alerts.

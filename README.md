# forward-bot-telegram

Mirror posts from one Telegram channel into several targets.

## Install

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set `BOT_TOKEN` in `.env`. Configure targets with `/add_target` (private chat with the bot) or add the bot to destination channels.

## Source channel: two modes

**1. Bot listener (default)** — leave `SOURCE_LISTENER` unset or set `SOURCE_LISTENER=bot`.  
The bot must be an **admin** on the source channel. Mirroring uses Bot API `copyMessage`.

**2. Telethon listener** — set `SOURCE_LISTENER=telethon` and add:

- `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` from [my.telegram.org](https://my.telegram.org)
- Optional `TELEGRAM_PHONE` / `TELEGRAM_PASSWORD` for first login
- Session file path via `TELETHON_SESSION` (default `telethon.session`)

Your **user account** must be subscribed to / able to read the source channel; the bot does **not** need to join it. The bot still posts to targets (it must be admin on targets).

First Telethon run may prompt for the login code in the terminal.

## Run

```bash
python bot.py
```

Persist source id with `/set_source -100xxxxxxxxxx` in private chat with the bot (same id format for both modes).

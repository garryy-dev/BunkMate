# BunkMate 🎓

A Telegram bot that tracks student attendance in real time and tells you exactly how many classes you can safely skip — or need to attend — to hit your target percentage.

Live in production on Render, currently used by 20 real students.

## What it does

- **Attendance tracking** — mark present/absent per subject, view full history, undo mistakes
- **"Can I bunk?"** — calculates exactly how many classes you can miss (or must attend) to stay above your target attendance percentage, per subject or overall
- **Forecasting** — projects your attendance percentage forward based on your remaining timetable
- **Per-subject targets** — set different attendance goals per subject, not just one global number
- **Reminders** — daily automated nudges via a scheduled job, so you don't forget to log a class
- **CSV import/export** — bring in an existing attendance sheet or pull your data out
- **New semester reset** — archive the old semester and start fresh without losing history
- **Self-serve account deletion** — users can permanently delete their own data, no admin needed

## Admin tools

Restricted to a single Telegram ID set via environment variable:

- Dashboard with global user stats
- View any user's data for support purposes
- Broadcast messages / DM individual users, with undo
- Ban/unban and permanently delete a specific user's data
- Full JSON backup on demand
- Kill switch to wipe all data — gated behind a two-step in-chat confirmation before it executes

## Tech stack

- **Python** — [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) for the bot framework, using its built-in job queue for scheduled reminders
- **FastAPI** — serves the Telegram webhook endpoint, validated against a secret token so only genuine Telegram requests are accepted
- **MongoDB** (via `pymongo`) — stores user data, attendance records, and admin state
- **Render** — hosting, webhook-based (not polling)

## Project structure

```
bot.py                     # entry point: handlers, admin commands, webhook server
bunkmate/
  calculator.py             # pure attendance math — % calculation, bunk/must-attend projections
  data_manager.py           # MongoDB read/write layer, ban/delete/backup logic
requirements.txt
```

## Setup

1. Clone the repo and install dependencies:
   ```
   pip install -r requirements.txt
   ```
2. Create a `.env` file (never committed — see `.gitignore`) with:
   ```
   TELEGRAM_BOT_TOKEN=your_bot_token
   ADMIN_ID=your_telegram_user_id
   MONGO_URI=your_mongodb_connection_string
   WEBHOOK_SECRET_TOKEN=a_long_random_string
   WEBHOOK_URL=https://your-deployment-url.com/webhook
   ```
3. Run locally:
   ```
   python bot.py
   ```

## Status

Actively used, not actively maintained on a fixed schedule — built and shipped solo as a side project alongside coursework.

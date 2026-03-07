# Salah Reminder Bot

A Telegram bot for Islamic prayer tracking, reminders, and AI chat. Python 3.12, Docker.

## Tech Stack

- **python-telegram-bot 21.x** -- webhook mode, APScheduler job queue
- **SQLAlchemy 2.x async + asyncpg** -- PostgreSQL database
- **Alembic** -- database migrations
- **Anthropic Claude Sonnet 4.5** -- AI chat with tool-calling (reminders CRUD)
- **faster-whisper** -- local speech-to-text (small model, CPU, preloaded on startup)
- **caldav** -- iCloud Calendar/Reminders bidirectional sync
- **praytimes** -- prayer time calculation (returns fajr, sunrise, dhuhr, asr, maghrib, isha)
- **Pydantic Settings** -- config via env vars with `SALAH_` prefix
- **Docker Compose** -- bot + postgres:16-alpine

## Project Structure

```
src/
├── main.py              # Entry point (webhook + polling)
├── config.py            # Pydantic settings (SALAH_ prefix env vars)
├── database.py          # Async SQLAlchemy engine (asyncpg, pool_size=20)
├── models/              # SQLAlchemy models (User, PrayerLog, DailyVerse, ScheduledTask, Note)
├── repositories/        # Data access layer (UserRepo, PrayerRepo, TaskRepo, NoteRepo)
├── services/            # Business logic
│   ├── ai.py            # Claude AI chat + tool execution
│   ├── prayer.py        # Prayer times + sunrise calculation
│   ├── scoring.py       # Prayer scoring + daily/weekly summaries
│   ├── apple_calendar.py # iCloud CalDAV integration
│   ├── quran.py         # Daily Quran delivery
│   ├── motivation.py    # Hadith motivation messages
│   ├── voice.py         # Whisper transcription
│   └── ical.py          # iCal feed generation
└── bot/
    ├── handlers/        # Telegram handlers (start, prayer, quran, chat, apple, notes)
    ├── keyboards.py     # Inline keyboards
    ├── scheduler.py     # Prayer notifications, follow-ups, daily times, quran, iCloud sync, productivity check-ins
    └── task_scheduler.py # User reminder scheduler
```

## Architecture

- **Repository pattern**: handlers -> services -> repositories -> models
- All DB operations use `async with async_session() as session:`
- **Handler registration order matters**: chat handlers MUST be registered LAST (catch-all for non-command text)
- **Webhook must include** `allowed_updates=["message", "callback_query"]` or buttons won't work
- AI service tools: `create_reminder`, `create_recurring_reminder`, `list_reminders`, `delete_reminder`, `capture_note`, `list_notes`, `complete_note`

## Key Behaviors

- **Prayer notifications**: sent at adhan time by default (user configurable via settings)
- **Time windows**: each notification shows Masjid/Iqama/On Time/Last Min/Qaza windows with time ranges
- **Fajr window**: uses sunrise as the hard deadline (not arbitrary cap)
- **Follow-up**: 30 min after adhan if user hasn't responded
- **Daily times**: sent 5 min after Fajr with all prayer times + sunrise
- **Daily Quran**: randomized time between 7-11am (not same time every day)
- **iCloud sync**: every 5 min, checks if bot-pushed items were completed/deleted on iPhone
- **iCloud event notifications**: 5-20 min before upcoming events
- **All times displayed in user's local timezone** -- events from other timezones are converted
- **AI responses**: split into multiple messages on paragraph breaks for natural feel
- **Scoring**: Masjid +5, Iqama +4, On Time +3, Last Min +2, Qaza +1

## Productivity System (5 Layers)

1. **Capture** -- `/n anything` saves instantly, voice messages transcribed & saved, forwarded messages captured
2. **Salah-anchored rhythm** -- Fajr (+10min): "What must happen today?", Asr (+5min): "Still on track?", Isha (+10min): "What carries to tomorrow?"
3. **Weekly brain** -- Sunday 7am: digest of all captures grouped by category, user responds with priorities → bot sets reminders
4. **Energy-aware reminders** -- AI schedules deep work for mornings, admin/calls after Asr
5. **Closure** -- `/done` marks complete, Friday 8pm: clean slate report (captured/done/ignored)

## Commands

- `/today` -- prayer times + daily summary (single command for both)
- `/week` -- weekly report
- `/score` -- total score & streak
- `/n` -- quick capture a note (`/n buy groceries`)
- `/done` -- mark notes complete (shows open notes with buttons)
- `/quran` -- get a Quran excerpt now
- `/connect_apple` -- connect iCloud Calendar & Reminders
- `/settings` -- preferences (calc method, madhab, timezone, notification timing, daily quran toggle)
- `/clear` -- clear AI chat history
- `/help` -- help

No separate `/times` command -- prayer times are included in `/today`.

## Dev Commands

```bash
# Docker
docker compose build && docker compose up -d
docker compose logs bot --tail 50
docker compose exec -T db psql -U reminder -d reminder

# Database
docker compose exec -T bot alembic revision --autogenerate -m "description"
docker compose exec -T bot alembic upgrade head
```

## Database

- **PostgreSQL 16** via Docker (container: reminder-postgres)
- Connection: `postgresql+asyncpg://reminder:reminder_s3cure_pwd@db:5432/reminder`
- Tables auto-created via `init_db()` on startup
- Old SQLite data at `data/salah_reminder.db` (migrated, kept as backup)

## Formatting

- `/today` uses `<code>` HTML tags per line for monospace alignment (no `<pre>` -- avoids copy button)
- Settings confirmations use same `<code>` format with sunrise
- Colored circle emojis: 🟢 Masjid/Iqama, 🟡 On Time, 🟠 Last Min, 🔴 Qaza, ⚪ pending/upcoming
- AI style: natural, direct, like a close friend -- not an assistant

## Important Notes

- **Chat handler is catch-all** -- must be registered LAST or it swallows other commands
- **iCloud sync is bidirectional**: reads calendar/reminders, pushes bot items, syncs back completions
- **iPhone alarms (Clock app) don't sync** -- only Calendar and Reminders app items via CalDAV
- **AI timezone fix**: if AI sends UTC offset for a non-UTC user, code treats time as user's local time
- **Voice messages** transcribed locally via faster-whisper (no external API)
- **2GB swap file** at `/swapfile` (persistent via fstab)
- `.env` contains secrets -- never commit

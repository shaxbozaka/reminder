# iCloud Bidirectional Sync — Design

## Summary

Full two-way integration between the Salah Reminder bot and iCloud Calendar & Reminders:
- **iCloud → Bot**: AI sees the user's upcoming calendar events and incomplete reminders
- **Bot → iCloud**: Bot-created reminders/events appear on iPhone in a dedicated "Reminder Bot" calendar and reminder list
- **Sync-back**: Completing or deleting a bot-created item on iPhone deactivates it in the bot

Prayer times are NOT pushed to iCloud.

## Data Flow

### iCloud → Bot (read, per chat message)
- `build_user_context()` calls `AppleCalendarService.format_for_context()` for connected users
- AI sees upcoming 7 days of events + incomplete reminders from all calendars/lists
- No new tables — real-time on each chat message

### Bot → iCloud (write, on reminder/event creation)
- AI creates a `ScheduledTask` via tools → also pushes to iCloud via CalDAV
- "Reminder Bot" calendar + reminder list auto-created on first `/connect_apple`
- `ScheduledTask.icloud_uid` tracks the corresponding iCloud item
- AI tool schema has a `target` field: `"reminder"` or `"calendar_event"`
- AI decides which based on user intent ("remind me to X" → reminder, "meeting at 3pm" → calendar event)

### iCloud → Bot (sync-back, every 5 min)
- Periodic job polls the "Reminder Bot" calendar + reminder list
- If an item with known `icloud_uid` is completed/deleted on iPhone → deactivate `ScheduledTask` + unschedule
- Only polls for connected users with active iCloud-synced tasks

## File Changes

### `src/models/scheduled_task.py`
Add two columns:
- `icloud_uid: Mapped[str | None]` — CalDAV UID of the pushed item
- `target: Mapped[str]` — `"reminder"` or `"calendar_event"` (default: `"reminder"`)

### `src/services/apple_calendar.py`
Add write methods:
- `create_calendar_if_missing()` — Idempotent creation of "Reminder Bot" calendar + reminder list
- `push_calendar_event(title, message, start_dt, end_dt) -> str` — Creates VEVENT, returns UID
- `push_reminder(title, message, due_dt) -> str` — Creates VTODO, returns UID
- `delete_item(uid)` — Removes event/reminder by UID
- `get_bot_items() -> list[dict]` — Fetches all items from "Reminder Bot" (for sync-back)

### `src/services/ai.py`

**`build_user_context()`:**
- If user has `apple_id` set, call `format_for_context()` and append to context

**Tool schema changes:**
- `create_reminder` / `create_recurring_reminder` get optional `target` field (`"reminder"` | `"calendar_event"`)
- After creating `ScheduledTask`, if user has Apple connected → push to iCloud, store `icloud_uid`
- `delete_reminder` also deletes the iCloud item if `icloud_uid` exists

**System prompt addition:**
- Guide AI to choose `"reminder"` for nudges/tasks, `"calendar_event"` for time-blocked activities

### `src/bot/scheduler.py`
New periodic job:
- `sync_icloud_states()` — Runs every 5 min per connected user
- Compares "Reminder Bot" items against active `ScheduledTask` rows with `icloud_uid`
- Missing/completed UIDs → deactivate + unschedule
- Registered in `schedule_all_users()` on startup
- Skips users with no active iCloud-synced tasks

### `src/bot/handlers/apple.py`
Update connection flow:
- After credential test, call `create_calendar_if_missing()`
- Start sync-back job immediately
- Updated success message mentioning the created calendar/list

### New Alembic migration
- Adds `icloud_uid` (nullable string) and `target` (string, default "reminder") to `scheduled_tasks`

## Error Handling

- All iCloud writes are **best-effort** — CalDAV failure doesn't block bot reminders
- Invalid credentials (401) → mark disconnected, notify user via Telegram
- Sync-back polling silently skips on network errors, retries next cycle
- `/disconnect_apple` → stops sync job, bot reminders keep working (just not mirrored)
- Container restart → sync jobs re-registered in `post_init`
- `icloud_uid` prevents duplicate pushes

## Edge Cases

- Recurring reminders → always pushed as iCloud Reminders (not calendar events, CalDAV recurring events are fragile)
- User disconnects → sync job stops, existing bot reminders stay active
- Credentials expire → auto-detect on next CalDAV call, notify user

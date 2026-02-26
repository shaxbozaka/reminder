# iCloud Bidirectional Sync — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Full two-way sync between the Salah Reminder bot and iCloud Calendar & Reminders.

**Architecture:** Bot-created items push to a dedicated "Reminder Bot" calendar/reminder list on iCloud via CalDAV. User's full iCloud schedule is injected into AI context on each chat. A periodic polling job syncs back completions/deletions from iPhone.

**Tech Stack:** caldav 2.2.6, SQLAlchemy 2.x async, Alembic, python-telegram-bot 21.x, icalendar

---

### Task 1: Alembic Migration — Add icloud_uid and target columns

**Files:**
- Modify: `src/models/scheduled_task.py`
- Create: `alembic/versions/` (auto-generated)

**Step 1: Add columns to model**

In `src/models/scheduled_task.py`, add after `timezone` field:

```python
# iCloud sync
icloud_uid: Mapped[str | None] = mapped_column(String(255), nullable=True)
target: Mapped[str] = mapped_column(String(20), default="reminder")  # "reminder" or "calendar_event"
```

**Step 2: Create alembic versions directory and generate migration**

```bash
cd /root/reminder
mkdir -p alembic/versions
.venv/bin/alembic revision --autogenerate -m "add icloud_uid and target to scheduled_tasks"
```

**Step 3: Run migration**

```bash
cd /root/reminder
.venv/bin/alembic upgrade head
```

**Step 4: Verify**

```bash
cd /root/reminder
.venv/bin/python -c "
import sqlite3
conn = sqlite3.connect('data/salah_reminder.db')
cursor = conn.execute('PRAGMA table_info(scheduled_tasks)')
for col in cursor.fetchall():
    print(col)
conn.close()
"
```

Expected: `icloud_uid` and `target` columns appear in output.

---

### Task 2: Apple Calendar Service — Write Methods

**Files:**
- Modify: `src/services/apple_calendar.py`

**Step 1: Add create_calendar_if_missing()**

After existing `__init__`, add method that finds or creates a "Reminder Bot" calendar. Uses `self.client.principal().make_calendar(name="Reminder Bot")` with an existence check first. Also finds or creates a "Reminder Bot" VTODO calendar (reminder list). Store both as `self._bot_calendar` and `self._bot_reminders`.

```python
def _get_or_create_calendar(self, name: str, supported_component: str = "VEVENT") -> caldav.Calendar:
    """Find existing calendar by name, or create it."""
    principal = self._get_principal()
    for cal in principal.calendars():
        if cal.name == name:
            return cal
    # Create new
    return principal.make_calendar(
        name=name,
        supported_calendar_component_set=[supported_component],
    )

def get_bot_calendar(self) -> caldav.Calendar:
    """Get or create the 'Reminder Bot' calendar for events."""
    if not hasattr(self, '_bot_calendar') or self._bot_calendar is None:
        self._bot_calendar = self._get_or_create_calendar("Reminder Bot", "VEVENT")
    return self._bot_calendar

def get_bot_reminders(self) -> caldav.Calendar:
    """Get or create the 'Reminder Bot' reminder list for VTODOs."""
    if not hasattr(self, '_bot_reminders') or self._bot_reminders is None:
        self._bot_reminders = self._get_or_create_calendar("Reminder Bot Reminders", "VTODO")
    return self._bot_reminders

def create_calendars_if_missing(self):
    """Ensure both bot calendar and reminder list exist. Call on /connect_apple."""
    self.get_bot_calendar()
    self.get_bot_reminders()
```

**Step 2: Add push_calendar_event()**

```python
def push_calendar_event(self, title: str, message: str, start_dt: datetime, end_dt: datetime | None = None) -> str:
    """Create a VEVENT in the Reminder Bot calendar. Returns the UID."""
    import uuid
    from icalendar import Calendar, Event

    if end_dt is None:
        end_dt = start_dt + timedelta(minutes=30)

    uid = f"reminder-bot-{uuid.uuid4()}@salah.shaxbozaka.cc"

    cal = Calendar()
    cal.add("prodid", "-//Reminder Bot//EN")
    cal.add("version", "2.0")

    event = Event()
    event.add("uid", uid)
    event.add("summary", title)
    event.add("description", message)
    event.add("dtstart", start_dt)
    event.add("dtend", end_dt)
    event.add("dtstamp", datetime.now(ZoneInfo("UTC")))

    # Add alarm
    from icalendar import Alarm
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", title)
    alarm.add("trigger", timedelta(0))
    event.add_component(alarm)

    cal.add_component(event)

    bot_cal = self.get_bot_calendar()
    bot_cal.save_event(cal.to_ical().decode())

    return uid
```

**Step 3: Add push_reminder()**

```python
def push_reminder(self, title: str, message: str, due_dt: datetime) -> str:
    """Create a VTODO in the Reminder Bot reminder list. Returns the UID."""
    import uuid
    from icalendar import Calendar, Todo

    uid = f"reminder-bot-{uuid.uuid4()}@salah.shaxbozaka.cc"

    cal = Calendar()
    cal.add("prodid", "-//Reminder Bot//EN")
    cal.add("version", "2.0")

    todo = Todo()
    todo.add("uid", uid)
    todo.add("summary", title)
    todo.add("description", message)
    todo.add("due", due_dt)
    todo.add("dtstamp", datetime.now(ZoneInfo("UTC")))
    todo.add("status", "NEEDS-ACTION")

    # Add alarm at due time
    from icalendar import Alarm
    alarm = Alarm()
    alarm.add("action", "DISPLAY")
    alarm.add("description", title)
    alarm.add("trigger", timedelta(0))
    todo.add_component(alarm)

    cal.add_component(todo)

    bot_list = self.get_bot_reminders()
    bot_list.save_event(cal.to_ical().decode())

    return uid
```

**Step 4: Add delete_item() and get_bot_items()**

```python
def delete_item(self, uid: str) -> bool:
    """Delete an event or reminder by UID. Returns True if found and deleted."""
    for cal in [self.get_bot_calendar(), self.get_bot_reminders()]:
        try:
            obj = cal.object_by_uid(uid)
            obj.delete()
            return True
        except Exception:
            continue
    return False

def get_bot_items(self) -> list[dict]:
    """Fetch all items from Reminder Bot calendar + reminders for sync-back."""
    items = []

    # Calendar events
    try:
        bot_cal = self.get_bot_calendar()
        for event in bot_cal.events():
            vevents = event.icalendar_instance.walk("VEVENT")
            for vevent in vevents:
                uid = str(vevent.get("UID", ""))
                if uid:
                    items.append({
                        "uid": uid,
                        "type": "calendar_event",
                        "summary": str(vevent.get("SUMMARY", "")),
                        "status": "active",
                    })
    except Exception as e:
        logger.warning(f"Could not fetch bot calendar events: {e}")

    # Reminders (VTODOs)
    try:
        bot_list = self.get_bot_reminders()
        for todo_item in bot_list.todos(include_completed=True):
            vtodos = todo_item.icalendar_instance.walk("VTODO")
            for vtodo in vtodos:
                uid = str(vtodo.get("UID", ""))
                status_str = str(vtodo.get("STATUS", "NEEDS-ACTION"))
                if uid:
                    items.append({
                        "uid": uid,
                        "type": "reminder",
                        "summary": str(vtodo.get("SUMMARY", "")),
                        "status": "completed" if status_str == "COMPLETED" else "active",
                    })
    except Exception as e:
        logger.warning(f"Could not fetch bot reminders: {e}")

    return items
```

**Step 5: Verify syntax**

```bash
cd /root/reminder && .venv/bin/python -c "from src.services.apple_calendar import AppleCalendarService; print('OK')"
```

---

### Task 3: Wire iCloud Context Into AI

**Files:**
- Modify: `src/services/ai.py`

**Step 1: Add iCloud data to build_user_context()**

At the end of `build_user_context()`, before `return`, add:

```python
# --- iCloud Calendar & Reminders ---
if user.apple_id and user.apple_app_password:
    try:
        from src.services.apple_calendar import AppleCalendarService
        apple_service = AppleCalendarService(user.apple_id, user.apple_app_password)
        apple_context = apple_service.format_for_context(days=7)
        if apple_context:
            parts.append(f"\n{apple_context}")
    except Exception as e:
        logger.warning(f"Could not fetch iCloud data for user {telegram_id}: {e}")
        parts.append("\niCLOUD: Connected but could not fetch (credentials may have expired)")
```

**Step 2: Update tool schemas — add target field**

Add to `create_reminder` input_schema properties:

```python
"target": {
    "type": "string",
    "enum": ["reminder", "calendar_event"],
    "description": "Where to create: 'reminder' for tasks/nudges (default), 'calendar_event' for time-blocked activities with duration"
},
"duration_minutes": {
    "type": "integer",
    "description": "Duration in minutes for calendar events. Default 30. Only used when target is 'calendar_event'."
},
```

Add to `create_recurring_reminder` input_schema properties:

```python
"target": {
    "type": "string",
    "enum": ["reminder", "calendar_event"],
    "description": "Where to create: 'reminder' for recurring tasks (default), 'calendar_event' for recurring time blocks"
},
```

**Step 3: Update SYSTEM_PROMPT**

Append to SYSTEM_PROMPT:

```python
"""
ITEM TARGETING (when user has Apple Calendar connected):
- Use target="reminder" for tasks, nudges, to-dos (e.g. "remind me to call doctor")
- Use target="calendar_event" for time-blocked activities (e.g. "meeting at 3pm for 1 hour")
- Default to "reminder" if unclear
- Items automatically sync to the user's iPhone
"""
```

**Step 4: Update execute_tool() — push to iCloud after creating task**

In the `create_reminder` branch, after `await session.commit()` and `await schedule_task(task)`, add:

```python
# Push to iCloud if connected
icloud_uid = None
user = await user_repo.get_by_telegram_id(telegram_id)
if user and user.apple_id and user.apple_app_password:
    try:
        from src.services.apple_calendar import AppleCalendarService
        apple = AppleCalendarService(user.apple_id, user.apple_app_password)
        target = tool_input.get("target", "reminder")

        if target == "calendar_event":
            duration = tool_input.get("duration_minutes", 30)
            end_at = run_at + timedelta(minutes=duration)
            icloud_uid = apple.push_calendar_event(
                tool_input["title"], tool_input["message"], run_at, end_at
            )
        else:
            icloud_uid = apple.push_reminder(
                tool_input["title"], tool_input["message"], run_at
            )

        if icloud_uid:
            task.icloud_uid = icloud_uid
            task.target = target
            await session.commit()
    except Exception as e:
        logger.warning(f"Failed to push to iCloud: {e}")
```

Add similar block in `create_recurring_reminder` (push as reminder to iCloud, since recurring calendar events are fragile).

In the `delete_reminder` branch, before `await task_repo.delete(task)`, add:

```python
# Delete from iCloud if synced
if task.icloud_uid:
    user = await user_repo.get_by_telegram_id(telegram_id)
    if user and user.apple_id and user.apple_app_password:
        try:
            from src.services.apple_calendar import AppleCalendarService
            apple = AppleCalendarService(user.apple_id, user.apple_app_password)
            apple.delete_item(task.icloud_uid)
        except Exception as e:
            logger.warning(f"Failed to delete from iCloud: {e}")
```

**Step 5: Verify syntax**

```bash
cd /root/reminder && .venv/bin/python -c "from src.services.ai import ai_service; print('OK')"
```

---

### Task 4: Sync-Back Polling Job

**Files:**
- Modify: `src/bot/scheduler.py`

**Step 1: Add sync_icloud_for_user() function**

```python
async def sync_icloud_for_user(telegram_id: int):
    """Check if any bot-pushed iCloud items were completed/deleted on iPhone."""
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id)

        if not user or not user.apple_id or not user.apple_app_password:
            return

        task_repo = TaskRepository(session)
        # Only check tasks that have icloud_uid
        from sqlalchemy import select, and_
        from src.models.scheduled_task import ScheduledTask
        stmt = select(ScheduledTask).where(
            and_(
                ScheduledTask.telegram_id == telegram_id,
                ScheduledTask.active == True,
                ScheduledTask.icloud_uid.isnot(None),
            )
        )
        result = await session.execute(stmt)
        synced_tasks = list(result.scalars().all())

        if not synced_tasks:
            return  # Nothing to sync

        try:
            from src.services.apple_calendar import AppleCalendarService
            apple = AppleCalendarService(user.apple_id, user.apple_app_password)
            icloud_items = apple.get_bot_items()
        except Exception as e:
            logger.warning(f"iCloud sync failed for {telegram_id}: {e}")
            return

        # Build a lookup: uid -> status
        icloud_status = {item["uid"]: item["status"] for item in icloud_items}

        for task in synced_tasks:
            uid = task.icloud_uid
            status = icloud_status.get(uid)

            # If UID not found (deleted) or completed on iPhone
            if status is None or status == "completed":
                logger.info(f"iCloud sync: deactivating task #{task.id} '{task.title}' (iCloud status: {status})")
                task.active = False
                from src.bot.task_scheduler import unschedule_task
                unschedule_task(task.id)

        await session.commit()
```

**Step 2: Add _icloud_sync_job callback**

```python
async def _icloud_sync_job(context):
    """Job callback: sync iCloud state for a user."""
    telegram_id = context.job.data["telegram_id"]
    try:
        await sync_icloud_for_user(telegram_id)
    except Exception as e:
        logger.error(f"iCloud sync job error for {telegram_id}: {e}")
```

**Step 3: Register sync job in schedule_user_prayers()**

At the end of `schedule_user_prayers()`, after the daily quran section, add:

```python
# Schedule iCloud sync if Apple connected
if user.apple_id and user.apple_app_password:
    sync_jobs = job_queue.get_jobs_by_name(f"icloud_sync_{user.telegram_id}")
    for job in sync_jobs:
        job.schedule_removal()

    job_queue.run_repeating(
        _icloud_sync_job,
        interval=300,  # every 5 minutes
        first=60,      # start after 1 minute
        data={"telegram_id": user.telegram_id},
        name=f"icloud_sync_{user.telegram_id}",
        chat_id=user.telegram_id,
    )
    logger.info(f"Scheduled iCloud sync for user {user.telegram_id}")
```

**Step 4: Add import for TaskRepository at top of scheduler.py**

```python
from src.repositories.task_repo import TaskRepository
```

**Step 5: Verify syntax**

```bash
cd /root/reminder && .venv/bin/python -c "from src.bot.scheduler import schedule_all_users; print('OK')"
```

---

### Task 5: Update Apple Connection Handler

**Files:**
- Modify: `src/bot/handlers/apple.py`

**Step 1: Update receive_app_password() — create calendars on connect**

After the `calendars = service.get_calendars()` line, add:

```python
# Create dedicated Reminder Bot calendar and reminder list
service.create_calendars_if_missing()
```

Update the success message:

```python
await context.bot.send_message(
    chat_id=telegram_id,
    text=(
        f"Connected successfully!\n\n"
        f"Found {len(calendars)} calendars: {cal_names}\n\n"
        f"Created 'Reminder Bot' calendar and reminder list on your iCloud.\n"
        f"Your calendar events and reminders are now visible to the AI, "
        f"and bot reminders will sync to your iPhone.\n\n"
        f"Try asking: \"What's on my calendar this week?\""
    ),
)
```

**Step 2: Trigger scheduler to start sync job**

After saving credentials and sending the success message, start the sync job:

```python
# Start iCloud sync job
from src.bot.scheduler import schedule_user_prayers
await schedule_user_prayers(context.application, user)
```

**Step 3: Verify syntax**

```bash
cd /root/reminder && .venv/bin/python -c "from src.bot.handlers.apple import get_apple_handlers; print('OK')"
```

---

### Task 6: Register Apple Handlers in main.py

**Files:**
- Modify: `src/main.py`

**Step 1: Add Apple handlers import and registration**

The Apple handlers (`get_apple_handlers()`) are defined but never registered in `main.py`. Add:

```python
from src.bot.handlers.apple import get_apple_handlers
```

And in `build_application()`, before the chat handlers:

```python
for handler in get_apple_handlers():
    application.add_handler(handler)
```

**Step 2: Verify syntax**

```bash
cd /root/reminder && .venv/bin/python -c "from src.main import build_application; print('OK')"
```

---

### Task 7: Add caldav to Docker dependencies

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add caldav to dependencies**

In the `dependencies` list, add:

```
"caldav>=2.2,<3.0",
```

(It's already installed in the venv but needs to be in pyproject.toml for Docker builds.)

---

### Task 8: Rebuild & Deploy

**Step 1: Rebuild container**

```bash
cd /root/reminder
docker compose build --no-cache
```

**Step 2: Restart**

```bash
cd /root/reminder
docker compose down && docker compose up -d
```

**Step 3: Run migration inside container**

```bash
docker exec salah-reminder alembic upgrade head
```

**Step 4: Check logs**

```bash
docker logs salah-reminder --tail 30
```

Expected: No errors, "Prayer schedules loaded", "Scheduled tasks loaded", iCloud sync job logged for connected users.

**Step 5: Test end-to-end**

In Telegram:
1. `/connect_apple` — connect with Apple ID + app-specific password
2. Ask the bot: "What's on my calendar this week?"
3. Ask: "Remind me tomorrow at 10am to drink water"
4. Check iPhone — "Reminder Bot Reminders" list should have the item
5. Ask: "Schedule a meeting with Ali tomorrow at 3pm for 1 hour"
6. Check iPhone — "Reminder Bot" calendar should have the event
7. Complete the reminder on iPhone, wait 5 min, check bot logs for sync-back

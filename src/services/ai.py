"""AI chat service using Anthropic Claude API with tools and full user context."""

import json
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import anthropic
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.prayer_log import PrayerName, PrayerStatus
from src.models.scheduled_task import TaskType
from src.repositories.prayer_repo import PrayerRepository
from src.repositories.task_repo import TaskRepository
from src.repositories.user_repo import UserRepository
from src.services.prayer import get_prayer_times, get_next_prayer
from src.services.scoring import ScoringService, STATUS_LABELS

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a knowledgeable Islamic assistant embedded in a Reminder Telegram bot. \
You help the user with anything related to Islam, Salah, Quran, Hadith, Fiqh, \
and general reminders/scheduling.

You have FULL ACCESS to the user's prayer data and can CREATE REMINDERS. Use tools proactively:
- If they ask "how am I doing?" — analyze their scores, streaks, and patterns
- If they ask to be reminded about something, use create_reminder or create_recurring_reminder
- If they want to see their reminders, use list_reminders
- If they want to cancel a reminder, use delete_reminder
- If you see many qaza or missed prayers, gently encourage them with Hadith/Quran
- If they have a strong streak, praise them
- Reference specific days, prayers, and patterns from their history
- Give personalized tips based on which prayers they struggle with

IMPORTANT for reminders:
- The current date/time and timezone are provided in USER DATA. ALWAYS use that timezone.
- For create_reminder: datetime_iso must include the timezone offset (e.g. '2026-02-25T08:00:00+05:00')
- For cron: day_of_week uses 0=Monday, 1=Tuesday, ..., 4=Friday, 6=Sunday
- Parse natural language times carefully using the current time as reference
- "tomorrow" means the next calendar day from the current time shown
- "in 5 hours" means current time + 5 hours
- When user says "remind me at 3pm" they mean their local time, not UTC

Guidelines:
- Always cite sources when referencing Quran ayahs or Hadith
- Be respectful, warm, and encouraging — like a caring friend
- Answer in the language the user writes in
- Keep answers concise but thorough

COMMUNICATION STYLE:
- Be a real friend, not an assistant. Talk like a close friend who happens to know a lot about Islam
- Be direct and honest — if it's 1am, say bro go sleep, Fajr is in 4 hours. If they missed prayers, don't sugarcoat it
- Match the user's energy. Short message = short reply. Deep question = thoughtful answer
- Use emojis and formatting naturally, don't overdo it
- Mention upcoming stuff when relevant, ask how things went — but don't dump everything you know in every message
- You can joke around, be a little tough, push them to be better — that's what real friends do

Scoring system:
Masjid: 5 | Iqama: 4 | On Time: 3 | Last Minutes: 2 | Qaza: 1 | Missed: 0

ITEM TARGETING (when user has Apple Calendar connected):
- Use target="reminder" for tasks, nudges, to-dos (e.g. "remind me to call doctor")
- Use target="calendar_event" for time-blocked activities (e.g. "meeting at 3pm for 1 hour")
- Default to "reminder" if unclear
- Items automatically sync to the user's iPhone
"""

TOOLS = [
    {
        "name": "create_reminder",
        "description": "Create a one-time reminder that sends a notification at a specific date/time.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title for the reminder"
                },
                "message": {
                    "type": "string",
                    "description": "The full notification message to send"
                },
                "datetime_iso": {
                    "type": "string",
                    "description": "ISO 8601 datetime WITH timezone offset. Example: '2026-02-25T08:00:00+05:00'. MUST include the offset."
                },
                "target": {
                    "type": "string",
                    "enum": ["reminder", "calendar_event"],
                    "description": "Where to create: 'reminder' for tasks/nudges (default), 'calendar_event' for time-blocked activities with duration"
                },
                "duration_minutes": {
                    "type": "integer",
                    "description": "Duration in minutes for calendar events. Default 30. Only used when target is 'calendar_event'."
                },
            },
            "required": ["title", "message", "datetime_iso"]
        }
    },
    {
        "name": "create_recurring_reminder",
        "description": "Create a recurring reminder using cron-like fields. Runs in the user's timezone.",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Short title for the reminder"
                },
                "message": {
                    "type": "string",
                    "description": "The notification message to send each time"
                },
                "cron_minute": {
                    "type": "string",
                    "description": "Minute (0-59 or *)"
                },
                "cron_hour": {
                    "type": "string",
                    "description": "Hour (0-23 or *)"
                },
                "cron_day": {
                    "type": "string",
                    "description": "Day of month (1-31 or *)"
                },
                "cron_month": {
                    "type": "string",
                    "description": "Month (1-12 or *)"
                },
                "cron_day_of_week": {
                    "type": "string",
                    "description": "Day of week: 0=Mon, 1=Tue, 2=Wed, 3=Thu, 4=Fri, 5=Sat, 6=Sun, or * for all"
                },
                "target": {
                    "type": "string",
                    "enum": ["reminder", "calendar_event"],
                    "description": "Where to create: 'reminder' for recurring tasks (default), 'calendar_event' for recurring time blocks"
                },
            },
            "required": ["title", "message", "cron_minute", "cron_hour", "cron_day", "cron_month", "cron_day_of_week"]
        }
    },
    {
        "name": "list_reminders",
        "description": "List all active reminders for the user.",
        "input_schema": {
            "type": "object",
            "properties": {},
        }
    },
    {
        "name": "update_prayer",
        "description": "Update a prayer log status for a specific date and prayer. Use when the user wants to correct or set how they prayed.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prayer_name": {
                    "type": "string",
                    "enum": ["fajr", "dhuhr", "asr", "maghrib", "isha"],
                    "description": "The prayer name"
                },
                "prayer_date": {
                    "type": "string",
                    "description": "The date in YYYY-MM-DD format"
                },
                "status": {
                    "type": "string",
                    "enum": ["masjid", "iqama", "on_time", "last_minutes", "qaza", "missed"],
                    "description": "The prayer status to set"
                },
            },
            "required": ["prayer_name", "prayer_date", "status"]
        }
    },
    {
        "name": "delete_reminder",
        "description": "Delete/cancel a reminder by its ID.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "The ID of the reminder to delete"
                }
            },
            "required": ["task_id"]
        }
    },
]


def _get_user_tz(user) -> ZoneInfo:
    """Get user's timezone, default to Asia/Tashkent."""
    tz_str = user.timezone if user and user.timezone != "UTC" else "Asia/Tashkent"
    try:
        return ZoneInfo(tz_str)
    except Exception:
        return ZoneInfo("UTC")


async def build_user_context(session: AsyncSession, telegram_id: int) -> str:
    """Build a rich context string with all user data for the AI."""
    user_repo = UserRepository(session)
    prayer_repo = PrayerRepository(session)
    scoring = ScoringService(session)

    user = await user_repo.get_by_telegram_id(telegram_id)

    parts = []

    # --- ALWAYS show current time first ---
    tz = _get_user_tz(user) if user else ZoneInfo("Asia/Tashkent")
    now = datetime.now(tz)
    tz_name = str(tz)

    parts.append("CURRENT DATE & TIME:")
    parts.append(f"  {now.strftime('%A, %B %d, %Y')}")
    parts.append(f"  {now.strftime('%H:%M:%S')} ({tz_name}, UTC{now.strftime('%z')})")
    parts.append(f"  ISO: {now.isoformat()}")

    if not user:
        parts.append("\nUser has not set up their account yet. Ask them to /start.")
        return "\n".join(parts)

    # --- User profile ---
    parts.append(f"\nUSER PROFILE:")
    parts.append(f"  Name: {user.first_name or 'Not set'}")
    parts.append(f"  Timezone: {tz_name}")
    parts.append(f"  Calculation method: {user.calc_method}")
    parts.append(f"  Madhab: {user.madhab}")
    parts.append(f"  Total score: {user.total_score}")
    parts.append(f"  Current streak: {user.current_streak} days")
    parts.append(f"  Best streak: {user.best_streak} days")

    # --- Today's prayer times ---
    if user.latitude and user.longitude:
        today = now.date()
        times = get_prayer_times(
            user.latitude, user.longitude, today,
            tz_name, user.calc_method, user.madhab
        )
        parts.append(f"\nTODAY'S PRAYER TIMES:")

        found_next = False
        for pt in times:
            if pt.time > now and not found_next:
                marker = " <-- NEXT"
                found_next = True
            elif pt.time <= now:
                marker = " (passed)"
            else:
                marker = ""
            parts.append(f"  {pt.name.value.capitalize():10s} {pt.time.strftime('%H:%M')}{marker}")

        next_p = get_next_prayer(
            user.latitude, user.longitude, tz_name,
            user.calc_method, user.madhab
        )
        if next_p:
            diff = next_p.time - now
            mins = int(diff.total_seconds() / 60)
            parts.append(f"  Next prayer: {next_p.name.value.capitalize()} in {mins} minutes")

    # --- Today's prayer log ---
    today_summary = await scoring.get_daily_summary(telegram_id, now.date())
    parts.append("\nTODAY'S PRAYERS:")
    if today_summary["logs"]:
        for log in today_summary["logs"]:
            label = STATUS_LABELS.get(log.status, log.status.value)
            parts.append(f"  {log.prayer_name.value.capitalize():10s} | {label} | +{log.score}")
        parts.append(f"  Today's score: {today_summary['total_points']}/{today_summary['max_possible']} ({today_summary['percentage']:.0f}%)")
    else:
        parts.append("  No prayers logged yet today.")

    # --- Weekly ---
    weekly = await scoring.get_weekly_summary(telegram_id)
    parts.append(f"\nWEEKLY SUMMARY (last 7 days):")
    parts.append(f"  Score: {weekly['total_points']}/{weekly['max_possible']} ({weekly['percentage']:.0f}%)")
    parts.append(f"  Prayers logged: {weekly['total_prayers']}")
    if weekly["status_counts"]:
        for status, count in sorted(weekly["status_counts"].items(), key=lambda x: x[0].value):
            label = STATUS_LABELS.get(status, status.value)
            parts.append(f"    {label}: {count}")

    # --- Prayer patterns ---
    seven_days_ago = now.date() - timedelta(days=7)
    recent_logs = await prayer_repo.get_date_range_logs(telegram_id, seven_days_ago, now.date())
    if recent_logs:
        prayer_stats: dict[PrayerName, dict[str, int]] = {}
        for log in recent_logs:
            if log.prayer_name not in prayer_stats:
                prayer_stats[log.prayer_name] = {"total": 0, "weak": 0, "strong": 0}
            prayer_stats[log.prayer_name]["total"] += 1
            if log.status in (PrayerStatus.QAZA, PrayerStatus.MISSED, PrayerStatus.LAST_MINUTES):
                prayer_stats[log.prayer_name]["weak"] += 1
            elif log.status in (PrayerStatus.MASJID, PrayerStatus.IQAMA):
                prayer_stats[log.prayer_name]["strong"] += 1

        parts.append("\nPRAYER PATTERNS (last 7 days):")
        for pname, stats in prayer_stats.items():
            if stats["total"] > 0:
                weak_pct = stats["weak"] / stats["total"] * 100
                strong_pct = stats["strong"] / stats["total"] * 100
                note = ""
                if weak_pct > 50:
                    note = " *** STRUGGLING ***"
                elif strong_pct > 70:
                    note = " (excellent!)"
                parts.append(f"  {pname.value.capitalize()}: {stats['strong']} strong, {stats['weak']} weak out of {stats['total']}{note}")

    # --- Active reminders ---
    task_repo = TaskRepository(session)
    tasks = await task_repo.get_user_tasks(telegram_id, active_only=True)
    if tasks:
        parts.append(f"\nACTIVE REMINDERS ({len(tasks)}):")
        for t in tasks:
            if t.task_type == TaskType.ONCE:
                when = t.run_at.strftime('%Y-%m-%d %H:%M %Z') if t.run_at else "?"
                parts.append(f"  #{t.id}: \"{t.title}\" — at {when}")
            elif t.task_type == TaskType.CRON:
                parts.append(f"  #{t.id}: \"{t.title}\" — cron: {t.cron_expression} ({t.timezone}, ran {t.run_count} times)")
    else:
        parts.append("\nACTIVE REMINDERS: None")

    # --- iCloud Calendar & Reminders ---
    if user.apple_id and user.apple_app_password:
        try:
            from src.services.apple_calendar import AppleCalendarService
            apple_service = AppleCalendarService(user.apple_id, user.apple_app_password)
            apple_context = apple_service.format_for_context(days=7, user_tz=user.timezone)
            if apple_context:
                parts.append(f"\n{apple_context}")
        except Exception as e:
            logger.warning(f"Could not fetch iCloud data for user {telegram_id}: {e}")
            parts.append("\niCLOUD: Connected but could not fetch (credentials may have expired)")

    return "\n".join(parts)


async def execute_tool(
    session: AsyncSession, telegram_id: int, tool_name: str, tool_input: dict
) -> str:
    """Execute an AI tool call and return the result."""
    task_repo = TaskRepository(session)
    user_repo = UserRepository(session)
    user = await user_repo.get_by_telegram_id(telegram_id)
    tz = _get_user_tz(user)
    tz_str = str(tz)

    if tool_name == "create_reminder":
        try:
            run_at = datetime.fromisoformat(tool_input["datetime_iso"])
            # Always ensure time is in user's timezone
            if run_at.tzinfo is None:
                run_at = run_at.replace(tzinfo=tz)
            elif run_at.utcoffset() == timedelta(0) and tz != ZoneInfo("UTC"):
                # AI sent UTC offset — treat the naive time as user's local time
                run_at = run_at.replace(tzinfo=tz)
        except ValueError as e:
            return json.dumps({"error": f"Invalid datetime: {e}"})

        now = datetime.now(tz)
        if run_at <= now:
            return json.dumps({"error": f"Cannot create reminder in the past. Current time: {now.isoformat()}"})

        task = await task_repo.create(
            telegram_id=telegram_id,
            title=tool_input["title"],
            message=tool_input["message"],
            task_type=TaskType.ONCE,
            run_at=run_at,
            timezone=tz_str,
        )
        await session.commit()

        from src.bot.task_scheduler import schedule_task
        await schedule_task(task)

        # Push to iCloud if connected
        target = tool_input.get("target", "reminder")
        user = await user_repo.get_by_telegram_id(telegram_id)
        if user and user.apple_id and user.apple_app_password:
            try:
                from src.services.apple_calendar import AppleCalendarService
                apple = AppleCalendarService(user.apple_id, user.apple_app_password)
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

        return json.dumps({
            "success": True,
            "task_id": task.id,
            "title": task.title,
            "scheduled_for": run_at.strftime("%Y-%m-%d %H:%M (%Z)"),
            "target": target,
            "synced_to_icloud": task.icloud_uid is not None,
        })

    elif tool_name == "create_recurring_reminder":
        cron_expr = (
            f"{tool_input['cron_minute']} {tool_input['cron_hour']} "
            f"{tool_input['cron_day']} {tool_input['cron_month']} "
            f"{tool_input['cron_day_of_week']}"
        )

        task = await task_repo.create(
            telegram_id=telegram_id,
            title=tool_input["title"],
            message=tool_input["message"],
            task_type=TaskType.CRON,
            cron_expression=cron_expr,
            timezone=tz_str,
        )
        await session.commit()

        from src.bot.task_scheduler import schedule_task
        await schedule_task(task)

        # Push to iCloud if connected (recurring always as reminder)
        target = tool_input.get("target", "reminder")
        user = await user_repo.get_by_telegram_id(telegram_id)
        if user and user.apple_id and user.apple_app_password:
            try:
                from src.services.apple_calendar import AppleCalendarService
                apple = AppleCalendarService(user.apple_id, user.apple_app_password)
                icloud_uid = apple.push_reminder(
                    tool_input["title"], tool_input["message"],
                    datetime.now(tz)  # due now as a reference
                )
                if icloud_uid:
                    task.icloud_uid = icloud_uid
                    task.target = target
                    await session.commit()
            except Exception as e:
                logger.warning(f"Failed to push recurring to iCloud: {e}")

        return json.dumps({
            "success": True,
            "task_id": task.id,
            "title": task.title,
            "cron": cron_expr,
            "timezone": tz_str,
        })

    elif tool_name == "update_prayer":
        try:
            prayer_name = PrayerName(tool_input["prayer_name"])
            prayer_date = date.fromisoformat(tool_input["prayer_date"])
            status = PrayerStatus(tool_input["status"])
        except (ValueError, KeyError) as e:
            return json.dumps({"error": f"Invalid input: {e}"})

        prayer_repo = PrayerRepository(session)
        log = await prayer_repo.get_log_by_date_prayer(telegram_id, prayer_name, prayer_date)
        if not log:
            return json.dumps({"error": f"No prayer log found for {prayer_name.value} on {prayer_date}"})

        old_status = log.status.value
        old_score = log.score
        await prayer_repo.update_status(log, status)

        # Update user total score
        score_diff = log.score - old_score
        if score_diff != 0:
            user = await user_repo.get_by_telegram_id(telegram_id)
            if user:
                user.total_score = (user.total_score or 0) + score_diff

        await session.commit()

        return json.dumps({
            "success": True,
            "prayer": prayer_name.value,
            "date": str(prayer_date),
            "old_status": old_status,
            "new_status": status.value,
            "score_change": score_diff,
        })

    elif tool_name == "list_reminders":
        tasks = await task_repo.get_user_tasks(telegram_id, active_only=True)
        if not tasks:
            return json.dumps({"reminders": [], "message": "No active reminders"})

        reminders = []
        for t in tasks:
            entry = {"id": t.id, "title": t.title, "type": t.task_type.value}
            if t.task_type == TaskType.ONCE and t.run_at:
                entry["scheduled_for"] = t.run_at.strftime("%Y-%m-%d %H:%M")
            elif t.task_type == TaskType.CRON:
                entry["cron"] = t.cron_expression
                entry["timezone"] = t.timezone
                entry["run_count"] = t.run_count
            reminders.append(entry)

        return json.dumps({"reminders": reminders})

    elif tool_name == "delete_reminder":
        task = await task_repo.get_by_id(tool_input["task_id"])
        if not task or task.telegram_id != telegram_id:
            return json.dumps({"error": "Reminder not found"})

        title = task.title
        from src.bot.task_scheduler import unschedule_task
        unschedule_task(task.id)

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

        await task_repo.delete(task)
        await session.commit()

        return json.dumps({"success": True, "deleted": title})

    return json.dumps({"error": f"Unknown tool: {tool_name}"})


class AIService:
    def __init__(self):
        self._client = None

    def _get_client(self) -> anthropic.AsyncAnthropic | None:
        if self._client is None:
            if not settings.anthropic_api_key:
                logger.warning("No Anthropic API key set. AI features disabled.")
                return None
            self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        return self._client

    async def chat(
        self,
        user_message: str,
        chat_history: list[dict] | None = None,
        user_context: str = "",
        session: AsyncSession | None = None,
        telegram_id: int | None = None,
    ) -> str:
        """Send a message to Claude with full user context and tool support."""
        client = self._get_client()
        if client is None:
            return (
                "AI features are not available. "
                "Please set SALAH_ANTHROPIC_API_KEY in .env"
            )

        system = SYSTEM_PROMPT
        if user_context:
            system += f"\n\n--- USER DATA (live from database) ---\n{user_context}\n--- END USER DATA ---"

        messages = []
        if chat_history:
            messages.extend(chat_history)
        messages.append({"role": "user", "content": user_message})

        try:
            max_iterations = 5
            for _ in range(max_iterations):
                response = await client.messages.create(
                    model=settings.anthropic_model,
                    max_tokens=1024,
                    system=system,
                    messages=messages,
                    tools=TOOLS,
                )

                if response.stop_reason == "end_turn":
                    text_parts = [
                        block.text for block in response.content
                        if block.type == "text"
                    ]
                    return "\n".join(text_parts) if text_parts else ""

                if response.stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": response.content})

                    tool_results = []
                    for block in response.content:
                        if block.type == "tool_use":
                            if session and telegram_id:
                                result = await execute_tool(
                                    session, telegram_id,
                                    block.name, block.input
                                )
                            else:
                                result = json.dumps({"error": "No database session"})

                            tool_results.append({
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            })

                    messages.append({"role": "user", "content": tool_results})
                    continue

                text_parts = [
                    block.text for block in response.content
                    if block.type == "text"
                ]
                return "\n".join(text_parts) if text_parts else ""

            return "I've completed the requested actions."

        except anthropic.AuthenticationError:
            logger.error("Invalid Anthropic API key")
            return "AI configuration error. Please check the API key."
        except Exception as e:
            logger.error(f"AI chat error: {e}")
            return "I'm sorry, I couldn't process your question right now. Please try again later."


# Singleton
ai_service = AIService()

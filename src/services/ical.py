"""iCal feed generator - serves prayer times and reminders as calendar events."""

import hashlib
import hmac
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.prayer_log import PrayerName
from src.models.scheduled_task import TaskType
from src.repositories.task_repo import TaskRepository
from src.repositories.user_repo import UserRepository
from src.services.prayer import get_prayer_times

# Secret for generating user tokens
_TOKEN_SECRET = "salah-reminder-ical-2026"

PRAYER_DURATIONS = {
    PrayerName.FAJR: 30,
    PrayerName.DHUHR: 20,
    PrayerName.ASR: 20,
    PrayerName.MAGHRIB: 15,
    PrayerName.ISHA: 20,
}


def generate_user_token(telegram_id: int) -> str:
    """Generate a unique, stable token for a user's calendar feed."""
    raw = hmac.new(
        _TOKEN_SECRET.encode(),
        str(telegram_id).encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    return raw


def verify_token(token: str, telegram_id: int) -> bool:
    """Verify a calendar token belongs to a user."""
    return token == generate_user_token(telegram_id)


def _ical_dt(dt: datetime) -> str:
    """Format datetime for iCal (UTC)."""
    utc = dt.astimezone(ZoneInfo("UTC"))
    return utc.strftime("%Y%m%dT%H%M%SZ")


def _uid(prefix: str, d: date, name: str, telegram_id: int) -> str:
    """Generate a stable UID for an event."""
    return f"{prefix}-{d.isoformat()}-{name}-{telegram_id}@salah.shaxbozaka.cc"


async def generate_ical_feed(session: AsyncSession, telegram_id: int) -> str:
    """Generate a full iCal feed for a user."""
    user_repo = UserRepository(session)
    task_repo = TaskRepository(session)

    user = await user_repo.get_by_telegram_id(telegram_id)
    if not user or not user.latitude:
        return _empty_calendar()

    tz = ZoneInfo(user.timezone)
    now = datetime.now(tz)
    today = now.date()

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Reminder Bot//Prayer Times//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Reminder",
        f"X-WR-TIMEZONE:{user.timezone}",
        "",
        # Timezone definition
        "BEGIN:VTIMEZONE",
        f"TZID:{user.timezone}",
        "END:VTIMEZONE",
    ]

    # Prayer times for next 14 days
    for day_offset in range(14):
        d = today + timedelta(days=day_offset)
        times = get_prayer_times(
            user.latitude, user.longitude, d,
            user.timezone, user.calc_method, user.madhab,
        )

        for pt in times:
            duration = PRAYER_DURATIONS.get(pt.name, 20)
            end_time = pt.time + timedelta(minutes=duration)
            uid = _uid("prayer", d, pt.name.value, telegram_id)

            lines.extend([
                "",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTART:{_ical_dt(pt.time)}",
                f"DTEND:{_ical_dt(end_time)}",
                f"SUMMARY:{pt.name.value.capitalize()} Prayer",
                f"DESCRIPTION:Time for {pt.name.value.capitalize()} prayer",
                "CATEGORIES:Prayer",
                "STATUS:CONFIRMED",
                # Alert 15 min before
                "BEGIN:VALARM",
                "TRIGGER:-PT15M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{pt.name.value.capitalize()} in 15 minutes",
                "END:VALARM",
                # Alert at prayer time
                "BEGIN:VALARM",
                "TRIGGER:PT0M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:Time for {pt.name.value.capitalize()}!",
                "END:VALARM",
                "END:VEVENT",
            ])

    # Scheduled reminders as events
    tasks = await task_repo.get_user_tasks(telegram_id, active_only=True)
    for task in tasks:
        if task.task_type == TaskType.ONCE and task.run_at:
            end = task.run_at + timedelta(minutes=15)
            uid = _uid("reminder", task.run_at.date(), str(task.id), telegram_id)

            lines.extend([
                "",
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTART:{_ical_dt(task.run_at)}",
                f"DTEND:{_ical_dt(end)}",
                f"SUMMARY:{task.title}",
                f"DESCRIPTION:{task.message}",
                "CATEGORIES:Reminder",
                "BEGIN:VALARM",
                "TRIGGER:PT0M",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{task.title}",
                "END:VALARM",
                "END:VEVENT",
            ])

        elif task.task_type == TaskType.CRON and task.cron_expression:
            # Generate next 14 occurrences for cron tasks
            cron_events = _generate_cron_events(task, tz, today, 14)
            for event_dt in cron_events:
                end = event_dt + timedelta(minutes=15)
                uid = _uid("cron", event_dt.date(), str(task.id), telegram_id)

                lines.extend([
                    "",
                    "BEGIN:VEVENT",
                    f"UID:{uid}",
                    f"DTSTART:{_ical_dt(event_dt)}",
                    f"DTEND:{_ical_dt(end)}",
                    f"SUMMARY:{task.title}",
                    f"DESCRIPTION:{task.message}",
                    "CATEGORIES:Reminder",
                    "BEGIN:VALARM",
                    "TRIGGER:PT0M",
                    "ACTION:DISPLAY",
                    f"DESCRIPTION:{task.title}",
                    "END:VALARM",
                    "END:VEVENT",
                ])

    lines.append("")
    lines.append("END:VCALENDAR")

    return "\r\n".join(lines)


def _generate_cron_events(task, tz, start_date, days):
    """Generate datetime occurrences from cron expression for N days."""
    parts = task.cron_expression.split()
    if len(parts) != 5:
        return []

    minute, hour, day, month, day_of_week = parts
    events = []

    for offset in range(days):
        d = start_date + timedelta(offset)

        # Check month
        if month != "*" and str(d.month) not in month.split(","):
            continue

        # Check day of month
        if day != "*" and str(d.day) not in day.split(","):
            continue

        # Check day of week (0=Mon ... 6=Sun)
        if day_of_week != "*" and str(d.weekday()) not in day_of_week.split(","):
            continue

        # Generate events for matching hours/minutes
        hours = _expand_cron_field(hour, 0, 23)
        minutes = _expand_cron_field(minute, 0, 59)

        for h in hours:
            for m in minutes:
                dt = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
                events.append(dt)

    return events


def _expand_cron_field(field: str, min_val: int, max_val: int) -> list[int]:
    """Expand a cron field like '*/5' or '1,3,5' or '8' into a list of ints."""
    if field == "*":
        return [min_val]  # For calendar, just use one value to avoid explosion

    if field.startswith("*/"):
        step = int(field[2:])
        return list(range(min_val, max_val + 1, step))

    return [int(x) for x in field.split(",") if x.isdigit()]


def _empty_calendar() -> str:
    return "\r\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Reminder Bot//Prayer Times//EN",
        "X-WR-CALNAME:Reminder",
        "END:VCALENDAR",
    ])

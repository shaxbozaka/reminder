"""Apple iCloud Calendar & Reminders integration via CalDAV."""

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import caldav

logger = logging.getLogger(__name__)

ICLOUD_CALDAV_URL = "https://caldav.icloud.com"
ICLOUD_REMINDERS_URL = "https://caldav.icloud.com"


def _to_user_tz(dt, tz: ZoneInfo) -> datetime:
    """Convert any datetime to user's timezone."""
    if not isinstance(dt, datetime):
        return dt
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(tz)


class AppleCalendarService:
    def __init__(self, apple_id: str, app_password: str):
        """Connect to iCloud CalDAV.

        Requires an app-specific password from appleid.apple.com.
        """
        self.client = caldav.DAVClient(
            url=ICLOUD_CALDAV_URL,
            username=apple_id,
            password=app_password,
        )
        self._principal = None

    def _get_principal(self):
        if self._principal is None:
            self._principal = self.client.principal()
        return self._principal

    def get_calendars(self) -> list[dict]:
        """List all calendars."""
        principal = self._get_principal()
        calendars = principal.calendars()
        return [
            {"name": cal.name, "url": str(cal.url), "id": str(cal.id)}
            for cal in calendars
        ]

    def get_upcoming_events(self, days: int = 7) -> list[dict]:
        """Get events from all calendars for the next N days."""
        principal = self._get_principal()
        calendars = principal.calendars()

        now = datetime.now(ZoneInfo("UTC"))
        end = now + timedelta(days=days)

        events = []
        for cal in calendars:
            try:
                results = cal.search(
                    start=now,
                    end=end,
                    event=True,
                    expand=True,
                )
                for event in results:
                    vevents = event.icalendar_instance.walk("VEVENT")
                    for vevent in vevents:
                        events.append({
                            "calendar": cal.name,
                            "summary": str(vevent.get("SUMMARY", "")),
                            "start": vevent.get("DTSTART").dt if vevent.get("DTSTART") else None,
                            "end": vevent.get("DTEND").dt if vevent.get("DTEND") else None,
                            "location": str(vevent.get("LOCATION", "")),
                            "description": str(vevent.get("DESCRIPTION", "")),
                        })
            except Exception as e:
                logger.warning(f"Could not read calendar '{cal.name}': {e}")

        events.sort(key=lambda x: x["start"] if x["start"] else datetime.min)
        return events

    def get_reminders(self) -> list[dict]:
        """Get incomplete reminders from iCloud."""
        principal = self._get_principal()
        calendars = principal.calendars()

        reminders = []
        for cal in calendars:
            try:
                todos = cal.search(todo=True)
                for todo in todos:
                    vtodos = todo.icalendar_instance.walk("VTODO")
                    for vtodo in vtodos:
                        status = str(vtodo.get("STATUS", ""))
                        if status == "COMPLETED":
                            continue
                        reminders.append({
                            "calendar": cal.name,
                            "summary": str(vtodo.get("SUMMARY", "")),
                            "due": vtodo.get("DUE").dt if vtodo.get("DUE") else None,
                            "priority": int(vtodo.get("PRIORITY", 0)),
                            "description": str(vtodo.get("DESCRIPTION", "")),
                        })
            except Exception:
                pass  # Not all calendars support todos

        reminders.sort(key=lambda x: x["due"] if x["due"] else datetime.max)
        return reminders


    # ── Write Methods ──────────────────────────────────────────────────

    def _get_or_create_calendar(self, name: str, supported_component: str = "VEVENT") -> caldav.Calendar:
        """Find a calendar by name, or create it."""
        principal = self._get_principal()
        for cal in principal.calendars():
            if cal.name == name:
                return cal
        return principal.make_calendar(
            name=name,
            supported_calendar_component_set=[supported_component],
        )

    def get_bot_calendar(self) -> caldav.Calendar:
        """Lazy getter for the 'Reminder Bot' calendar (VEVENT)."""
        if not hasattr(self, '_bot_calendar') or self._bot_calendar is None:
            self._bot_calendar = self._get_or_create_calendar("Reminder Bot", "VEVENT")
        return self._bot_calendar

    def get_bot_reminders(self) -> caldav.Calendar:
        """Lazy getter for the 'Reminder Bot Reminders' list (VTODO)."""
        if not hasattr(self, '_bot_reminders') or self._bot_reminders is None:
            self._bot_reminders = self._get_or_create_calendar("Reminder Bot Reminders", "VTODO")
        return self._bot_reminders

    def create_calendars_if_missing(self):
        """Ensure bot calendar and reminders list exist."""
        self.get_bot_calendar()
        self.get_bot_reminders()

    def push_calendar_event(self, title: str, message: str, start_dt: datetime, end_dt: datetime | None = None) -> str:
        """Create a VEVENT in 'Reminder Bot' calendar. Returns UID."""
        import uuid
        from icalendar import Calendar, Event, Alarm

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

        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", title)
        alarm.add("trigger", timedelta(0))
        event.add_component(alarm)

        cal.add_component(event)

        bot_cal = self.get_bot_calendar()
        bot_cal.save_event(cal.to_ical().decode())

        return uid

    def push_reminder(self, title: str, message: str, due_dt: datetime) -> str:
        """Create a VTODO in 'Reminder Bot Reminders' list. Returns UID."""
        import uuid
        from icalendar import Calendar, Todo, Alarm

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

        alarm = Alarm()
        alarm.add("action", "DISPLAY")
        alarm.add("description", title)
        alarm.add("trigger", timedelta(0))
        todo.add_component(alarm)

        cal.add_component(todo)

        bot_list = self.get_bot_reminders()
        bot_list.save_event(cal.to_ical().decode())

        return uid

    def delete_item(self, uid: str) -> bool:
        """Delete event/reminder by UID."""
        for cal in [self.get_bot_calendar(), self.get_bot_reminders()]:
            try:
                obj = cal.object_by_uid(uid)
                obj.delete()
                return True
            except Exception:
                continue
        return False

    def get_bot_items(self) -> list[dict]:
        """Fetch all items from bot calendar + reminders for sync-back."""
        items = []
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

    def format_for_context(self, days: int = 7, user_tz: str = "UTC") -> str:
        """Format calendar data for AI context. All times in user's timezone."""
        tz = ZoneInfo(user_tz)
        lines = []

        try:
            events = self.get_upcoming_events(days)
            if events:
                lines.append(f"iPHONE CALENDAR (next {days} days, {len(events)} events):")
                for ev in events[:20]:
                    start = ev["start"]
                    if isinstance(start, datetime):
                        start = _to_user_tz(start, tz)
                        start_str = start.strftime("%a %b %d, %H:%M")
                    else:
                        start_str = str(start)
                    summary = ev["summary"]
                    loc = f" @ {ev['location']}" if ev["location"] else ""
                    lines.append(f"  {start_str} — {summary}{loc}")
            else:
                lines.append("iPHONE CALENDAR: No upcoming events")
        except Exception as e:
            logger.error(f"Calendar fetch error: {e}")
            lines.append(f"iPHONE CALENDAR: Could not fetch ({e})")

        try:
            reminders = self.get_reminders()
            if reminders:
                lines.append(f"\niPHONE REMINDERS ({len(reminders)} pending):")
                for rem in reminders[:15]:
                    due = ""
                    if rem["due"]:
                        if isinstance(rem["due"], datetime):
                            d = _to_user_tz(rem["due"], tz)
                            due = f" (due {d.strftime('%a %b %d, %H:%M')})"
                        else:
                            due = f" (due {rem['due']})"
                    lines.append(f"  - {rem['summary']}{due}")
        except Exception as e:
            logger.debug(f"Reminders fetch: {e}")

        return "\n".join(lines)

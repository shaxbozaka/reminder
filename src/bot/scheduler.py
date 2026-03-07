"""Prayer time scheduler - schedules notifications for each user's prayer times."""

import logging
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram.ext import Application

from src.database import async_session
from src.models.prayer_log import PrayerName
from src.repositories.user_repo import UserRepository
from src.repositories.task_repo import TaskRepository
from src.services.prayer import get_prayer_times, get_sunrise_time

logger = logging.getLogger(__name__)

# Track which iCloud events we've already notified about (in-memory, resets on restart)
_notified_events: set[str] = set()


def _random_quran_time(tz, target_date):
    """Generate a random time between 7am-11am for Quran delivery."""
    hour = random.randint(7, 10)
    minute = random.randint(0, 59)
    return datetime.combine(target_date, datetime.min.time(), tzinfo=tz).replace(hour=hour, minute=minute)


async def schedule_user_prayers(app: Application, user):
    """Schedule prayer notifications for a specific user for today and tomorrow."""
    if not user.latitude or not user.longitude:
        return

    tz = ZoneInfo(user.timezone)
    now = datetime.now(tz)
    today = now.date()
    tomorrow = today + timedelta(days=1)

    job_queue = app.job_queue

    # Remove existing jobs for this user
    current_jobs = job_queue.get_jobs_by_name(f"prayer_{user.telegram_id}")
    for job in current_jobs:
        job.schedule_removal()

    # Schedule today's remaining prayers
    for day in [today, tomorrow]:
        times = get_prayer_times(
            user.latitude, user.longitude, day,
            user.timezone, user.calc_method, user.madhab
        )

        for prayer_time in times:
            # Calculate notification time (X minutes before adhan)
            notify_at = prayer_time.time - timedelta(minutes=user.notify_before_minutes)

            if notify_at > now:
                job_queue.run_once(
                    _send_prayer_reminder,
                    when=notify_at,
                    data={
                        "user_id": user.id,
                        "telegram_id": user.telegram_id,
                        "prayer_name": prayer_time.name,
                        "prayer_time": prayer_time.time,
                    },
                    name=f"prayer_{user.telegram_id}",
                    chat_id=user.telegram_id,
                )
                logger.info(
                    f"Scheduled {prayer_time.name.value} reminder for user "
                    f"{user.telegram_id} at {notify_at.strftime('%H:%M')}"
                )

                # Schedule follow-up if no response
                followup_at = prayer_time.time + timedelta(minutes=user.notify_before_minutes + 30)
                if followup_at > now:
                    job_queue.run_once(
                        _send_prayer_followup,
                        when=followup_at,
                        data={
                            "telegram_id": user.telegram_id,
                            "prayer_name": prayer_time.name,
                            "prayer_date": prayer_time.time,
                        },
                        name=f"prayer_{user.telegram_id}",
                        chat_id=user.telegram_id,
                    )

    # Schedule daily prayer times message (sent at Fajr adhan time)
    today_times = get_prayer_times(
        user.latitude, user.longitude, today,
        user.timezone, user.calc_method, user.madhab
    )
    if today_times:
        fajr_time = today_times[0].time  # Fajr is always first
        # Send 5 min after Fajr adhan
        send_times_at = fajr_time + timedelta(minutes=5)

        times_jobs = job_queue.get_jobs_by_name(f"daily_times_{user.telegram_id}")
        for job in times_jobs:
            job.schedule_removal()

        if send_times_at > now:
            job_queue.run_once(
                _send_daily_times,
                when=send_times_at,
                data={"telegram_id": user.telegram_id},
                name=f"daily_times_{user.telegram_id}",
                chat_id=user.telegram_id,
            )
        else:
            # If Fajr already passed today, schedule for tomorrow's Fajr
            tomorrow_times = get_prayer_times(
                user.latitude, user.longitude, tomorrow,
                user.timezone, user.calc_method, user.madhab
            )
            if tomorrow_times:
                send_times_at = tomorrow_times[0].time + timedelta(minutes=5)
                job_queue.run_once(
                    _send_daily_times,
                    when=send_times_at,
                    data={"telegram_id": user.telegram_id},
                    name=f"daily_times_{user.telegram_id}",
                    chat_id=user.telegram_id,
                )

    # Schedule daily reschedule job (runs at midnight user's local time)
    midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=tz)
    reschedule_jobs = job_queue.get_jobs_by_name(f"reschedule_{user.telegram_id}")
    for job in reschedule_jobs:
        job.schedule_removal()

    job_queue.run_once(
        _daily_reschedule,
        when=midnight,
        data={"telegram_id": user.telegram_id},
        name=f"reschedule_{user.telegram_id}",
        chat_id=user.telegram_id,
    )

    # Schedule daily Quran if enabled
    if user.daily_quran_enabled:
        quran_jobs = job_queue.get_jobs_by_name(f"quran_{user.telegram_id}")
        for job in quran_jobs:
            job.schedule_removal()

        quran_time = _random_quran_time(tz, today)
        if quran_time <= now:
            quran_time = _random_quran_time(tz, tomorrow)

        job_queue.run_once(
            _send_daily_quran,
            when=quran_time,
            data={"telegram_id": user.telegram_id},
            name=f"quran_{user.telegram_id}",
            chat_id=user.telegram_id,
        )
        logger.info(f"Scheduled daily Quran for user {user.telegram_id} at {quran_time.strftime('%H:%M')}")

    # Schedule salah-anchored productivity check-ins
    _schedule_checkins(job_queue, user, today_times, now)

    # Schedule weekly digest (Sunday) and Friday clean slate
    _schedule_weekly_jobs(job_queue, user, tz, now, today)

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


async def schedule_all_users(app: Application):
    """Schedule prayers for all configured users. Called on startup."""
    async with async_session() as session:
        repo = UserRepository(session)
        users = await repo.get_all_configured_users()

    for user in users:
        try:
            await schedule_user_prayers(app, user)
        except Exception as e:
            logger.error(f"Failed to schedule for user {user.telegram_id}: {e}")

    logger.info(f"Scheduled prayers for {len(users)} users")


async def sync_icloud_for_user(telegram_id: int):
    """Check if any bot-pushed iCloud items were completed/deleted on iPhone."""
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id)

        if not user or not user.apple_id or not user.apple_app_password:
            return

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


async def _icloud_sync_job(context):
    """Job callback: sync iCloud state and notify about upcoming events."""
    telegram_id = context.job.data["telegram_id"]
    try:
        await sync_icloud_for_user(telegram_id)
    except Exception as e:
        logger.error(f"iCloud sync job error for {telegram_id}: {e}")

    # Check for upcoming iCloud events to notify about
    try:
        await _notify_upcoming_events(context.bot, telegram_id)
    except Exception as e:
        logger.warning(f"iCloud event notification error for {telegram_id}: {e}")


async def _notify_upcoming_events(bot, telegram_id: int):
    """Send Telegram notifications for upcoming iCloud calendar events."""
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id)

        if not user or not user.apple_id or not user.apple_app_password:
            return

    try:
        from src.services.apple_calendar import AppleCalendarService
        apple = AppleCalendarService(user.apple_id, user.apple_app_password)
        events = apple.get_upcoming_events(days=1)
    except Exception as e:
        logger.warning(f"Could not fetch iCloud events for {telegram_id}: {e}")
        return

    tz = ZoneInfo(user.timezone)
    now = datetime.now(tz)

    for event in events:
        start = event.get("start")
        summary = event.get("summary", "")
        if not start or not summary:
            continue

        # Skip all-day events (date without time)
        if not hasattr(start, 'hour'):
            continue

        # Convert to user's timezone
        if start.tzinfo is None:
            start = start.replace(tzinfo=tz)
        else:
            start = start.astimezone(tz)

        # Unique key for this event occurrence
        event_key = f"{telegram_id}:{summary}:{start.isoformat()}"
        if event_key in _notified_events:
            continue

        # Notify if event is 5-20 minutes away
        diff_minutes = (start - now).total_seconds() / 60
        if 5 <= diff_minutes <= 20:
            location = event.get("location", "")
            loc_text = f"\nLocation: {location}" if location else ""
            time_str = start.strftime("%H:%M")

            await bot.send_message(
                chat_id=telegram_id,
                text=f"Coming up at {time_str}: {summary}{loc_text}",
            )
            _notified_events.add(event_key)
            logger.info(f"Notified {telegram_id} about upcoming: {summary} at {time_str}")

    # Clean up old entries (keep set from growing forever)
    stale_keys = [k for k in _notified_events if k.startswith(f"{telegram_id}:")]
    if len(stale_keys) > 100:
        for k in stale_keys[:50]:
            _notified_events.discard(k)


async def _send_prayer_reminder(context):
    """Job callback: send prayer notification."""
    data = context.job.data
    telegram_id = data["telegram_id"]

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)

    if not user:
        return

    from src.bot.handlers.prayer import send_prayer_notification

    await send_prayer_notification(
        context.bot,
        user,
        data["prayer_name"],
        data["prayer_time"],
    )



async def _send_prayer_followup(context):
    """Job callback: follow up if prayer notification was not responded to."""
    data = context.job.data
    telegram_id = data["telegram_id"]
    prayer_name = data["prayer_name"]
    prayer_date = data["prayer_date"]

    async with async_session() as session:
        from src.repositories.prayer_repo import PrayerRepository
        from src.models.prayer_log import PrayerStatus
        from zoneinfo import ZoneInfo

        prayer_repo = PrayerRepository(session)

        # Check if the prayer date is a datetime, extract date
        if isinstance(prayer_date, datetime):
            p_date = prayer_date.date()
        else:
            p_date = prayer_date

        log = await prayer_repo.get_pending_log(telegram_id, prayer_name, p_date)

        if not log:
            return  # Already responded, no need for follow-up

        # Send follow-up nudge
        from src.bot.keyboards import prayer_response_keyboard

        date_str = p_date.strftime("%Y-%m-%d")
        keyboard = prayer_response_keyboard(prayer_name, date_str)

        msg = await context.bot.send_message(
            chat_id=telegram_id,
            text=f"Hey, you haven\'t logged {prayer_name.value.capitalize()} yet. How did it go?",
            reply_markup=keyboard,
        )

        # Store follow-up message ID so it can be dismissed on response
        log.followup_message_id = msg.message_id
        await session.commit()


async def _daily_reschedule(context):
    """Job callback: reschedule prayers for the new day."""
    data = context.job.data
    telegram_id = data["telegram_id"]

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)

    if user:
        await schedule_user_prayers(context.application, user)


async def _send_daily_times(context):
    """Job callback: send today's prayer times to the user."""
    data = context.job.data
    telegram_id = data["telegram_id"]

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)

    if not user or not user.latitude:
        return

    from src.services.prayer import format_prayer_times

    tz = ZoneInfo(user.timezone)
    today = datetime.now(tz).date()
    times = get_prayer_times(
        user.latitude, user.longitude, today,
        user.timezone, user.calc_method, user.madhab
    )

    if times:
        sunrise = get_sunrise_time(
            user.latitude, user.longitude, today,
            user.timezone, user.calc_method
        )
        text = format_prayer_times(times, sunrise)
        day_name = today.strftime("%A")
        await context.bot.send_message(
            chat_id=telegram_id,
            text=f"{day_name}'s prayer times:\n\n{text}",
        )

    # Reschedule for tomorrow
    if user:
        tomorrow = today + timedelta(days=1)
        tomorrow_times = get_prayer_times(
            user.latitude, user.longitude, tomorrow,
            user.timezone, user.calc_method, user.madhab
        )
        if tomorrow_times:
            send_at = tomorrow_times[0].time + timedelta(minutes=5)
            context.job_queue.run_once(
                _send_daily_times,
                when=send_at,
                data={"telegram_id": telegram_id},
                name=f"daily_times_{telegram_id}",
                chat_id=telegram_id,
            )


async def _send_daily_quran(context):
    """Job callback: send daily Quran excerpt."""
    data = context.job.data
    telegram_id = data["telegram_id"]

    from src.bot.handlers.quran import send_daily_quran

    await send_daily_quran(context.bot, telegram_id)

    # Reschedule for tomorrow
    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)

    if user:
        tz = ZoneInfo(user.timezone)
        tomorrow = datetime.now(tz).date() + timedelta(days=1)
        quran_time = _random_quran_time(tz, tomorrow)

        context.job_queue.run_once(
            _send_daily_quran,
            when=quran_time,
            data={"telegram_id": telegram_id},
            name=f"quran_{telegram_id}",
            chat_id=telegram_id,
        )
        logger.info(f"Next Quran for {telegram_id} at {quran_time.strftime('%H:%M')}")


def _schedule_checkins(job_queue, user, today_times, now):
    """Schedule Fajr/Asr/Isha productivity check-ins."""
    # Remove existing check-in jobs
    for job in job_queue.get_jobs_by_name(f"checkin_{user.telegram_id}"):
        job.schedule_removal()

    if not today_times:
        return

    # Find Fajr, Asr, Isha times
    prayer_map = {pt.name: pt.time for pt in today_times}

    # Fajr check-in: 10 min after Fajr adhan
    fajr = prayer_map.get(PrayerName.FAJR)
    if fajr:
        checkin_at = fajr + timedelta(minutes=10)
        if checkin_at > now:
            job_queue.run_once(
                _fajr_checkin,
                when=checkin_at,
                data={"telegram_id": user.telegram_id},
                name=f"checkin_{user.telegram_id}",
                chat_id=user.telegram_id,
            )

    # Asr check-in: 5 min after Asr adhan
    asr = prayer_map.get(PrayerName.ASR)
    if asr:
        checkin_at = asr + timedelta(minutes=5)
        if checkin_at > now:
            job_queue.run_once(
                _asr_checkin,
                when=checkin_at,
                data={"telegram_id": user.telegram_id},
                name=f"checkin_{user.telegram_id}",
                chat_id=user.telegram_id,
            )

    # Isha check-in: 10 min after Isha adhan
    isha = prayer_map.get(PrayerName.ISHA)
    if isha:
        checkin_at = isha + timedelta(minutes=10)
        if checkin_at > now:
            job_queue.run_once(
                _isha_checkin,
                when=checkin_at,
                data={"telegram_id": user.telegram_id},
                name=f"checkin_{user.telegram_id}",
                chat_id=user.telegram_id,
            )


def _schedule_weekly_jobs(job_queue, user, tz, now, today):
    """Schedule Sunday weekly digest and Friday clean slate report."""
    # Remove existing weekly jobs
    for name in [f"weekly_digest_{user.telegram_id}", f"friday_report_{user.telegram_id}"]:
        for job in job_queue.get_jobs_by_name(name):
            job.schedule_removal()

    # Find next Sunday
    days_until_sunday = (6 - today.weekday()) % 7
    if days_until_sunday == 0 and now.hour >= 7:
        days_until_sunday = 7
    next_sunday = today + timedelta(days=days_until_sunday)
    # Sunday digest at 7:00 AM (around Fajr time)
    sunday_time = datetime.combine(next_sunday, datetime.min.time(), tzinfo=tz).replace(hour=7, minute=0)
    if sunday_time > now:
        job_queue.run_once(
            _weekly_digest,
            when=sunday_time,
            data={"telegram_id": user.telegram_id},
            name=f"weekly_digest_{user.telegram_id}",
            chat_id=user.telegram_id,
        )
        logger.info(f"Scheduled weekly digest for {user.telegram_id} on {next_sunday}")

    # Find next Friday
    days_until_friday = (4 - today.weekday()) % 7
    if days_until_friday == 0 and now.hour >= 20:
        days_until_friday = 7
    next_friday = today + timedelta(days=days_until_friday)
    # Friday clean slate at 8:00 PM (after Isha usually)
    friday_time = datetime.combine(next_friday, datetime.min.time(), tzinfo=tz).replace(hour=20, minute=0)
    if friday_time > now:
        job_queue.run_once(
            _friday_report,
            when=friday_time,
            data={"telegram_id": user.telegram_id},
            name=f"friday_report_{user.telegram_id}",
            chat_id=user.telegram_id,
        )
        logger.info(f"Scheduled Friday report for {user.telegram_id} on {next_friday}")


async def _fajr_checkin(context):
    """Fajr check-in: What's the one thing that must happen today?"""
    telegram_id = context.job.data["telegram_id"]
    from src.bot.handlers.chat import add_bot_message

    msg = "What's the one thing that must happen today?"
    await context.bot.send_message(chat_id=telegram_id, text=msg)
    add_bot_message(telegram_id, msg)


async def _asr_checkin(context):
    """Asr check-in: Still on track?"""
    telegram_id = context.job.data["telegram_id"]
    from src.bot.handlers.chat import add_bot_message

    async with async_session() as session:
        from src.repositories.note_repo import NoteRepository
        note_repo = NoteRepository(session)
        today_notes = await note_repo.get_notes_since(
            telegram_id,
            datetime.now() - timedelta(hours=12)
        )

    open_count = sum(1 for n in today_notes if n.status.value == "open")

    if open_count > 0:
        msg = f"Still on track? You have {open_count} open note{'s' if open_count != 1 else ''} today."
    else:
        msg = "Still on track?"
    await context.bot.send_message(chat_id=telegram_id, text=msg)
    add_bot_message(telegram_id, msg)


async def _isha_checkin(context):
    """Isha check-in: What carries to tomorrow?"""
    telegram_id = context.job.data["telegram_id"]
    from src.bot.handlers.chat import add_bot_message

    async with async_session() as session:
        from src.repositories.note_repo import NoteRepository
        note_repo = NoteRepository(session)
        open_notes = await note_repo.get_open_notes(telegram_id)

    if open_notes:
        items = "\n".join(f"  • {n.content[:60]}" for n in open_notes[:5])
        remaining = len(open_notes) - 5 if len(open_notes) > 5 else 0
        msg = f"What carries to tomorrow?\n\nStill open:\n{items}"
        if remaining > 0:
            msg += f"\n  ...and {remaining} more"
    else:
        msg = "Day's closing. Everything handled?"

    await context.bot.send_message(chat_id=telegram_id, text=msg)
    add_bot_message(telegram_id, msg)


async def _weekly_digest(context):
    """Sunday weekly digest: all captures grouped by category."""
    telegram_id = context.job.data["telegram_id"]
    from src.bot.handlers.chat import add_bot_message

    async with async_session() as session:
        from src.repositories.note_repo import NoteRepository
        note_repo = NoteRepository(session)
        week_notes = await note_repo.get_week_notes(telegram_id)
        stats = await note_repo.get_stats(
            telegram_id,
            since=datetime.now() - timedelta(days=7)
        )

    if not week_notes:
        msg = "Weekly digest: quiet week. No notes captured."
        await context.bot.send_message(chat_id=telegram_id, text=msg)
        add_bot_message(telegram_id, msg)
        return

    # Group by category
    by_cat: dict[str, list] = {}
    for n in week_notes:
        cat = n.category or "uncategorized"
        by_cat.setdefault(cat, []).append(n)

    lines = ["Weekly digest:\n"]
    for cat, notes in sorted(by_cat.items()):
        lines.append(f"[{cat}]")
        for n in notes:
            status_icon = {"open": "⬜", "done": "✅", "ignored": "➖"}.get(n.status.value, "⬜")
            lines.append(f"  {status_icon} {n.content[:70]}")
        lines.append("")

    lines.append(
        f"Stats: {stats['total']} captured, "
        f"{stats['done']} done, {stats['open']} open, "
        f"{stats['ignored']} ignored"
    )
    lines.append("\nWhat are your priorities for this week? I'll set reminders.")

    msg = "\n".join(lines)
    await context.bot.send_message(chat_id=telegram_id, text=msg)
    add_bot_message(telegram_id, msg)

    # Reschedule for next Sunday
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id)
    if user:
        tz = ZoneInfo(user.timezone)
        next_sunday = datetime.now(tz).date() + timedelta(days=7)
        sunday_time = datetime.combine(next_sunday, datetime.min.time(), tzinfo=tz).replace(hour=7)
        context.job_queue.run_once(
            _weekly_digest,
            when=sunday_time,
            data={"telegram_id": telegram_id},
            name=f"weekly_digest_{telegram_id}",
            chat_id=telegram_id,
        )


async def _friday_report(context):
    """Friday clean slate report: what was captured, done, and ignored."""
    telegram_id = context.job.data["telegram_id"]
    from src.bot.handlers.chat import add_bot_message

    async with async_session() as session:
        from src.repositories.note_repo import NoteRepository
        note_repo = NoteRepository(session)
        week_notes = await note_repo.get_week_notes(telegram_id)
        stats = await note_repo.get_stats(
            telegram_id,
            since=datetime.now() - timedelta(days=7)
        )

    if not week_notes:
        msg = "Jumu'ah Mubarak! Clean week — no notes to report."
        await context.bot.send_message(chat_id=telegram_id, text=msg)
        add_bot_message(telegram_id, msg)
        return

    done = [n for n in week_notes if n.status.value == "done"]
    still_open = [n for n in week_notes if n.status.value == "open"]
    ignored = [n for n in week_notes if n.status.value == "ignored"]

    lines = ["Jumu'ah Clean Slate\n"]

    if done:
        lines.append(f"Done ({len(done)}):")
        for n in done[:10]:
            lines.append(f"  ✅ {n.content[:60]}")
        lines.append("")

    if still_open:
        lines.append(f"Still open ({len(still_open)}):")
        for n in still_open[:10]:
            lines.append(f"  ⬜ {n.content[:60]}")
        lines.append("")

    if ignored:
        lines.append(f"Ignored ({len(ignored)}):")
        for n in ignored[:10]:
            lines.append(f"  ➖ {n.content[:60]}")
        lines.append("")

    # Completion rate
    if stats["total"] > 0:
        rate = stats["done"] / stats["total"] * 100
        lines.append(f"Completion: {rate:.0f}% ({stats['done']}/{stats['total']})")

    lines.append("\nAnything to carry forward or let go?")

    msg = "\n".join(lines)
    await context.bot.send_message(chat_id=telegram_id, text=msg)
    add_bot_message(telegram_id, msg)

    # Reschedule for next Friday
    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id)
    if user:
        tz = ZoneInfo(user.timezone)
        next_friday = datetime.now(tz).date() + timedelta(days=7)
        friday_time = datetime.combine(next_friday, datetime.min.time(), tzinfo=tz).replace(hour=20)
        context.job_queue.run_once(
            _friday_report,
            when=friday_time,
            data={"telegram_id": telegram_id},
            name=f"friday_report_{telegram_id}",
            chat_id=telegram_id,
        )

"""Prayer-related handlers: times, response buttons, summaries."""

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from src.bot.keyboards import prayer_response_keyboard
from src.database import async_session
from src.models.prayer_log import PrayerName, PrayerStatus
from src.repositories.prayer_repo import PrayerRepository
from src.repositories.user_repo import UserRepository
from src.services.motivation import get_motivation_message
from src.services.prayer import format_prayer_times, get_prayer_times, get_sunrise_time
from src.services.scoring import ScoringService

logger = logging.getLogger(__name__)



async def prayer_response_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle prayer response button press.

    Callback data format: prayer:{prayer_name}:{date}:{status}
    """
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    if len(parts) != 4:
        return

    _, prayer_name_str, date_str, status_str = parts

    try:
        prayer_name = PrayerName(prayer_name_str)
        prayer_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        status = PrayerStatus(status_str)
    except (ValueError, KeyError):
        logger.error(f"Invalid callback data: {query.data}")
        return

    telegram_id = update.effective_user.id

    async with async_session() as session:
        prayer_repo = PrayerRepository(session)
        scoring = ScoringService(session)

        log = await prayer_repo.get_pending_log(telegram_id, prayer_name, prayer_date)

        if not log:
            await query.edit_message_text(
                f"{query.message.text}\n\n(Already responded)"
            )
            return

        points = await scoring.record_prayer(telegram_id, log, status)

        # Build response message
        status_labels = {
            PrayerStatus.MASJID: "Masjid",
            PrayerStatus.IQAMA: "Iqama",
            PrayerStatus.ON_TIME: "On Time",
            PrayerStatus.LAST_MINUTES: "Last Minutes",
            PrayerStatus.QAZA: "Qaza",
        }
        label = status_labels.get(status, status.value)

        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id)
        total = user.total_score if user else 0
        response_text = (
            f"{prayer_name.value.capitalize()} - {label}\n"
            f"+{points} points | Total: {total}"
        )

        await query.edit_message_text(response_text)

        # If qaza, send motivational message
        if status == PrayerStatus.QAZA:
            motivation = get_motivation_message(is_qaza=True)
            if motivation:
                await context.bot.send_message(
                    chat_id=telegram_id,
                    text=motivation,
                )
                await prayer_repo.mark_motivation_sent(log)
                await session.commit()


async def today_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show today's prayer summary with times."""
    telegram_id = update.effective_user.id

    async with async_session() as session:
        user_repo = UserRepository(session)
        user = await user_repo.get_by_telegram_id(telegram_id)
        scoring = ScoringService(session)
        summary = await scoring.get_daily_summary(telegram_id, date.today())

    prayer_times = None
    sunrise = None
    if user and user.latitude:
        prayer_times = get_prayer_times(
            user.latitude, user.longitude, date.today(),
            user.timezone, user.calc_method, user.madhab
        )
        sunrise = get_sunrise_time(
            user.latitude, user.longitude, date.today(),
            user.timezone, user.calc_method
        )

    text = scoring.format_daily_summary(summary, prayer_times, sunrise)
    await update.message.reply_text(text, parse_mode="HTML")


async def week_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show weekly summary."""
    telegram_id = update.effective_user.id

    async with async_session() as session:
        scoring = ScoringService(session)
        summary = await scoring.get_weekly_summary(telegram_id)
        text = scoring.format_weekly_summary(summary)

    await update.message.reply_text(text)


async def score_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user's score and streak."""
    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)

    if not user:
        await update.message.reply_text("Please /start first.")
        return

    await update.message.reply_text(
        f"Your Stats\n\n"
        f"Total Score: {user.total_score}"
    )



def _calc_time_windows(prayer_time: datetime, prayer_name: PrayerName, user) -> str:
    """Calculate time windows for each prayer status.

    For Fajr, the window ends at sunrise (praying after sunrise = Qaza).
    For other prayers, uses the gap to next prayer capped at 90 min.
    """
    tz = ZoneInfo(user.timezone)
    today = prayer_time.astimezone(tz).date()

    all_times = get_prayer_times(
        user.latitude, user.longitude, today,
        user.timezone, user.calc_method, user.madhab
    )

    # For Fajr, use sunrise as the hard deadline
    if prayer_name == PrayerName.FAJR:
        sunrise = get_sunrise_time(
            user.latitude, user.longitude, today,
            user.timezone, user.calc_method
        )
        if sunrise:
            gap_min = int((sunrise - prayer_time).total_seconds() / 60)
            total_min = max(gap_min, 30)
        else:
            total_min = 90
    else:
        # Find the next prayer after this one
        end_time = None
        found_current = False
        for pt in all_times:
            if found_current:
                end_time = pt.time
                break
            if pt.name == prayer_name:
                found_current = True

        if end_time is None:
            end_time = prayer_time + timedelta(hours=2)

        gap_min = int((end_time - prayer_time).total_seconds() / 60)
        total_min = min(max(gap_min, 30), 90)

    # Iqama = first window (Masjid shares same time, jama'ah vs solo)
    iqama_end = prayer_time + timedelta(minutes=int(total_min * 0.50))
    ontime_end = prayer_time + timedelta(minutes=int(total_min * 0.70))
    lastmin_end = prayer_time + timedelta(minutes=int(total_min * 0.85))
    qaza_end = prayer_time + timedelta(minutes=total_min)

    def fmt(dt):
        return dt.astimezone(tz).strftime("%H:%M")

    lines = [
        f"\U0001f7e2 Iqama      {fmt(prayer_time)}\u2013{fmt(iqama_end)}",
        f"\U0001f7e1 On Time    {fmt(iqama_end)}\u2013{fmt(ontime_end)}",
        f"\U0001f7e0 Last Min   {fmt(ontime_end)}\u2013{fmt(lastmin_end)}",
        f"\U0001f534 Qaza       {fmt(lastmin_end)}\u2013{fmt(qaza_end)}",
    ]

    return "\n".join(lines)


PRAYER_EMOJI = {
    PrayerName.FAJR: "\U0001f305",
    PrayerName.DHUHR: "\u2600\ufe0f",
    PrayerName.ASR: "\U0001f324",
    PrayerName.MAGHRIB: "\U0001f307",
    PrayerName.ISHA: "\U0001f319",
}


async def send_prayer_notification(bot, user, prayer_name: PrayerName, prayer_time: datetime):
    """Send a prayer reminder notification with response buttons."""
    tz = ZoneInfo(user.timezone)
    time_str = prayer_time.astimezone(tz).strftime("%H:%M")
    date_str = prayer_time.astimezone(tz).strftime("%Y-%m-%d")

    emoji = PRAYER_EMOJI.get(prayer_name, "")
    windows = _calc_time_windows(prayer_time, prayer_name, user)

    text = (
        f"{emoji} {prayer_name.value.capitalize()}\n"
        f"{windows}\n\n"
        f"How did you pray?"
    )

    keyboard = prayer_response_keyboard(prayer_name, date_str)

    msg = await bot.send_message(
        chat_id=user.telegram_id,
        text=text,
        reply_markup=keyboard,
    )

    # Create pending prayer log
    async with async_session() as session:
        prayer_repo = PrayerRepository(session)
        await prayer_repo.create_log(
            user_id=user.id,
            telegram_id=user.telegram_id,
            prayer_name=prayer_name,
            prayer_date=prayer_time.astimezone(tz).date(),
            prayer_time=prayer_time,
            notification_message_id=msg.message_id,
        )
        await session.commit()

    return msg


def get_prayer_handlers():
    """Return all prayer-related handlers."""
    return [
        CommandHandler("today", today_command),
        CommandHandler("week", week_command),
        CommandHandler("score", score_command),
        CallbackQueryHandler(prayer_response_callback, pattern=r"^prayer:"),
    ]

"""Start and setup handlers."""

import logging

from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.bot.keyboards import calc_method_keyboard, madhab_keyboard, notify_timing_keyboard, settings_keyboard
from src.database import async_session
from src.repositories.user_repo import UserRepository
from src.services.prayer import get_prayer_times, get_sunrise_time, format_prayer_times


def _format_times_code(user, tz_override=None, method_override=None):
    """Format prayer times as <code> lines for Telegram HTML."""
    from datetime import date
    tz = tz_override or user.timezone
    method = method_override or user.calc_method
    times = get_prayer_times(user.latitude, user.longitude, date.today(), tz, method, user.madhab)
    sunrise = get_sunrise_time(user.latitude, user.longitude, date.today(), tz, method)
    lines = []
    for pt in times:
        name = pt.name.value.capitalize()
        time_str = pt.time.strftime("%H:%M")
        lines.append(f"<code>   {name:<8s}{time_str}</code>")
        if pt.name.value == "fajr" and sunrise:
            lines.append(f"<code>   {'Sunrise':<8s}{sunrise.strftime('%H:%M')}</code>")
    return "\n".join(lines)

logger = logging.getLogger(__name__)


def location_request_keyboard():
    """Create a reply keyboard with location request button."""
    button = KeyboardButton("Share Location", request_location=True)
    return ReplyKeyboardMarkup([[button]], one_time_keyboard=True, resize_keyboard=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command - register user and request location."""
    user = update.effective_user

    async with async_session() as session:
        repo = UserRepository(session)
        db_user, created = await repo.get_or_create(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        await session.commit()

    welcome = (
        f"Assalamu Alaikum{', ' + user.first_name if user.first_name else ''}!\n\n"
        "Welcome to Reminder - your smart companion.\n\n"
        "I will:\n"
        "  - Remind you before each prayer\n"
        "  - Track how you pray (Masjid, Iqama, On-time...)\n"
        "  - Send daily Quran with translation\n"
        "  - Motivate you with Hadith when needed\n"
        "  - Answer your questions with AI\n"
        "  - Schedule any reminders you need\n\n"
        "Please share your location so I can calculate accurate prayer times."
    )

    await update.message.reply_text(
        welcome,
        reply_markup=location_request_keyboard(),
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle shared location to set up prayer times."""
    location = update.message.location
    if not location:
        return

    telegram_id = update.effective_user.id
    lat, lon = location.latitude, location.longitude

    timezone = await _get_timezone(lat, lon)

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_or_create(telegram_id=telegram_id)
        user = user[0]  # get_or_create returns (user, created)
        await repo.update_location(telegram_id, lat, lon, timezone)
        await session.commit()

        # Re-fetch to get updated user
        user = await repo.get_by_telegram_id(telegram_id)

    if user:
        times_text = _format_times_code(user, tz_override=timezone)

        await update.message.reply_text(
            f"Location saved! Timezone: {timezone}\n\n"
            f"{times_text}\n\n"
            f"Use /settings to configure preferences.",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )

        from src.bot.scheduler import schedule_user_prayers
        await schedule_user_prayers(context.application, user)


async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command."""
    await update.message.reply_text(
        "Settings\n\nChoose what to configure:",
        reply_markup=settings_keyboard(),
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle settings button callbacks."""
    query = update.callback_query
    await query.answer()

    action = query.data.split(":")[1]

    if action == "calc_method":
        await query.edit_message_text(
            "Select your prayer time calculation method:",
            reply_markup=calc_method_keyboard(),
        )
    elif action == "madhab":
        await query.edit_message_text(
            "Select your Madhab (affects Asr time):",
            reply_markup=madhab_keyboard(),
        )
    elif action == "notify_timing":
        await query.edit_message_text(
            "When should I notify you about prayers?",
            reply_markup=notify_timing_keyboard(),
        )
    elif action == "quran_toggle":
        telegram_id = update.effective_user.id
        async with async_session() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(telegram_id)
            if user:
                user.daily_quran_enabled = not user.daily_quran_enabled
                await session.commit()
                status = "enabled" if user.daily_quran_enabled else "disabled"
                await query.edit_message_text(
                    f"Daily Quran is now {status}.\n\n"
                    f"{'You will receive a daily Quran excerpt at ' + str(user.daily_quran_hour) + ':00.' if user.daily_quran_enabled else 'You will no longer receive daily Quran excerpts.'}"
                )

                # Re-schedule to update quran job
                from src.bot.scheduler import schedule_user_prayers
                await schedule_user_prayers(context.application, user)


async def calc_method_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle calculation method selection."""
    query = update.callback_query
    await query.answer()

    method = query.data.split(":")[1]
    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)
        if user:
            user.calc_method = method
            await session.commit()

            times_text = _format_times_code(user, method_override=method)

            await query.edit_message_text(
                f"Calculation method: {method.replace('_', ' ').title()}\n\n"
                f"{times_text}",
                parse_mode="HTML",
            )

            from src.bot.scheduler import schedule_user_prayers
            await schedule_user_prayers(context.application, user)


async def madhab_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle madhab selection."""
    query = update.callback_query
    await query.answer()

    madhab = query.data.split(":")[1]
    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)
        if user:
            user.madhab = madhab
            await session.commit()

            await query.edit_message_text(
                f"Madhab updated to: {madhab.capitalize()}\n\n"
                f"This affects your Asr prayer time calculation."
            )

            from src.bot.scheduler import schedule_user_prayers
            await schedule_user_prayers(context.application, user)


async def notify_timing_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle notification timing selection."""
    query = update.callback_query
    await query.answer()

    minutes = int(query.data.split(":")[1])
    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)
        if user:
            user.notify_before_minutes = minutes
            await session.commit()

            if minutes == 0:
                text = "Notifications set to: at adhan time"
            else:
                text = f"Notifications set to: {minutes} minutes before adhan"

            await query.edit_message_text(text)

            from src.bot.scheduler import schedule_user_prayers
            await schedule_user_prayers(context.application, user)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command."""
    help_text = (
        "Commands\n\n"
        "/start - Set up your account\n"
        "/today - Today's prayer times & summary\n"
        "/week - Weekly report\n"
        "/score - Your score & streak\n"
        "/n - Quick capture a note\n"
        "/done - Mark notes complete\n"
        "/quran - Get a Quran excerpt\n"
        "/connect_apple - Connect iCloud\n"
        "/settings - Preferences\n"
        "/clear - Clear AI chat history\n"
        "/help - Show this help\n\n"
        "Quick capture:\n"
        "  /n buy groceries — saves instantly\n"
        "  Forward any message — captured\n"
        "  Voice message — transcribed & saved\n\n"
        "Just type anything — I can answer questions, "
        "create reminders, and more."
    )
    await update.message.reply_text(help_text)


async def calendar_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /calendar command - give user their iCal subscription URL."""
    telegram_id = update.effective_user.id

    from src.services.ical import generate_user_token
    token = generate_user_token(telegram_id)
    url = f"https://salah.shaxbozaka.cc/cal/{token}.ics"

    await update.message.reply_text(
        "Calendar Subscription\n\n"
        "Subscribe to this URL on your iPhone or Google Calendar "
        "to sync prayer times and reminders:\n\n"
        f"{url}\n\n"
        "On iPhone:\n"
        "  1. Open Settings > Calendar > Accounts\n"
        "  2. Add Account > Other > Add Subscribed Calendar\n"
        "  3. Paste the URL above\n\n"
        "On Google Calendar:\n"
        "  1. Open calendar.google.com\n"
        "  2. Other calendars (+) > From URL\n"
        "  3. Paste the URL above\n\n"
        "The calendar updates automatically with your prayer times "
        "and any reminders you create."
    )


async def _get_timezone(lat: float, lon: float) -> str:
    """Determine timezone from coordinates using geographic boundaries."""
    try:
        from timezonefinder import TimezoneFinder
        tf = TimezoneFinder()
        tz = tf.timezone_at(lat=lat, lng=lon)
        return tz or "UTC"
    except Exception:
        return "UTC"


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /profile command - open analytics Mini App."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

    await update.message.reply_text(
        "Your prayer analytics dashboard",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton(
                "\U0001f4ca Open Profile",
                web_app=WebAppInfo(url="https://salah.shaxbozaka.cc/tg-app"),
            )
        ]]),
    )


def get_start_handlers():
    """Return all handlers for the start/setup module."""
    return [
        CommandHandler("start", start_command),
        CommandHandler("settings", settings_command),
        CommandHandler("help", help_command),
        CommandHandler("profile", profile_command),
        MessageHandler(filters.LOCATION, handle_location),
        CallbackQueryHandler(settings_callback, pattern=r"^settings:"),
        CallbackQueryHandler(calc_method_callback, pattern=r"^calc_method:"),
        CallbackQueryHandler(madhab_callback, pattern=r"^madhab:"),
        CallbackQueryHandler(notify_timing_callback, pattern=r"^notify_timing:"),
        CommandHandler("calendar", calendar_command),
    ]

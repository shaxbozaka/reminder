"""Apple Calendar integration handlers."""

import logging

from telegram import Update
from telegram.ext import (
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from src.database import async_session
from src.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

APPLE_ID, APP_PASSWORD = range(2)


async def connect_apple_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start Apple Calendar connection flow."""
    await update.message.reply_text(
        "Connect Apple Calendar & Reminders\n\n"
        "I'll sync your iPhone calendar events and reminders "
        "so the AI can see your schedule and give smarter advice.\n\n"
        "You need an App-Specific Password:\n"
        "  1. Go to appleid.apple.com\n"
        "  2. Sign In > App-Specific Passwords\n"
        "  3. Generate one for 'Reminder Bot'\n\n"
        "Please send your Apple ID (email):\n\n"
        "Send /cancel to abort."
    )
    return APPLE_ID


async def receive_apple_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive Apple ID email."""
    apple_id = update.message.text.strip()

    if "@" not in apple_id:
        await update.message.reply_text("That doesn't look like an email. Please send your Apple ID email:")
        return APPLE_ID

    context.user_data["apple_id"] = apple_id

    # Delete the message containing the Apple ID for privacy
    try:
        await update.message.delete()
    except Exception:
        pass

    await update.message.reply_text(
        "Got it. Now send the App-Specific Password:\n\n"
        "(I'll delete your message immediately for security)"
    )
    return APP_PASSWORD


async def receive_app_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive app-specific password and test connection."""
    app_password = update.message.text.strip()
    apple_id = context.user_data.get("apple_id", "")

    # Delete the password message immediately
    try:
        await update.message.delete()
    except Exception:
        pass

    telegram_id = update.effective_user.id

    # Test connection
    await context.bot.send_chat_action(chat_id=telegram_id, action="typing")

    try:
        from src.services.apple_calendar import AppleCalendarService
        service = AppleCalendarService(apple_id, app_password)
        calendars = service.get_calendars()

        # Create dedicated Reminder Bot calendar and reminder list
        service.create_calendars_if_missing()

        # Save credentials
        async with async_session() as session:
            repo = UserRepository(session)
            user = await repo.get_by_telegram_id(telegram_id)
            if user:
                user.apple_id = apple_id
                user.apple_app_password = app_password
                await session.commit()

        cal_names = ", ".join(c["name"] for c in calendars[:5])
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

        # Start iCloud sync job by re-scheduling user
        from src.bot.scheduler import schedule_user_prayers
        await schedule_user_prayers(context.application, user)

    except Exception as e:
        logger.error(f"Apple Calendar connection failed: {e}")
        await context.bot.send_message(
            chat_id=telegram_id,
            text=(
                "Connection failed. Please check:\n"
                "  - Apple ID email is correct\n"
                "  - App-Specific Password (not your regular password)\n"
                "  - Two-factor authentication is enabled on your Apple ID\n\n"
                f"Error: {str(e)[:200]}\n\n"
                "Use /connect_apple to try again."
            ),
        )

    context.user_data.pop("apple_id", None)
    return ConversationHandler.END


async def disconnect_apple_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Disconnect Apple Calendar."""
    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_telegram_id(telegram_id)
        if user:
            user.apple_id = None
            user.apple_app_password = None
            await session.commit()

    await update.message.reply_text("Apple Calendar disconnected.")


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel the connection flow."""
    context.user_data.pop("apple_id", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def get_apple_handlers():
    """Return Apple Calendar handlers."""
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("connect_apple", connect_apple_command)],
        states={
            APPLE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_apple_id)],
            APP_PASSWORD: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_app_password)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    return [
        conv_handler,
        CommandHandler("disconnect_apple", disconnect_apple_command),
    ]

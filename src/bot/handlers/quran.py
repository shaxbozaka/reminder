"""Quran-related handlers."""

import logging

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from src.services.quran import format_quran_excerpt, get_random_surah_excerpt

logger = logging.getLogger(__name__)


async def quran_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a random Quran excerpt with translation."""
    excerpt = get_random_surah_excerpt()
    if excerpt:
        text = format_quran_excerpt(excerpt)
        await update.message.reply_text(text)
    else:
        await update.message.reply_text(
            "Quran data is not loaded yet.\n"
            "Please ensure data/quran.json exists with the Quran dataset."
        )


async def send_daily_quran(bot, telegram_id: int):
    """Send daily Quran excerpt to a user. Called by scheduler."""
    excerpt = get_random_surah_excerpt()
    if excerpt:
        text = "Daily Quran\n\n" + format_quran_excerpt(excerpt)
        await bot.send_message(chat_id=telegram_id, text=text)


def get_quran_handlers():
    """Return quran-related handlers."""
    return [
        CommandHandler("quran", quran_command),
    ]

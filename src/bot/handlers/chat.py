"""AI chat handler - text and voice messages."""

import asyncio
import logging
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import CommandHandler, ContextTypes, MessageHandler, filters

from src.database import async_session
from src.services.ai import ai_service, build_user_context
from src.services.voice import transcribe_voice

logger = logging.getLogger(__name__)

# Store recent chat history per user (in-memory, limited)
_chat_histories: dict[int, list[dict]] = {}
MAX_HISTORY = 10


async def _send_natural(bot, chat_id, response):
    """Send response as multiple messages split on paragraph breaks."""
    # Split on double newlines (paragraphs)
    parts = [p.strip() for p in response.split("\n\n") if p.strip()]

    if len(parts) <= 1:
        # Short response — send as one message
        if len(response) <= 4096:
            await bot.send_message(chat_id=chat_id, text=response)
        else:
            for i in range(0, len(response), 4096):
                await bot.send_message(chat_id=chat_id, text=response[i:i + 4096])
        return

    # Group small parts together, split large ones
    messages = []
    current = ""
    for part in parts:
        # If adding this part would make the message too long, flush
        if current and len(current) + len(part) + 2 > 800:
            messages.append(current)
            current = part
        else:
            current = current + "\n\n" + part if current else part

    if current:
        messages.append(current)

    # Send each message with a small typing delay
    for i, msg in enumerate(messages):
        if i > 0:
            await bot.send_chat_action(chat_id=chat_id, action="typing")
            await asyncio.sleep(0.8)
        if len(msg) <= 4096:
            await bot.send_message(chat_id=chat_id, text=msg)
        else:
            for j in range(0, len(msg), 4096):
                await bot.send_message(chat_id=chat_id, text=msg[j:j + 4096])


async def _process_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_message: str
):
    """Shared logic for processing text (from keyboard or transcribed voice)."""
    telegram_id = update.effective_user.id

    await context.bot.send_chat_action(chat_id=telegram_id, action="typing")

    async with async_session() as session:
        user_context = await build_user_context(session, telegram_id)
        history = _chat_histories.get(telegram_id, [])

        response = await ai_service.chat(
            user_message,
            chat_history=history,
            user_context=user_context,
            session=session,
            telegram_id=telegram_id,
        )

    # Update history
    history.append({"role": "user", "content": user_message})
    history.append({"role": "assistant", "content": response})
    if len(history) > MAX_HISTORY * 2:
        history = history[-(MAX_HISTORY * 2):]
    _chat_histories[telegram_id] = history

    # Send response naturally
    await _send_natural(context.bot, telegram_id, response)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages as AI chat."""
    user_message = update.message.text
    if not user_message or user_message.startswith("/"):
        return
    await _process_message(update, context, user_message)


async def handle_voice_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle voice messages - transcribe then process as text."""
    telegram_id = update.effective_user.id
    voice = update.message.voice or update.message.audio

    if not voice:
        return

    await context.bot.send_chat_action(chat_id=telegram_id, action="typing")

    # Download voice file
    try:
        file = await context.bot.get_file(voice.file_id)

        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            tmp_path = tmp.name
            await file.download_to_drive(tmp_path)

        # Transcribe
        text = await transcribe_voice(tmp_path)

        # Clean up
        Path(tmp_path).unlink(missing_ok=True)

        if not text:
            await update.message.reply_text(
                "Couldn't catch that. Try again or type it out."
            )
            return

        # Show what was heard then process
        await update.message.reply_text(f"\"{text}\"")

        await _process_message(update, context, text)

    except Exception as e:
        logger.error(f"Voice processing error: {e}")
        await update.message.reply_text(
            "Couldn't process the voice message. Try typing instead."
        )


async def clear_chat_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Clear AI chat history."""
    telegram_id = update.effective_user.id
    _chat_histories.pop(telegram_id, None)
    await update.message.reply_text("Chat history cleared.")


def get_chat_handlers():
    """Return chat handlers. NOTE: These must be added LAST."""
    return [
        CommandHandler("clear", clear_chat_command),
        MessageHandler(filters.VOICE | filters.AUDIO, handle_voice_message),
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message),
    ]

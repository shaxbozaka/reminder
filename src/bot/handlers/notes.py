"""Note capture and productivity handlers."""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from src.database import async_session
from src.models.note import NoteSource
from src.repositories.note_repo import NoteRepository

logger = logging.getLogger(__name__)


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /n command — instant note capture."""
    text = update.message.text
    # Strip /n or /note prefix
    if text.startswith("/note "):
        content = text[6:].strip()
    elif text.startswith("/n "):
        content = text[3:].strip()
    else:
        await update.message.reply_text("Usage: /n your note here")
        return

    if not content:
        await update.message.reply_text("Usage: /n your note here")
        return

    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = NoteRepository(session)
        note = await repo.create(telegram_id, content, source=NoteSource.TEXT)
        await session.commit()

    await update.message.reply_text(f"Captured. #{note.id}")


async def handle_forwarded_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle forwarded messages — capture as notes."""
    msg = update.message
    if not msg.forward_date:
        return

    # Build content from forwarded message
    parts = []
    if msg.forward_from:
        parts.append(f"From: {msg.forward_from.first_name or msg.forward_from.username}")
    elif msg.forward_sender_name:
        parts.append(f"From: {msg.forward_sender_name}")
    elif msg.forward_from_chat:
        parts.append(f"From: {msg.forward_from_chat.title}")

    if msg.text:
        parts.append(msg.text)
    elif msg.caption:
        parts.append(msg.caption)
    else:
        parts.append("[media message]")

    content = "\n".join(parts)
    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = NoteRepository(session)
        note = await repo.create(telegram_id, content, source=NoteSource.FORWARD)
        await session.commit()

    await update.message.reply_text(f"Captured. #{note.id}")


async def done_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /done command — mark notes as complete."""
    telegram_id = update.effective_user.id
    text = update.message.text.strip()

    # /done <id> — mark specific note done
    parts = text.split()
    if len(parts) >= 2:
        try:
            note_id = int(parts[1])
        except ValueError:
            await update.message.reply_text("Usage: /done <note_id> or just /done to see open notes")
            return

        async with async_session() as session:
            repo = NoteRepository(session)
            note = await repo.mark_done_by_id(telegram_id, note_id)
            await session.commit()

        if note:
            await update.message.reply_text(f"Done: {note.content[:80]}")
        else:
            await update.message.reply_text("Note not found or already completed.")
        return

    # /done with no args — show open notes with done buttons
    async with async_session() as session:
        repo = NoteRepository(session)
        open_notes = await repo.get_open_notes(telegram_id)

    if not open_notes:
        await update.message.reply_text("Nothing open. Clean slate.")
        return

    # Show up to 20 open notes with inline buttons
    lines = []
    keyboard = []
    for note in open_notes[:20]:
        preview = note.content[:60] + ("..." if len(note.content) > 60 else "")
        lines.append(f"#{note.id} — {preview}")
        keyboard.append([
            InlineKeyboardButton(
                f"Done #{note.id}", callback_data=f"note_done:{note.id}"
            )
        ])

    text = f"Open notes ({len(open_notes)}):\n\n" + "\n".join(lines)
    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def note_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline button to mark note done."""
    query = update.callback_query
    await query.answer()

    note_id = int(query.data.split(":")[1])
    telegram_id = update.effective_user.id

    async with async_session() as session:
        repo = NoteRepository(session)
        note = await repo.mark_done_by_id(telegram_id, note_id)
        await session.commit()

    if note:
        await query.edit_message_text(
            query.message.text + f"\n\n#{note_id} done."
        )
    else:
        await query.answer("Already done or not found.")


def get_note_handlers():
    """Return note/productivity handlers. Must be registered BEFORE chat handlers."""
    return [
        CommandHandler("n", note_command),
        CommandHandler("note", note_command),
        CommandHandler("done", done_command),
        CallbackQueryHandler(note_done_callback, pattern=r"^note_done:"),
        MessageHandler(filters.FORWARDED & ~filters.COMMAND, handle_forwarded_message),
    ]

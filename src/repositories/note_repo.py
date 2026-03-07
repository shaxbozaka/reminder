"""Repository for quick-capture notes."""

from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.note import Note, NoteSource, NoteStatus


class NoteRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, telegram_id: int, content: str,
                     source: NoteSource = NoteSource.TEXT,
                     category: str | None = None) -> Note:
        note = Note(
            telegram_id=telegram_id,
            content=content,
            source=source,
            category=category,
        )
        self.session.add(note)
        await self.session.flush()
        return note

    async def get_by_id(self, note_id: int) -> Note | None:
        return await self.session.get(Note, note_id)

    async def get_open_notes(self, telegram_id: int) -> list[Note]:
        stmt = (
            select(Note)
            .where(and_(
                Note.telegram_id == telegram_id,
                Note.status == NoteStatus.OPEN,
            ))
            .order_by(Note.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_notes_since(self, telegram_id: int, since: datetime) -> list[Note]:
        """Get all notes (any status) created since a given datetime."""
        stmt = (
            select(Note)
            .where(and_(
                Note.telegram_id == telegram_id,
                Note.created_at >= since,
            ))
            .order_by(Note.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_week_notes(self, telegram_id: int) -> list[Note]:
        """Get all notes from the last 7 days."""
        since = datetime.now() - timedelta(days=7)
        return await self.get_notes_since(telegram_id, since)

    async def mark_done(self, note: Note):
        note.status = NoteStatus.DONE
        await self.session.flush()

    async def mark_ignored(self, note: Note):
        note.status = NoteStatus.IGNORED
        await self.session.flush()

    async def mark_done_by_id(self, telegram_id: int, note_id: int) -> Note | None:
        note = await self.get_by_id(note_id)
        if note and note.telegram_id == telegram_id and note.status == NoteStatus.OPEN:
            note.status = NoteStatus.DONE
            await self.session.flush()
            return note
        return None

    async def get_stats(self, telegram_id: int, since: datetime | None = None) -> dict:
        """Get note stats: total, open, done, ignored."""
        base = select(func.count()).select_from(Note).where(Note.telegram_id == telegram_id)
        if since:
            base = base.where(Note.created_at >= since)

        total = (await self.session.execute(base)).scalar() or 0
        open_count = (await self.session.execute(
            base.where(Note.status == NoteStatus.OPEN)
        )).scalar() or 0
        done_count = (await self.session.execute(
            base.where(Note.status == NoteStatus.DONE)
        )).scalar() or 0
        ignored_count = (await self.session.execute(
            base.where(Note.status == NoteStatus.IGNORED)
        )).scalar() or 0

        return {
            "total": total,
            "open": open_count,
            "done": done_count,
            "ignored": ignored_count,
        }

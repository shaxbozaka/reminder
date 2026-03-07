"""Quick-capture notes for the productivity system."""

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class NoteSource(str, enum.Enum):
    TEXT = "text"          # /n command or direct text
    VOICE = "voice"        # Voice message transcribed
    FORWARD = "forward"    # Forwarded message


class NoteStatus(str, enum.Enum):
    OPEN = "open"          # Active, not yet done
    DONE = "done"          # Completed
    IGNORED = "ignored"    # Acknowledged but not acted on


class Note(Base, TimestampMixin):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    content: Mapped[str] = mapped_column(Text)
    source: Mapped[NoteSource] = mapped_column(Enum(NoteSource), default=NoteSource.TEXT)
    status: Mapped[NoteStatus] = mapped_column(Enum(NoteStatus), default=NoteStatus.OPEN)

    # Optional category (assigned by AI or user)
    category: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Link to a scheduled task if this note became a reminder
    task_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

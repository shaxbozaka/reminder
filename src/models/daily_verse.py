from datetime import datetime

from sqlalchemy import BigInteger, Date, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class DailyVerse(Base, TimestampMixin):
    """Track which verse/surah was sent to each user each day."""
    __tablename__ = "daily_verses"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)
    date: Mapped[datetime] = mapped_column(Date, index=True)
    surah_number: Mapped[int] = mapped_column(Integer)
    ayah_start: Mapped[int] = mapped_column(Integer)
    ayah_end: Mapped[int] = mapped_column(Integer)
    text_arabic: Mapped[str] = mapped_column(Text)
    text_translation: Mapped[str] = mapped_column(Text)

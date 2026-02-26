from sqlalchemy import BigInteger, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Location for prayer time calculation
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    # Preferences
    calc_method: Mapped[str] = mapped_column(String(50), default="uzbekistan")
    madhab: Mapped[str] = mapped_column(String(20), default="hanafi")
    language: Mapped[str] = mapped_column(String(10), default="en")

    # Notification preferences
    notify_before_minutes: Mapped[int] = mapped_column(default=0)
    daily_quran_enabled: Mapped[bool] = mapped_column(default=True)
    daily_quran_hour: Mapped[int] = mapped_column(default=8)

    # Apple iCloud integration (CalDAV)
    apple_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    apple_app_password: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Stats
    total_score: Mapped[int] = mapped_column(default=0)
    current_streak: Mapped[int] = mapped_column(default=0)
    best_streak: Mapped[int] = mapped_column(default=0)

    # Relationships
    prayer_logs: Mapped[list["PrayerLog"]] = relationship(back_populates="user")


# Import here to avoid circular imports at module level
from src.models.prayer_log import PrayerLog  # noqa: E402, F401

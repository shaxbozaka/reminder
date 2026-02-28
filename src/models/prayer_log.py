import enum
from datetime import datetime

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin


class PrayerName(str, enum.Enum):
    FAJR = "fajr"
    DHUHR = "dhuhr"
    ASR = "asr"
    MAGHRIB = "maghrib"
    ISHA = "isha"
    # Optional prayers (not scored, tracked separately)
    TAHAJJUD = "tahajjud"
    DUHA = "duha"
    WITR = "witr"
    TARAWIH = "tarawih"


FARD_PRAYERS = {PrayerName.FAJR, PrayerName.DHUHR, PrayerName.ASR, PrayerName.MAGHRIB, PrayerName.ISHA}
OPTIONAL_PRAYERS = {PrayerName.TAHAJJUD, PrayerName.DUHA, PrayerName.WITR, PrayerName.TARAWIH}


class PrayerStatus(str, enum.Enum):
    PENDING = "pending"      # Notification sent, waiting for response
    MASJID = "masjid"        # 5 points
    IQAMA = "iqama"          # 4 points
    ON_TIME = "on_time"      # 3 points
    LAST_MINUTES = "last_minutes"  # 2 points
    QAZA = "qaza"            # 1 point
    MISSED = "missed"        # 0 points - no response at all


SCORE_MAP = {
    PrayerStatus.MASJID: 5,
    PrayerStatus.IQAMA: 4,
    PrayerStatus.ON_TIME: 3,
    PrayerStatus.LAST_MINUTES: 2,
    PrayerStatus.QAZA: 1,
    PrayerStatus.MISSED: 0,
    PrayerStatus.PENDING: 0,
}


class PrayerLog(Base, TimestampMixin):
    __tablename__ = "prayer_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    prayer_name: Mapped[PrayerName] = mapped_column(Enum(PrayerName))
    prayer_date: Mapped[datetime] = mapped_column(Date, index=True)
    prayer_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[PrayerStatus] = mapped_column(
        Enum(PrayerStatus), default=PrayerStatus.PENDING
    )
    score: Mapped[int] = mapped_column(Integer, default=0)

    # Track which notification message this belongs to
    notification_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Motivation sent flag (for qaza follow-up)
    motivation_sent: Mapped[bool] = mapped_column(default=False)

    user: Mapped["User"] = relationship(back_populates="prayer_logs")


from src.models.user import User  # noqa: E402, F401

"""Scheduled tasks / reminders created by AI or user."""

import enum
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin


class TaskType(str, enum.Enum):
    ONCE = "once"          # One-time reminder
    CRON = "cron"          # Recurring cron schedule
    INTERVAL = "interval"  # Every N minutes/hours


class ScheduledTask(Base, TimestampMixin):
    __tablename__ = "scheduled_tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, index=True)

    # What to remind
    title: Mapped[str] = mapped_column(String(500))
    message: Mapped[str] = mapped_column(Text)

    # When to remind
    task_type: Mapped[TaskType] = mapped_column(Enum(TaskType))
    run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # for ONCE
    cron_expression: Mapped[str | None] = mapped_column(String(100), nullable=True)  # for CRON
    interval_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)  # for INTERVAL

    # State
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_run: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    run_count: Mapped[int] = mapped_column(Integer, default=0)

    # Timezone for cron
    timezone: Mapped[str] = mapped_column(String(50), default="UTC")

    # iCloud sync
    icloud_uid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target: Mapped[str] = mapped_column(String(20), default="reminder")  # "reminder" or "calendar_event"

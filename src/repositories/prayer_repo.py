from datetime import date, datetime, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.prayer_log import SCORE_MAP, PrayerLog, PrayerName, PrayerStatus


class PrayerRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_log(
        self,
        user_id: int,
        telegram_id: int,
        prayer_name: PrayerName,
        prayer_date: date,
        prayer_time: datetime,
        notification_message_id: int | None = None,
    ) -> PrayerLog:
        log = PrayerLog(
            user_id=user_id,
            telegram_id=telegram_id,
            prayer_name=prayer_name,
            prayer_date=prayer_date,
            prayer_time=prayer_time,
            notification_message_id=notification_message_id,
        )
        self.session.add(log)
        await self.session.flush()
        return log

    async def get_pending_log(
        self, telegram_id: int, prayer_name: PrayerName, prayer_date: date
    ) -> PrayerLog | None:
        stmt = select(PrayerLog).where(
            and_(
                PrayerLog.telegram_id == telegram_id,
                PrayerLog.prayer_name == prayer_name,
                PrayerLog.prayer_date == prayer_date,
                PrayerLog.status == PrayerStatus.PENDING,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(
        self, log: PrayerLog, status: PrayerStatus
    ) -> PrayerLog:
        log.status = status
        log.score = SCORE_MAP[status]
        log.response_time = datetime.now(log.prayer_time.tzinfo)
        await self.session.flush()
        return log

    async def get_log_by_date_prayer(
        self, telegram_id: int, prayer_name: PrayerName, prayer_date: date
    ) -> PrayerLog | None:
        """Get a prayer log by date and prayer name (any status)."""
        stmt = select(PrayerLog).where(
            and_(
                PrayerLog.telegram_id == telegram_id,
                PrayerLog.prayer_name == prayer_name,
                PrayerLog.prayer_date == prayer_date,
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_today_logs(self, telegram_id: int, today: date) -> list[PrayerLog]:
        stmt = (
            select(PrayerLog)
            .where(
                and_(
                    PrayerLog.telegram_id == telegram_id,
                    PrayerLog.prayer_date == today,
                )
            )
            .order_by(PrayerLog.prayer_time)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_date_range_logs(
        self, telegram_id: int, start_date: date, end_date: date
    ) -> list[PrayerLog]:
        stmt = (
            select(PrayerLog)
            .where(
                and_(
                    PrayerLog.telegram_id == telegram_id,
                    PrayerLog.prayer_date >= start_date,
                    PrayerLog.prayer_date <= end_date,
                )
            )
            .order_by(PrayerLog.prayer_date, PrayerLog.prayer_time)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_total_score(self, telegram_id: int) -> int:
        stmt = select(func.sum(PrayerLog.score)).where(
            PrayerLog.telegram_id == telegram_id
        )
        result = await self.session.execute(stmt)
        return result.scalar() or 0

    async def get_streak(self, telegram_id: int) -> int:
        """Calculate current streak of days with all 5 prayers logged (non-missed)."""
        stmt = (
            select(PrayerLog.prayer_date, func.count(PrayerLog.id))
            .where(
                and_(
                    PrayerLog.telegram_id == telegram_id,
                    PrayerLog.status != PrayerStatus.MISSED,
                    PrayerLog.status != PrayerStatus.PENDING,
                )
            )
            .group_by(PrayerLog.prayer_date)
            .having(func.count(PrayerLog.id) >= 5)
            .order_by(PrayerLog.prayer_date.desc())
        )
        result = await self.session.execute(stmt)
        rows = result.all()

        if not rows:
            return 0

        streak = 0
        expected_date = date.today()
        for row_date, _ in rows:
            if row_date == expected_date:
                streak += 1
                expected_date = expected_date - timedelta(days=1)
            else:
                break

        return streak

    async def mark_motivation_sent(self, log: PrayerLog):
        log.motivation_sent = True
        await self.session.flush()

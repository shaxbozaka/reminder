from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        stmt = select(User).where(User.telegram_id == telegram_id)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(
        self, telegram_id: int, username: str | None = None, first_name: str | None = None
    ) -> tuple[User, bool]:
        """Returns (user, created) tuple."""
        user = await self.get_by_telegram_id(telegram_id)
        if user:
            return user, False

        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
        )
        self.session.add(user)
        await self.session.flush()
        return user, True

    async def update_location(
        self, telegram_id: int, latitude: float, longitude: float, timezone: str
    ) -> User | None:
        user = await self.get_by_telegram_id(telegram_id)
        if not user:
            return None
        user.latitude = latitude
        user.longitude = longitude
        user.timezone = timezone
        await self.session.flush()
        return user

    async def update_score(self, user: User, points: int):
        user.total_score += points
        await self.session.flush()

    async def update_streak(self, user: User, streak: int):
        user.current_streak = streak
        if streak > user.best_streak:
            user.best_streak = streak
        await self.session.flush()

    async def get_all_configured_users(self) -> list[User]:
        """Get all users who have set their location (ready for reminders)."""
        stmt = select(User).where(User.latitude.isnot(None), User.longitude.isnot(None))
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

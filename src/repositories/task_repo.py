"""Repository for scheduled tasks."""

from datetime import datetime

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.scheduled_task import ScheduledTask, TaskType


class TaskRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs) -> ScheduledTask:
        task = ScheduledTask(**kwargs)
        self.session.add(task)
        await self.session.flush()
        return task

    async def get_by_id(self, task_id: int) -> ScheduledTask | None:
        return await self.session.get(ScheduledTask, task_id)

    async def get_user_tasks(self, telegram_id: int, active_only: bool = True) -> list[ScheduledTask]:
        stmt = select(ScheduledTask).where(ScheduledTask.telegram_id == telegram_id)
        if active_only:
            stmt = stmt.where(ScheduledTask.active == True)
        stmt = stmt.order_by(ScheduledTask.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_all_active(self) -> list[ScheduledTask]:
        stmt = select(ScheduledTask).where(ScheduledTask.active == True)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def deactivate(self, task: ScheduledTask):
        task.active = False
        await self.session.flush()

    async def mark_run(self, task: ScheduledTask):
        task.last_run = datetime.now(task.run_at.tzinfo if task.run_at else None)
        task.run_count += 1
        # Deactivate one-time tasks after they fire
        if task.task_type == TaskType.ONCE:
            task.active = False
        await self.session.flush()

    async def delete(self, task: ScheduledTask):
        await self.session.delete(task)
        await self.session.flush()

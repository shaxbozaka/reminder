"""Task scheduler - manages user-created reminders via APScheduler."""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram.ext import Application

from src.database import async_session
from src.models.scheduled_task import ScheduledTask, TaskType
from src.repositories.task_repo import TaskRepository

logger = logging.getLogger(__name__)

# Global reference to the application (set during startup)
_app: Application | None = None


def set_app(app: Application):
    global _app
    _app = app


async def schedule_task(task: ScheduledTask):
    """Schedule a task in APScheduler."""
    if not _app:
        logger.warning("App not set, cannot schedule task")
        return

    job_queue = _app.job_queue
    job_name = f"task_{task.id}"

    # Remove existing job if any
    existing = job_queue.get_jobs_by_name(job_name)
    for job in existing:
        job.schedule_removal()

    if task.task_type == TaskType.ONCE and task.run_at:
        tz = ZoneInfo(task.timezone)
        run_at = task.run_at if task.run_at.tzinfo else task.run_at.replace(tzinfo=tz)
        now = datetime.now(tz)
        if run_at > now:
            job_queue.run_once(
                _fire_task,
                when=run_at,
                data={"task_id": task.id},
                name=job_name,
                chat_id=task.telegram_id,
            )
            logger.info(f"Scheduled one-time task #{task.id} '{task.title}' at {task.run_at}")

    elif task.task_type == TaskType.CRON and task.cron_expression:
        parts = task.cron_expression.split()
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts

            # Convert cron fields to APScheduler kwargs
            kwargs = {}
            if minute != "*":
                kwargs["minute"] = minute
            if hour != "*":
                kwargs["hour"] = hour
            if day != "*":
                kwargs["day"] = day
            if month != "*":
                kwargs["month"] = month
            if day_of_week != "*":
                # APScheduler uses 0=mon..6=sun, same as our cron
                kwargs["day_of_week"] = day_of_week

            tz = ZoneInfo(task.timezone)

            job_queue.run_custom(
                _fire_task,
                job_kwargs={
                    "trigger": "cron",
                    "timezone": tz,
                    **kwargs,
                },
                data={"task_id": task.id},
                name=job_name,
                chat_id=task.telegram_id,
            )
            logger.info(f"Scheduled cron task #{task.id} '{task.title}' [{task.cron_expression}]")


def unschedule_task(task_id: int):
    """Remove a task from APScheduler."""
    if not _app:
        return

    job_name = f"task_{task_id}"
    existing = _app.job_queue.get_jobs_by_name(job_name)
    for job in existing:
        job.schedule_removal()
    logger.info(f"Unscheduled task #{task_id}")


async def _fire_task(context):
    """Callback when a scheduled task fires - send notification."""
    task_id = context.job.data["task_id"]

    async with async_session() as session:
        repo = TaskRepository(session)
        task = await repo.get_by_id(task_id)

        if not task or not task.active:
            return

        # Send the reminder
        try:
            await context.bot.send_message(
                chat_id=task.telegram_id,
                text=f"Reminder: {task.title}\n\n{task.message}",
            )
            logger.info(f"Fired task #{task.id} '{task.title}' to {task.telegram_id}")
        except Exception as e:
            logger.error(f"Failed to send task #{task.id}: {e}")

        # Mark as run
        await repo.mark_run(task)
        await session.commit()


async def load_all_tasks():
    """Load and schedule all active tasks from DB. Called on startup."""
    async with async_session() as session:
        repo = TaskRepository(session)
        tasks = await repo.get_all_active()

    count = 0
    for task in tasks:
        try:
            await schedule_task(task)
            count += 1
        except Exception as e:
            logger.error(f"Failed to schedule task #{task.id}: {e}")

    logger.info(f"Loaded {count} scheduled tasks from database")

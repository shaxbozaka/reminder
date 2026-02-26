"""Lightweight HTTP server for iCal feed, runs alongside the bot."""

import asyncio
import logging
from aiohttp import web

from src.database import async_session
from src.services.ical import generate_ical_feed, generate_user_token
from src.repositories.user_repo import UserRepository

logger = logging.getLogger(__name__)

_runner: web.AppRunner | None = None


async def _handle_ical(request: web.Request) -> web.Response:
    """Serve iCal feed for a user by token."""
    token = request.match_info.get("token", "")

    if not token:
        return web.Response(status=404, text="Not found")

    # Find user by token
    async with async_session() as session:
        repo = UserRepository(session)
        users = await repo.get_all_configured_users()

        target_user = None
        for user in users:
            if generate_user_token(user.telegram_id) == token:
                target_user = user
                break

        if not target_user:
            return web.Response(status=404, text="Calendar not found")

        ical_data = await generate_ical_feed(session, target_user.telegram_id)

    return web.Response(
        text=ical_data,
        content_type="text/calendar",
        charset="utf-8",
        headers={"Content-Disposition": "inline; filename=reminder.ics"},
    )


async def start_cal_server(port: int = 8444):
    """Start the calendar HTTP server."""
    global _runner

    app = web.Application()
    app.router.add_get("/cal/{token}.ics", _handle_ical)

    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Calendar server started on port {port}")


async def stop_cal_server():
    """Stop the calendar server."""
    global _runner
    if _runner:
        await _runner.cleanup()
        _runner = None

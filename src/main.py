"""Main entry point for the Reminder bot (webhook mode)."""

import logging
import threading

from telegram.ext import ApplicationBuilder

from src.bot.handlers.chat import get_chat_handlers
from src.bot.handlers.notes import get_note_handlers
from src.bot.handlers.prayer import get_prayer_handlers
from src.bot.handlers.quran import get_quran_handlers
from src.bot.handlers.start import get_start_handlers
from src.bot.handlers.apple import get_apple_handlers
from src.bot.scheduler import schedule_all_users
from src.bot.task_scheduler import load_all_tasks, set_app
from src.config import settings
from src.database import init_db

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(application):
    """Called after the application is initialized."""
    await init_db()
    logger.info("Database initialized")

    # Set app reference for task scheduler
    set_app(application)

    await schedule_all_users(application)
    logger.info("Prayer schedules loaded")

    await load_all_tasks()
    logger.info("Scheduled tasks loaded")

    # Set menu button to open Mini App (clear commands so button opens app, not command list)
    from telegram import MenuButtonWebApp, WebAppInfo
    await application.bot.delete_my_commands()
    await application.bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(
            text="Open",
            web_app=WebAppInfo(url="https://salah.shaxbozaka.cc/tg-app"),
        )
    )

    # Pre-load whisper model so first voice message is fast
    from src.services.voice import preload_model
    await preload_model()
    logger.info("Whisper model loaded")


def build_application():
    """Build and configure the application."""
    if not settings.telegram_bot_token:
        raise RuntimeError(
            "Set SALAH_TELEGRAM_BOT_TOKEN environment variable or add it to .env"
        )

    application = (
        ApplicationBuilder()
        .token(settings.telegram_bot_token)
        .post_init(post_init)
        .build()
    )

    # Register handlers (order matters - chat handler must be last)
    for handler in get_start_handlers():
        application.add_handler(handler)

    for handler in get_prayer_handlers():
        application.add_handler(handler)

    for handler in get_quran_handlers():
        application.add_handler(handler)

    for handler in get_apple_handlers():
        application.add_handler(handler)

    # Note handlers before chat (forwarded message filter must come first)
    for handler in get_note_handlers():
        application.add_handler(handler)

    # Chat handler last - catches all non-command text
    for handler in get_chat_handlers():
        application.add_handler(handler)

    return application


def _start_web_server():
    """Start FastAPI web server in a background thread."""
    import uvicorn
    from src.web.app import app as web_app

    logger.info("Starting web dashboard on port 8090")
    uvicorn.run(web_app, host="0.0.0.0", port=8090, log_level="warning")


def main():
    """Start the bot using webhook mode."""
    application = build_application()

    # Start web dashboard in background thread
    web_thread = threading.Thread(target=_start_web_server, daemon=True)
    web_thread.start()

    if settings.webhook_url:
        webhook_path = f"/webhook/{settings.telegram_bot_token}"
        webhook_url = f"{settings.webhook_url}{webhook_path}"

        logger.info(f"Starting in WEBHOOK mode on port {settings.webhook_port}")
        logger.info(f"Webhook URL: {settings.webhook_url}/webhook/***")

        application.run_webhook(
            listen=settings.webhook_listen,
            port=settings.webhook_port,
            url_path=webhook_path,
            webhook_url=webhook_url,
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
    else:
        logger.info("No WEBHOOK_URL set. Starting in POLLING mode (dev mode)")
        application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

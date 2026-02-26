from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    model_config = {"env_prefix": "SALAH_", "env_file": ".env", "env_file_encoding": "utf-8"}

    # Telegram
    telegram_bot_token: str = ""

    # Webhook (server deployment)
    webhook_url: str = ""        # e.g. "https://yourdomain.com" - leave empty for polling mode
    webhook_listen: str = "0.0.0.0"
    webhook_port: int = 8443

    # Database
    database_url: str = "postgresql+asyncpg://reminder:reminder_s3cure_pwd@db:5432/reminder"

    # Prayer times defaults (user overrides stored in DB)
    default_calc_method: str = "muslim_world_league"  # adhan calculation method
    default_madhab: str = "hanafi"

    # AI / Anthropic Claude
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-6"

    # Voice / OpenAI Whisper (for speech-to-text)
    openai_api_key: str = ""  # optional - falls back to local whisper

    # Reminders
    reminder_before_minutes: int = 0  # notify at adhan time (user can change)
    reminder_after_minutes: int = 30   # follow-up if no response
    qaza_extra_reminders: int = 3      # extra motivational msgs after qaza

    # Daily Quran
    daily_quran_hour: int = 8  # hour (user local time) for daily surah

    # Scoring
    score_masjid: int = 5
    score_iqama: int = 4
    score_on_time: int = 3
    score_last_minutes: int = 2
    score_qaza: int = 1


settings = Settings()

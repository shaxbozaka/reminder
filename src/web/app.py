"""FastAPI web application for the Salah prayer analytics dashboard."""

from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
from datetime import date, datetime
from urllib.parse import parse_qs

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import settings
from src.services.ical import generate_user_token
from src.web.analytics import get_profile_data

_TEMPLATE_DIR = pathlib.Path(__file__).parent / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=True,
)

# Own engine + session — web runs on a separate event loop (thread)
_engine = create_async_engine(settings.database_url, echo=False, pool_size=5, max_overflow=5)
_async_session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)

app = FastAPI(title="Salah Analytics", docs_url=None, redoc_url=None)


# ---------------------------------------------------------------------------
# Telegram WebApp init data verification
# ---------------------------------------------------------------------------

def _verify_telegram_init_data(init_data: str) -> int | None:
    """Verify Telegram WebApp initData and return telegram_id.

    See: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    parsed = parse_qs(init_data)
    received_hash = parsed.get("hash", [None])[0]
    if not received_hash:
        return None

    # Build data-check-string: sorted key=value pairs excluding hash
    pairs = []
    for part in init_data.split("&"):
        key, _, val = part.partition("=")
        if key != "hash":
            pairs.append(f"{key}={val}")
    pairs.sort()
    data_check_string = "\n".join(pairs)

    # HMAC-SHA256 verification
    secret_key = hmac.new(b"WebAppData", settings.telegram_bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    # Extract user id
    user_json = parsed.get("user", [None])[0]
    if not user_json:
        return None

    try:
        user = json.loads(user_json)
        return user.get("id")
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Token-based routes (browser access)
# ---------------------------------------------------------------------------

async def _resolve_user_by_token(token: str) -> int | None:
    """Find the telegram_id whose generated token matches."""
    from sqlalchemy import text

    async with _async_session() as session:
        result = await session.execute(text("SELECT telegram_id FROM users"))
        rows = result.all()

    for (tid,) in rows:
        if generate_user_token(tid) == token:
            return tid
    return None


class _DateEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (date, datetime)):
            return obj.isoformat()
        return super().default(obj)


@app.get("/profile/{token}", response_class=HTMLResponse)
async def profile_page(token: str):
    """Render the analytics dashboard (browser)."""
    telegram_id = await _resolve_user_by_token(token)
    if telegram_id is None:
        return HTMLResponse(content=_render_error("Invalid or expired link."), status_code=404)

    template = _jinja_env.get_template("profile.html")
    html = template.render(token=token)
    return HTMLResponse(content=html)


@app.get("/api/profile/{token}")
async def profile_api(token: str):
    """Return analytics data as JSON (browser)."""
    telegram_id = await _resolve_user_by_token(token)
    if telegram_id is None:
        return JSONResponse({"error": "Invalid token"}, status_code=404)

    async with _async_session() as session:
        data = await get_profile_data(session, telegram_id)

    serialized = json.loads(json.dumps(data, cls=_DateEncoder))
    return JSONResponse(serialized)


# ---------------------------------------------------------------------------
# Telegram Mini App routes
# ---------------------------------------------------------------------------

@app.get("/tg-app", response_class=HTMLResponse)
async def tg_app_page():
    """Serve the Telegram Mini App shell."""
    template = _jinja_env.get_template("tg_app.html")
    html = template.render()
    return HTMLResponse(content=html)


@app.post("/api/tg-app")
async def tg_app_api(body: dict):
    """Verify Telegram initData and return analytics."""
    init_data = body.get("initData", "")
    telegram_id = _verify_telegram_init_data(init_data)

    if telegram_id is None:
        return JSONResponse({"error": "Invalid auth"}, status_code=401)

    async with _async_session() as session:
        data = await get_profile_data(session, telegram_id)

    serialized = json.loads(json.dumps(data, cls=_DateEncoder))
    return JSONResponse(serialized)


# ---------------------------------------------------------------------------
# Error page
# ---------------------------------------------------------------------------

def _render_error(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Not Found</title>
<style>
  body {{
    background: #0f1923; color: #e0e0e0; font-family: 'Inter', system-ui, sans-serif;
    display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0;
  }}
  .card {{
    text-align: center; padding: 3rem; background: #1a2634; border-radius: 16px;
    box-shadow: 0 8px 32px rgba(0,0,0,.4);
  }}
  h1 {{ color: #ef5350; margin-bottom: .5rem; }}
  p {{ color: #90a4ae; }}
</style>
</head>
<body>
<div class="card">
  <h1>404</h1>
  <p>{message}</p>
</div>
</body>
</html>"""

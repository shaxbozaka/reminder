"""Prayer analytics computation module.

Provides aggregated statistics, trends, and insights
for a user's Salah prayer history.
"""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

STATUS_SCORES = {
    "masjid": 5,
    "iqama": 4,
    "on_time": 3,
    "last_minutes": 2,
    "qaza": 1,
    "missed": 0,
}

PRAYER_ORDER = ["fajr", "dhuhr", "asr", "maghrib", "isha"]

STATUS_ORDER = ["masjid", "iqama", "on_time", "last_minutes", "qaza", "missed"]


async def get_profile_data(session: AsyncSession, telegram_id: int) -> dict[str, Any]:
    """Build the full analytics payload for a user."""

    user = await _get_user(session, telegram_id)
    if user is None:
        return {}

    logs = await _get_all_logs(session, telegram_id)

    daily_scores = _compute_daily_scores(logs)
    prayer_breakdown = _compute_prayer_breakdown(logs)
    weekly_trend = _compute_weekly_trend(logs)
    status_distribution = _compute_status_distribution(logs)
    best_prayer, worst_prayer = _compute_best_worst_prayer(prayer_breakdown)
    fajr_rate = _compute_fajr_rate(logs)
    masjid_rate = _compute_masjid_rate(logs)
    consistency_score = _compute_consistency_score(logs)
    insights = _generate_insights(logs, prayer_breakdown, user)

    return {
        "user": {
            "name": user["first_name"] or user["username"] or "User",
            "username": user["username"],
            "total_score": user["total_score"] or 0,
            "current_streak": user["current_streak"] or 0,
            "best_streak": user["best_streak"] or 0,
        },
        "daily_scores": daily_scores,
        "prayer_breakdown": prayer_breakdown,
        "weekly_trend": weekly_trend,
        "status_distribution": status_distribution,
        "best_prayer": best_prayer,
        "worst_prayer": worst_prayer,
        "fajr_rate": fajr_rate,
        "masjid_rate": masjid_rate,
        "consistency_score": consistency_score,
        "insights": insights,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _get_user(session: AsyncSession, telegram_id: int) -> dict[str, Any] | None:
    result = await session.execute(
        text(
            "SELECT telegram_id, username, first_name, timezone, "
            "total_score, current_streak, best_streak "
            "FROM users WHERE telegram_id = :tid"
        ),
        {"tid": telegram_id},
    )
    row = result.mappings().first()
    return dict(row) if row else None


async def _get_all_logs(session: AsyncSession, telegram_id: int) -> list[dict]:
    result = await session.execute(
        text(
            "SELECT prayer_name::text, prayer_date, status::text, score "
            "FROM prayer_logs "
            "WHERE telegram_id = :tid AND status::text != 'PENDING' "
            "ORDER BY prayer_date DESC"
        ),
        {"tid": telegram_id},
    )
    # Normalize enum values to lowercase
    rows = []
    for r in result.mappings().all():
        d = dict(r)
        d["prayer_name"] = d["prayer_name"].lower()
        d["status"] = d["status"].lower()
        rows.append(d)
    return rows


def _compute_daily_scores(logs: list[dict]) -> list[dict]:
    """Last 30 days of daily score totals."""
    today = date.today()
    start = today - timedelta(days=29)

    day_points: dict[date, int] = defaultdict(int)
    day_count: dict[date, int] = defaultdict(int)

    for log in logs:
        d = _to_date(log["prayer_date"])
        if start <= d <= today:
            day_points[d] += log.get("score", 0) or 0
            day_count[d] += 1

    result = []
    for i in range(30):
        d = start + timedelta(days=i)
        result.append({
            "date": d.isoformat(),
            "points": day_points.get(d, 0),
            "max_possible": 25,  # 5 prayers * 5 max score
        })
    return result


def _compute_prayer_breakdown(logs: list[dict]) -> list[dict]:
    """Per-prayer status counts and average score."""
    stats: dict[str, dict[str, int]] = {}
    scores: dict[str, list[int]] = {}

    for name in PRAYER_ORDER:
        stats[name] = {s: 0 for s in STATUS_ORDER}
        scores[name] = []

    for log in logs:
        name = log["prayer_name"]
        status = log["status"]
        if name in stats and status in stats[name]:
            stats[name][status] += 1
            scores[name].append(log.get("score", 0) or 0)

    result = []
    for name in PRAYER_ORDER:
        s = stats[name]
        sc = scores[name]
        result.append({
            "name": name.capitalize(),
            "masjid_count": s["masjid"],
            "iqama_count": s["iqama"],
            "on_time_count": s["on_time"],
            "last_min_count": s["last_minutes"],
            "qaza_count": s["qaza"],
            "missed_count": s["missed"],
            "avg_score": round(sum(sc) / len(sc), 2) if sc else 0,
        })
    return result


def _compute_weekly_trend(logs: list[dict]) -> list[dict]:
    """Last 12 weeks of weekly score totals."""
    today = date.today()
    # Start of the current ISO week (Monday)
    current_week_start = today - timedelta(days=today.weekday())
    start = current_week_start - timedelta(weeks=11)

    week_points: dict[date, int] = defaultdict(int)
    week_count: dict[date, int] = defaultdict(int)

    for log in logs:
        d = _to_date(log["prayer_date"])
        week_start = d - timedelta(days=d.weekday())
        if start <= week_start <= current_week_start:
            week_points[week_start] += log.get("score", 0) or 0
            week_count[week_start] += 1

    result = []
    for i in range(12):
        ws = start + timedelta(weeks=i)
        result.append({
            "week_start": ws.isoformat(),
            "points": week_points.get(ws, 0),
            "max_possible": 175,  # 7 days * 5 prayers * 5 max score
        })
    return result


def _compute_status_distribution(logs: list[dict]) -> dict[str, int]:
    dist = {s: 0 for s in STATUS_ORDER}
    for log in logs:
        status = log["status"]
        if status in dist:
            dist[status] += 1
    return dist


def _compute_best_worst_prayer(breakdown: list[dict]) -> tuple[str | None, str | None]:
    if not breakdown:
        return None, None
    valid = [p for p in breakdown if p["avg_score"] > 0]
    if not valid:
        return None, None
    best = max(valid, key=lambda p: p["avg_score"])
    worst = min(valid, key=lambda p: p["avg_score"])
    return best["name"], worst["name"]


def _compute_fajr_rate(logs: list[dict]) -> float:
    fajr_logs = [l for l in logs if l["prayer_name"] == "fajr"]
    if not fajr_logs:
        return 0.0
    prayed = sum(1 for l in fajr_logs if l["status"] != "missed")
    return round(prayed / len(fajr_logs) * 100, 1)


def _compute_masjid_rate(logs: list[dict]) -> float:
    if not logs:
        return 0.0
    masjid = sum(1 for l in logs if l["status"] == "masjid")
    return round(masjid / len(logs) * 100, 1)


def _compute_consistency_score(logs: list[dict]) -> float:
    """Percentage of days where all 5 prayers were logged."""
    day_prayers: dict[date, set[str]] = defaultdict(set)
    for log in logs:
        d = _to_date(log["prayer_date"])
        day_prayers[d].add(log["prayer_name"])

    if not day_prayers:
        return 0.0
    complete = sum(1 for prayers in day_prayers.values() if len(prayers) >= 5)
    return round(complete / len(day_prayers) * 100, 1)


def _generate_insights(
    logs: list[dict],
    breakdown: list[dict],
    user: dict[str, Any],
) -> list[dict]:
    """Generate human-readable insights with icons."""
    insights: list[dict] = []
    if not logs:
        return [{"icon": "info", "text": "Start logging your prayers to see insights here."}]

    today = date.today()

    # --- Fajr weekday vs weekend analysis ---
    fajr_logs = [l for l in logs if l["prayer_name"] == "fajr"]
    if len(fajr_logs) >= 7:
        weekday_fajr = [l for l in fajr_logs if _to_date(l["prayer_date"]).weekday() < 5]
        weekend_fajr = [l for l in fajr_logs if _to_date(l["prayer_date"]).weekday() >= 5]

        wd_miss = sum(1 for l in weekday_fajr if l["status"] == "missed") if weekday_fajr else 0
        we_miss = sum(1 for l in weekend_fajr if l["status"] == "missed") if weekend_fajr else 0

        wd_rate = wd_miss / len(weekday_fajr) if weekday_fajr else 0
        we_rate = we_miss / len(weekend_fajr) if weekend_fajr else 0

        if wd_rate > we_rate + 0.15 and weekday_fajr:
            insights.append({
                "icon": "warning",
                "text": "You tend to miss Fajr more on weekdays. Try setting an earlier alarm.",
            })
        elif we_rate > wd_rate + 0.15 and weekend_fajr:
            insights.append({
                "icon": "warning",
                "text": "You tend to miss Fajr more on weekends. Keep a consistent sleep schedule.",
            })

    # --- Best day of the week ---
    day_scores: dict[int, list[int]] = defaultdict(list)
    for log in logs:
        d = _to_date(log["prayer_date"])
        day_scores[d.weekday()].append(log.get("score", 0) or 0)

    if day_scores:
        best_day_num = max(day_scores, key=lambda k: sum(day_scores[k]) / len(day_scores[k]))
        best_day_name = calendar.day_name[best_day_num]
        insights.append({
            "icon": "star",
            "text": f"Your best day is {best_day_name}. Keep it up!",
        })

    # --- Monthly improvement ---
    this_month_start = today.replace(day=1)
    last_month_start = (this_month_start - timedelta(days=1)).replace(day=1)

    this_month_logs = [l for l in logs if _to_date(l["prayer_date"]) >= this_month_start]
    last_month_logs = [
        l for l in logs
        if last_month_start <= _to_date(l["prayer_date"]) < this_month_start
    ]

    if this_month_logs and last_month_logs:
        this_avg = sum(l.get("score", 0) or 0 for l in this_month_logs) / len(this_month_logs)
        last_avg = sum(l.get("score", 0) or 0 for l in last_month_logs) / len(last_month_logs)

        if last_avg > 0:
            change = ((this_avg - last_avg) / last_avg) * 100
            if change > 5:
                insights.append({
                    "icon": "trending_up",
                    "text": f"You've improved {abs(change):.0f}% this month vs last month. Masha'Allah!",
                })
            elif change < -5:
                insights.append({
                    "icon": "trending_down",
                    "text": f"Your scores dropped {abs(change):.0f}% compared to last month. Stay consistent!",
                })



    # --- Masjid encouragement ---
    masjid_count = sum(1 for l in logs if l["status"] == "masjid")
    total = len(logs)
    if total > 0:
        masjid_pct = masjid_count / total * 100
        if masjid_pct >= 50:
            insights.append({
                "icon": "mosque",
                "text": f"Masha'Allah! You pray {masjid_pct:.0f}% of your prayers at the masjid.",
            })
        elif masjid_pct < 10 and total > 20:
            insights.append({
                "icon": "mosque",
                "text": "Try to pray more at the masjid. The reward is 27 times greater.",
            })

    # --- Best / worst prayer ---
    valid_breakdown = [p for p in breakdown if p["avg_score"] > 0]
    if len(valid_breakdown) >= 2:
        best = max(valid_breakdown, key=lambda p: p["avg_score"])
        worst = min(valid_breakdown, key=lambda p: p["avg_score"])
        if best["name"] != worst["name"]:
            insights.append({
                "icon": "chart",
                "text": (
                    f"{best['name']} is your strongest prayer (avg {best['avg_score']:.1f}). "
                    f"Focus on improving {worst['name']} (avg {worst['avg_score']:.1f})."
                ),
            })

    # --- Qaza awareness ---
    qaza_count = sum(1 for l in logs if l["status"] == "qaza")
    if qaza_count > 0 and total > 0:
        qaza_pct = qaza_count / total * 100
        if qaza_pct > 20:
            insights.append({
                "icon": "clock",
                "text": f"{qaza_pct:.0f}% of your prayers are Qaza. Set reminders to pray on time.",
            })

    if not insights:
        insights.append({
            "icon": "check",
            "text": "You're doing great! Keep up the good work.",
        })

    return insights


def _to_date(val: Any) -> date:
    """Convert a value to a date object."""
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, date):
        return val
    if isinstance(val, str):
        return date.fromisoformat(val)
    return val

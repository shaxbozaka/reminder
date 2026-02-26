"""Scoring system for prayer tracking."""

from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.prayer_log import SCORE_MAP, PrayerLog, PrayerStatus
from src.repositories.prayer_repo import PrayerRepository
from src.repositories.user_repo import UserRepository


STATUS_LABELS = {
    PrayerStatus.MASJID: "Masjid",
    PrayerStatus.IQAMA: "Iqama",
    PrayerStatus.ON_TIME: "On Time",
    PrayerStatus.LAST_MINUTES: "Last Min",
    PrayerStatus.QAZA: "Qaza",
    PrayerStatus.MISSED: "Missed",
    PrayerStatus.PENDING: "...",
}

STATUS_EMOJI = {
    PrayerStatus.MASJID: "\U0001f7e2",
    PrayerStatus.IQAMA: "\U0001f7e2",
    PrayerStatus.ON_TIME: "\U0001f7e1",
    PrayerStatus.LAST_MINUTES: "\U0001f7e0",
    PrayerStatus.QAZA: "\U0001f534",
    PrayerStatus.MISSED: "\u26ab",
    PrayerStatus.PENDING: "\u26aa",
}


class ScoringService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.prayer_repo = PrayerRepository(session)
        self.user_repo = UserRepository(session)

    async def record_prayer(
        self, telegram_id: int, log: PrayerLog, status: PrayerStatus
    ) -> int:
        """Record prayer status and return points earned."""
        await self.prayer_repo.update_status(log, status)
        points = SCORE_MAP[status]

        user = await self.user_repo.get_by_telegram_id(telegram_id)
        if user:
            await self.user_repo.update_score(user, points)
            streak = await self.prayer_repo.get_streak(telegram_id)
            await self.user_repo.update_streak(user, streak)

        await self.session.commit()
        return points

    async def get_daily_summary(self, telegram_id: int, day: date) -> dict:
        """Get summary for a specific day."""
        logs = await self.prayer_repo.get_today_logs(telegram_id, day)

        total_points = sum(log.score for log in logs)
        max_possible = 5 * 5  # 5 prayers * 5 max points
        logged_count = sum(
            1 for log in logs if log.status not in (PrayerStatus.PENDING, PrayerStatus.MISSED)
        )

        return {
            "date": day,
            "logs": logs,
            "total_points": total_points,
            "max_possible": max_possible,
            "logged_count": logged_count,
            "percentage": (total_points / max_possible * 100) if max_possible > 0 else 0,
        }

    async def get_weekly_summary(self, telegram_id: int, user_today: date | None = None) -> dict:
        """Get summary for the past 7 days with per-day breakdown."""
        today = user_today or date.today()
        week_ago = today - timedelta(days=6)
        logs = await self.prayer_repo.get_date_range_logs(telegram_id, week_ago, today)

        total_points = sum(log.score for log in logs)
        max_possible = 7 * 5 * 5

        status_counts = {}
        for log in logs:
            status_counts[log.status] = status_counts.get(log.status, 0) + 1

        # Per-day breakdown
        from src.models.prayer_log import PrayerName
        all_prayers = [PrayerName.FAJR, PrayerName.DHUHR, PrayerName.ASR, PrayerName.MAGHRIB, PrayerName.ISHA]
        days = []
        for i in range(7):
            d = week_ago + timedelta(days=i)
            day_logs = [l for l in logs if l.prayer_date == d]
            log_map = {l.prayer_name: l for l in day_logs}
            day_points = sum(l.score for l in day_logs if l.score)
            days.append({
                "date": d,
                "logs": log_map,
                "prayers": all_prayers,
                "points": day_points,
            })

        return {
            "start_date": week_ago,
            "end_date": today,
            "days": days,
            "total_points": total_points,
            "max_possible": max_possible,
            "percentage": (total_points / max_possible * 100) if max_possible > 0 else 0,
            "status_counts": status_counts,
            "total_prayers": len(logs),
        }

    def format_daily_summary(self, summary: dict, prayer_times=None, sunrise=None) -> str:
        """Format daily summary for display."""
        from src.models.prayer_log import PrayerName

        day_name = summary["date"].strftime("%A, %b %d")

        # Build log lookup by prayer name
        log_map = {}
        for log in summary["logs"]:
            log_map[log.prayer_name] = log

        # Build prayer time lookup
        time_map = {}
        if prayer_times:
            for pt in prayer_times:
                time_map[pt.name] = pt.time.strftime("%H:%M")

        rows = []
        all_prayers = [PrayerName.FAJR, PrayerName.DHUHR, PrayerName.ASR, PrayerName.MAGHRIB, PrayerName.ISHA]
        for prayer in all_prayers:
            name = prayer.value.capitalize()
            time_str = time_map.get(prayer, "     ")
            log = log_map.get(prayer)

            if log and log.status not in (PrayerStatus.PENDING,):
                emoji = STATUS_EMOJI.get(log.status, "\u26aa")
                label = STATUS_LABELS.get(log.status, "")
                score = f"+{log.score}" if log.score > 0 else ""
                rows.append(f"{emoji} {name:<8s}{time_str}  {label} {score}".rstrip())
            elif log and log.status == PrayerStatus.PENDING:
                rows.append(f"\u26aa {name:<8s}{time_str}")
            else:
                rows.append(f"\u26aa {name:<8s}{time_str}")

            if prayer == PrayerName.FAJR and sunrise:
                rows.append(f"\u2600\ufe0f {'Sunrise':<8s}{sunrise.strftime('%H:%M')}")

        points = summary["total_points"]
        logged = summary["logged_count"]

        code_lines = "\n".join(f"<code>{r}</code>" for r in rows)
        return f"{day_name}\n\n{code_lines}\n\n{points} points \u2022 {logged}/5 logged"

    def format_weekly_summary(self, summary: dict) -> str:
        """Format weekly summary with per-day prayer grid."""
        points = summary["total_points"]
        max_pts = summary["max_possible"]
        pct = summary["percentage"]

        # Status to single-char indicator
        STATUS_CHAR = {
            PrayerStatus.MASJID: "\U0001f7e2",
            PrayerStatus.IQAMA: "\U0001f7e2",
            PrayerStatus.ON_TIME: "\U0001f7e1",
            PrayerStatus.LAST_MINUTES: "\U0001f7e0",
            PrayerStatus.QAZA: "\U0001f534",
            PrayerStatus.MISSED: "\u26ab",
            PrayerStatus.PENDING: "\u26aa",
        }

        rows = []
        # Header
        rows.append("          F  D  A  M  I  pts")

        for day in summary["days"]:
            d = day["date"]
            day_label = d.strftime("%a %d")
            log_map = day["logs"]

            icons = []
            for prayer in day["prayers"]:
                log = log_map.get(prayer)
                if log:
                    icons.append(STATUS_CHAR.get(log.status, "\u26aa"))
                else:
                    icons.append("\u00b7")

            pts = day["points"]
            pts_str = str(pts) if pts > 0 else "\u00b7"
            row = f"{day_label}  {'  '.join(icons)}  {pts_str}"
            rows.append(row)

        rows.append("")
        rows.append(f"{points}/{max_pts} points ({pct:.0f}%)")

        code_lines = "\n".join(f"<code>{r}</code>" for r in rows)
        return code_lines

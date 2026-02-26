"""Prayer time calculation using our precise Meeus-based algorithm."""

import logging
import math
from datetime import date, datetime, timedelta, time as dt_time
from typing import NamedTuple
from zoneinfo import ZoneInfo

from src.models.prayer_log import PrayerName

logger = logging.getLogger(__name__)


class PrayerTime(NamedTuple):
    name: PrayerName
    time: datetime


# ═══════════════════════════════════════════════════════════════════════
# Precise solar position (Jean Meeus, "Astronomical Algorithms" 2nd ed.)
# ═══════════════════════════════════════════════════════════════════════

_R = math.pi / 180.0


def _sin(d: float) -> float:
    return math.sin(d * _R)


def _cos(d: float) -> float:
    return math.cos(d * _R)


def _tan(d: float) -> float:
    return math.tan(d * _R)


def _asin(x: float) -> float:
    return math.asin(max(-1.0, min(1.0, x))) / _R


def _acos(x: float) -> float:
    return math.acos(max(-1.0, min(1.0, x))) / _R


def _acot(x: float) -> float:
    return math.atan(1.0 / x) / _R


def _fix360(x: float) -> float:
    return x % 360.0


def _jd(year: int, month: int, day: int) -> float:
    """Calendar date to Julian Day Number (Meeus Ch.7)."""
    if month <= 2:
        year -= 1
        month += 12
    A = year // 100
    B = 2 - A + A // 4
    return (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day + B - 1524.5
    )


def _sun(jd: float) -> tuple[float, float]:
    """
    Solar declination (deg) and equation of time (min).

    Meeus Ch.25 with nutation correction (Ch.22).
    """
    T = (jd - 2451545.0) / 36525.0
    T2, T3 = T * T, T * T * T

    L0 = _fix360(280.46646 + 36000.76983 * T + 0.0003032 * T2)
    M = _fix360(357.52911 + 35999.05029 * T - 0.0001537 * T2)
    e = 0.016708634 - 0.000042037 * T - 0.0000001267 * T2

    C = (
        (1.914602 - 0.004817 * T - 0.000014 * T2) * _sin(M)
        + (0.019993 - 0.000101 * T) * _sin(2 * M)
        + 0.000289 * _sin(3 * M)
    )

    theta = L0 + C
    omega = 125.04 - 1934.136 * T
    lam = theta - 0.00569 - 0.00478 * _sin(omega)

    eps0 = (
        23.0
        + (26.0 + (21.448 - 46.8150 * T - 0.00059 * T2 + 0.001813 * T3) / 60.0)
        / 60.0
    )
    eps = eps0 + 0.00256 * _cos(omega)

    decl = _asin(_sin(eps) * _sin(lam))

    y = _tan(eps / 2.0) ** 2
    eot = (
        y * _sin(2 * L0)
        - 2 * e * _sin(M)
        + 4 * e * y * _sin(M) * _cos(2 * L0)
        - 0.5 * y * y * _sin(4 * L0)
        - 1.25 * e * e * _sin(2 * M)
    )
    eot = eot / _R * 4.0

    return decl, eot


def _ha(lat: float, decl: float, angle: float) -> float | None:
    """Hour angle (hours) for sun at given altitude angle."""
    cos_h = (_sin(angle) - _sin(lat) * _sin(decl)) / (_cos(lat) * _cos(decl))
    if cos_h > 1.0 or cos_h < -1.0:
        return None
    return _acos(cos_h) / 15.0


# ═══════════════════════════════════════════════════════════════════════
# Prayer time computation
# ═══════════════════════════════════════════════════════════════════════

# Fajr/Isha sun angles per calculation method
_METHODS: dict[str, tuple[float, float | None]] = {
    "muslim_world_league": (18.0, 17.0),
    "isna": (15.0, 15.0),
    "egyptian": (19.5, 17.5),
    "karachi": (18.0, 18.0),
    "umm_al_qura": (18.5, None),  # isha = maghrib + 90 min
    "tehran": (17.7, 14.0),
    "jafari": (16.0, 14.0),
}


def _compute(
    latitude: float,
    longitude: float,
    date_: date,
    utc_offset: float,
    fajr_ang: float,
    isha_ang: float | None,
    asr_factor: int,
    elevation: float,
) -> dict[str, float]:
    """
    Two-pass prayer time calculation (decimal hours, local time).

    Pass 1: approximate using noon solar position.
    Pass 2: refine using solar position at each prayer's approximate time.
    """
    jd_noon = _jd(date_.year, date_.month, date_.day)

    sun_alt = -0.8333
    if elevation > 0:
        sun_alt -= 0.0347 * math.sqrt(elevation)

    # ── Pass 1 ───────────────────────────────────────────────────────
    decl0, eot0 = _sun(jd_noon)
    noon_h = 12.0 + utc_offset - longitude / 15.0 - eot0 / 60.0

    def approx(angle: float, before: bool) -> float:
        h = _ha(latitude, decl0, angle)
        if h is None:
            h = 12.0 if before else 0.0
        return noon_h - h if before else noon_h + h

    fajr_h = approx(-fajr_ang, True)
    rise_h = approx(sun_alt, True)
    asr_alt = _acot(asr_factor + _tan(abs(latitude - decl0)))
    asr_h = approx(asr_alt, False)
    mag_h = approx(sun_alt, False)
    isha_h = approx(-isha_ang, False) if isha_ang else mag_h + 1.5

    # ── Pass 2 ───────────────────────────────────────────────────────
    def refine(h_approx: float, angle: float, before: bool) -> float:
        frac = (h_approx - 12.0 - utc_offset) / 24.0
        decl, eot = _sun(jd_noon + frac)
        transit = 12.0 + utc_offset - longitude / 15.0 - eot / 60.0
        h = _ha(latitude, decl, angle)
        if h is None:
            return h_approx
        return transit - h if before else transit + h

    fajr_h = refine(fajr_h, -fajr_ang, True)
    rise_h = refine(rise_h, sun_alt, True)

    # Asr: re-derive altitude with refined declination
    frac = (asr_h - 12.0 - utc_offset) / 24.0
    decl_a, _ = _sun(jd_noon + frac)
    asr_alt2 = _acot(asr_factor + _tan(abs(latitude - decl_a)))
    asr_h = refine(asr_h, asr_alt2, False)

    mag_h = refine(mag_h, sun_alt, False)

    # Dhuhr: refined transit
    _, eot_n = _sun(jd_noon)
    dhuhr_h = 12.0 + utc_offset - longitude / 15.0 - eot_n / 60.0

    if isha_ang is not None:
        isha_h = refine(isha_h, -isha_ang, False)
    else:
        isha_h = mag_h + 1.5

    return {
        "fajr": fajr_h,
        "sunrise": rise_h,
        "dhuhr": dhuhr_h,
        "asr": asr_h,
        "maghrib": mag_h,
        "isha": isha_h,
    }


def _hours_to_dt(
    h: float, d: date, tz: ZoneInfo, *, round_up: bool
) -> datetime:
    """
    Decimal hours → tz-aware datetime, with precautionary rounding.

    round_up=False: floor to minute (Fajr, Sunrise — earlier = safer)
    round_up=True:  ceil  to minute (Dhuhr..Isha  — later  = safer)
    """
    h = h % 24.0
    total_sec = h * 3600
    if round_up:
        total_sec = math.ceil(total_sec / 60) * 60
    else:
        total_sec = math.floor(total_sec / 60) * 60
    total_sec = int(total_sec)
    hh = total_sec // 3600
    mm = (total_sec % 3600) // 60
    if hh >= 24:
        hh -= 24
    return datetime.combine(d, dt_time(hh, mm, 0), tzinfo=tz)


# ═══════════════════════════════════════════════════════════════════════
# Public API  (drop-in replacement)
# ═══════════════════════════════════════════════════════════════════════

CALC_METHODS = {k: k for k in _METHODS}   # keep name→name for compatibility

ASR_METHODS = {
    "hanafi": 2,
    "shafi": 1,
}


def get_prayer_times(
    latitude: float,
    longitude: float,
    date_: date,
    timezone: str,
    calc_method: str = "muslim_world_league",
    madhab: str = "hanafi",
) -> list[PrayerTime]:
    """Calculate all 5 prayer times for a given location and date."""
    tz = ZoneInfo(timezone)
    utc_off = (
        datetime.combine(date_, dt_time(12, 0), tzinfo=tz)
        .utcoffset()
        .total_seconds()
        / 3600.0
    )
    fajr_ang, isha_ang = _METHODS.get(calc_method, _METHODS["muslim_world_league"])
    asr_factor = ASR_METHODS.get(madhab, 2)

    raw = _compute(latitude, longitude, date_, utc_off, fajr_ang, isha_ang, asr_factor, 0.0)

    return [
        PrayerTime(PrayerName.FAJR, _hours_to_dt(raw["fajr"], date_, tz, round_up=False)),
        PrayerTime(PrayerName.DHUHR, _hours_to_dt(raw["dhuhr"], date_, tz, round_up=True)),
        PrayerTime(PrayerName.ASR, _hours_to_dt(raw["asr"], date_, tz, round_up=True)),
        PrayerTime(PrayerName.MAGHRIB, _hours_to_dt(raw["maghrib"], date_, tz, round_up=True)),
        PrayerTime(PrayerName.ISHA, _hours_to_dt(raw["isha"], date_, tz, round_up=True)),
    ]


def get_sunrise_time(
    latitude: float,
    longitude: float,
    date_: date,
    timezone: str,
    calc_method: str = "muslim_world_league",
) -> datetime | None:
    """Get sunrise time for a given location and date."""
    tz = ZoneInfo(timezone)
    utc_off = (
        datetime.combine(date_, dt_time(12, 0), tzinfo=tz)
        .utcoffset()
        .total_seconds()
        / 3600.0
    )
    fajr_ang, isha_ang = _METHODS.get(calc_method, _METHODS["muslim_world_league"])

    raw = _compute(latitude, longitude, date_, utc_off, fajr_ang, isha_ang, 2, 0.0)
    return _hours_to_dt(raw["sunrise"], date_, tz, round_up=False)


def get_next_prayer(
    latitude: float,
    longitude: float,
    timezone: str,
    calc_method: str = "muslim_world_league",
    madhab: str = "hanafi",
) -> PrayerTime | None:
    """Get the next upcoming prayer."""
    tz = ZoneInfo(timezone)
    now = datetime.now(tz)
    today = now.date()

    times = get_prayer_times(latitude, longitude, today, timezone, calc_method, madhab)
    for pt in times:
        if pt.time > now:
            return pt

    tomorrow = today + timedelta(days=1)
    times = get_prayer_times(latitude, longitude, tomorrow, timezone, calc_method, madhab)
    if times:
        return times[0]
    return None


def format_prayer_times(times: list[PrayerTime], sunrise: datetime | None = None) -> str:
    """Format prayer times for display."""
    lines = []
    labels = {
        PrayerName.FAJR: "Fajr",
        PrayerName.DHUHR: "Dhuhr",
        PrayerName.ASR: "Asr",
        PrayerName.MAGHRIB: "Maghrib",
        PrayerName.ISHA: "Isha",
    }
    for pt in times:
        label = labels.get(pt.name, pt.name.value)
        lines.append(f"{label:<10s}{pt.time.strftime('%H:%M')}")
        if pt.name == PrayerName.FAJR and sunrise:
            lines.append(f"{'Sunrise':<10s}{sunrise.strftime('%H:%M')}")
    return "\n".join(lines)

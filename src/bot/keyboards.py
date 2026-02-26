"""Inline keyboards for prayer response buttons."""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from src.models.prayer_log import PrayerName, PrayerStatus


def prayer_response_keyboard(prayer_name: PrayerName, prayer_date: str) -> InlineKeyboardMarkup:
    """Create the prayer response keyboard with emoji buttons.

    Callback data format: prayer:{prayer_name}:{date}:{status}
    """
    prefix = f"prayer:{prayer_name.value}:{prayer_date}"

    keyboard = [
        [
            InlineKeyboardButton("🕌 Masjid", callback_data=f"{prefix}:{PrayerStatus.MASJID.value}"),
            InlineKeyboardButton("✨ Iqama", callback_data=f"{prefix}:{PrayerStatus.IQAMA.value}"),
        ],
        [
            InlineKeyboardButton("🟡 On Time", callback_data=f"{prefix}:{PrayerStatus.ON_TIME.value}"),
            InlineKeyboardButton("🟠 Last Min", callback_data=f"{prefix}:{PrayerStatus.LAST_MINUTES.value}"),
        ],
        [
            InlineKeyboardButton("🔴 Qaza", callback_data=f"{prefix}:{PrayerStatus.QAZA.value}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def location_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for requesting location."""
    keyboard = [
        [InlineKeyboardButton("Send Location", callback_data="setup:location")],
    ]
    return InlineKeyboardMarkup(keyboard)


def settings_keyboard() -> InlineKeyboardMarkup:
    """Settings menu keyboard."""
    keyboard = [
        [
            InlineKeyboardButton("Calculation Method", callback_data="settings:calc_method"),
            InlineKeyboardButton("Madhab", callback_data="settings:madhab"),
        ],
        [
            InlineKeyboardButton("Daily Quran", callback_data="settings:quran_toggle"),
        ],
        [
            InlineKeyboardButton("Notification Timing", callback_data="settings:notify_timing"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)




def calc_method_keyboard() -> InlineKeyboardMarkup:
    """Calculation method selection."""
    methods = [
        ("Uzbekistan (O'zMI)", "uzbekistan"),
        ("Muslim World League", "muslim_world_league"),
        ("Egyptian", "egyptian"),
        ("Karachi", "karachi"),
        ("Umm al-Qura", "umm_al_qura"),
        ("North America (ISNA)", "isna"),
        ("Tehran", "tehran"),
        ("Jafari", "jafari"),
    ]
    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"calc_method:{value}")]
        for name, value in methods
    ]
    return InlineKeyboardMarkup(keyboard)


def madhab_keyboard() -> InlineKeyboardMarkup:
    """Madhab selection."""
    keyboard = [
        [
            InlineKeyboardButton("Hanafi", callback_data="madhab:hanafi"),
            InlineKeyboardButton("Shafi'i", callback_data="madhab:shafi"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)



def notify_timing_keyboard() -> InlineKeyboardMarkup:
    """Notification timing selection."""
    options = [
        ("At adhan time", "0"),
        ("5 min before", "5"),
        ("10 min before", "10"),
        ("15 min before", "15"),
        ("30 min before", "30"),
    ]
    keyboard = [
        [InlineKeyboardButton(name, callback_data=f"notify_timing:{value}")]
        for name, value in options
    ]
    return InlineKeyboardMarkup(keyboard)

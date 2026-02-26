"""Motivation service - sends extra reminders when prayers are missed or qaza."""

import json
import random
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent.parent / "data"

_hadith_data: list[dict[str, Any]] | None = None


def _load_hadith() -> list[dict[str, Any]]:
    global _hadith_data
    if _hadith_data is None:
        path = DATA_DIR / "hadith_salah.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                _hadith_data = json.load(f)
        else:
            _hadith_data = []
    return _hadith_data


def get_motivation_message(is_qaza: bool = True) -> str:
    """Get a motivational message about salah from Hadith and Quran."""
    from src.services.quran import get_salah_motivation, format_motivation

    messages = []

    # Try Quran ayah about salah
    ayah = get_salah_motivation()
    if ayah:
        messages.append(format_motivation(ayah))

    # Try Hadith about salah
    hadith_collection = _load_hadith()
    if hadith_collection:
        hadith = random.choice(hadith_collection)
        hadith_text = hadith.get("text", "")
        hadith_source = hadith.get("source", "")
        narrator = hadith.get("narrator", "")

        parts = []
        if narrator:
            parts.append(f"Narrated by {narrator}:")
        if hadith_text:
            parts.append(hadith_text)
        if hadith_source:
            parts.append(f"\n- {hadith_source}")

        messages.append("\n".join(parts))

    if not messages:
        # Fallback messages if no data files exist yet
        messages = [_get_fallback_message()]

    separator = "\n\n" + "\u2500" * 30 + "\n\n"
    header = "A Gentle Reminder About Salah\n\n" if is_qaza else ""
    return header + separator.join(messages)


def _get_fallback_message() -> str:
    """Built-in motivational messages when data files aren't loaded."""
    fallbacks = [
        (
            "The Prophet (PBUH) said:\n"
            '"The first thing that the servant will be called to account for on the Day of '
            'Judgment will be the prayer. If it is good, his deeds will have been good. '
            'If it is bad, his deeds will have been bad."\n'
            "- At-Tabarani"
        ),
        (
            "Allah says in the Quran:\n"
            '"Indeed, prayer prohibits immorality and wrongdoing, and the remembrance of '
            'Allah is greater."\n'
            "- Surah Al-Ankabut [29:45]"
        ),
        (
            "The Prophet (PBUH) said:\n"
            '"Between a man and shirk (polytheism) and kufr (disbelief) is the '
            'abandonment of prayer."\n'
            "- Sahih Muslim"
        ),
        (
            "Allah says in the Quran:\n"
            '"Guard strictly your prayers, especially the middle prayer, and stand before '
            'Allah in devout obedience."\n'
            "- Surah Al-Baqarah [2:238]"
        ),
        (
            "The Prophet (PBUH) said:\n"
            '"If there was a river at the door of any one of you and he took a bath in it '
            "five times a day, would you notice any dirt on him?' They said, 'Not a trace "
            "of dirt would be left.' The Prophet said, 'That is the example of the five "
            "daily prayers with which Allah blots out evil deeds.'\"\n"
            "- Sahih Al-Bukhari & Muslim"
        ),
        (
            "The Prophet (PBUH) said:\n"
            '"The covenant between us and them is the prayer; whoever abandons it has '
            'committed kufr."\n'
            "- Ahmad, At-Tirmidhi, An-Nasa'i"
        ),
    ]
    return random.choice(fallbacks)

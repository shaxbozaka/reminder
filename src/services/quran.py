"""Quran surah and ayah service using offline JSON data."""

import json
import random
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).parent.parent.parent / "data"

_quran_data: list[dict[str, Any]] | None = None
_salah_ayahs: list[dict[str, Any]] | None = None


def _load_quran() -> list[dict[str, Any]]:
    global _quran_data
    if _quran_data is None:
        quran_path = DATA_DIR / "quran.json"
        if quran_path.exists():
            with open(quran_path, "r", encoding="utf-8") as f:
                _quran_data = json.load(f)
        else:
            _quran_data = []
    return _quran_data


def _load_salah_ayahs() -> list[dict[str, Any]]:
    global _salah_ayahs
    if _salah_ayahs is None:
        path = DATA_DIR / "salah_ayahs.json"
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                _salah_ayahs = json.load(f)
        else:
            _salah_ayahs = []
    return _salah_ayahs


def get_random_surah_excerpt() -> dict[str, Any] | None:
    """Get a random short surah or excerpt for daily delivery."""
    quran = _load_quran()
    if not quran:
        return None

    # Pick a random surah
    surah = random.choice(quran)
    ayahs = surah.get("ayahs", [])

    if not ayahs:
        return None

    # For long surahs, pick a random window of 3-5 ayahs
    if len(ayahs) > 5:
        window_size = min(random.randint(3, 5), len(ayahs))
        start_idx = random.randint(0, len(ayahs) - window_size)
        selected_ayahs = ayahs[start_idx : start_idx + window_size]
    else:
        selected_ayahs = ayahs

    return {
        "surah_number": surah["number"],
        "surah_name": surah["name"],
        "surah_name_en": surah.get("englishName", ""),
        "ayahs": selected_ayahs,
    }


def get_salah_motivation() -> dict[str, Any] | None:
    """Get a Quran ayah specifically about salah (for qaza motivation)."""
    ayahs = _load_salah_ayahs()
    if not ayahs:
        return None
    return random.choice(ayahs)


def format_quran_excerpt(excerpt: dict[str, Any]) -> str:
    """Format a Quran excerpt for Telegram message."""
    if not excerpt:
        return "Could not load Quran data."

    lines = [
        f"Surah {excerpt['surah_name']} ({excerpt['surah_name_en']})",
        "",
    ]

    for ayah in excerpt["ayahs"]:
        ayah_num = ayah.get("numberInSurah", "")
        arabic = ayah.get("arabic", "")
        translation = ayah.get("translation", ayah.get("text", ""))

        if arabic:
            lines.append(f"{arabic}")
        lines.append(f"[{excerpt['surah_number']}:{ayah_num}] {translation}")
        lines.append("")

    return "\n".join(lines)


def format_motivation(ayah: dict[str, Any]) -> str:
    """Format a motivational ayah/hadith."""
    if not ayah:
        return ""

    source = ayah.get("source", "")
    arabic = ayah.get("arabic", "")
    translation = ayah.get("translation", "")

    lines = []
    if arabic:
        lines.append(arabic)
        lines.append("")
    if translation:
        lines.append(translation)
    if source:
        lines.append(f"\n- {source}")

    return "\n".join(lines)

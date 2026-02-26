"""Download Quran data from the open-source quran.com API and save as local JSON.

Usage: python scripts/fetch_quran.py

This downloads all 114 surahs with Arabic text and English translation,
saving them to data/quran.json for offline use.
"""

import json
import sys
import urllib.request
import time
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "quran.json"

# Using alquran.cloud free API (no auth needed)
BASE_URL = "https://api.alquran.cloud/v1"


def fetch_surah(number: int) -> dict | None:
    """Fetch a single surah with Arabic and English translation."""
    try:
        # Get Arabic + English in one call using edition
        url = f"{BASE_URL}/surah/{number}/editions/quran-uthmani,en.sahih"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())

        if data["code"] != 200:
            print(f"  Error fetching surah {number}: {data.get('status')}")
            return None

        editions = data["data"]
        arabic_data = editions[0]
        english_data = editions[1]

        surah = {
            "number": arabic_data["number"],
            "name": arabic_data["name"],
            "englishName": arabic_data["englishName"],
            "englishNameTranslation": arabic_data["englishNameTranslation"],
            "revelationType": arabic_data["revelationType"],
            "numberOfAyahs": arabic_data["numberOfAyahs"],
            "ayahs": [],
        }

        for ar_ayah, en_ayah in zip(arabic_data["ayahs"], english_data["ayahs"]):
            surah["ayahs"].append({
                "number": ar_ayah["number"],
                "numberInSurah": ar_ayah["numberInSurah"],
                "arabic": ar_ayah["text"],
                "translation": en_ayah["text"],
                "juz": ar_ayah["juz"],
                "page": ar_ayah["page"],
            })

        return surah

    except Exception as e:
        print(f"  Error fetching surah {number}: {e}")
        return None


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # Check if already downloaded
    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE) as f:
            existing = json.load(f)
        if len(existing) == 114:
            print(f"Quran data already downloaded ({OUTPUT_FILE})")
            print("Delete the file to re-download.")
            return

    print("Downloading Quran data (114 surahs)...")
    print("Source: alquran.cloud (Arabic Uthmani + English Sahih International)")
    print()

    quran = []
    for i in range(1, 115):
        print(f"  Surah {i}/114 ...", end=" ", flush=True)
        surah = fetch_surah(i)
        if surah:
            quran.append(surah)
            print(f"{surah['englishName']} ({surah['numberOfAyahs']} ayahs)")
        else:
            print("FAILED")

        # Be polite to the API
        time.sleep(0.5)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(quran, f, ensure_ascii=False, indent=2)

    print(f"\nDone! Saved {len(quran)} surahs to {OUTPUT_FILE}")
    print(f"File size: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()

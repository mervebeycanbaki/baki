import csv
import os
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from statistics import fmean


DATA_DIR = Path("data/daily")
REPORT_DIR = Path("reports")
WINDOWS = (30, 90, 180, 365)
MIN_COVERAGE = 0.80
SIGNAL_MIN_PASSES = 3


def load_daily_data():
    """Load daily 24h rolling-volume observations grouped by CMC coin ID."""
    if not DATA_DIR.exists():
        raise RuntimeError("data/daily klasoru bulunamadi.")

    coins = defaultdict(dict)
    latest_day = None
    csv_files = sorted(DATA_DIR.glob("????-??-??.csv"))

    if not csv_files:
        raise RuntimeError("data/daily klasorunde tarihli CSV dosyasi bulunamadi.")

    for csv_path in csv_files:
        try:
            file_day = date.fromisoformat(csv_path.stem)
        except ValueError:
            continue

        with csv_path.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {"cmc_id", "name", "symbol", "volume_24h_usd"}
            if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
                raise RuntimeError(
                    f"{csv_path} dosyasinda gerekli sutunlar eksik. "
                    f"Gerekli sutunlar: {', '.join(sorted(required))}"
                )

            for row in reader:
                try:
                    cmc_id = int(row["cmc_id"])
                    volume = float(row["volume_24h_usd"])
                except (TypeError, ValueError):
                    continue

                if volume < 0:
                    continue

                timestamp = (row.get("timestamp_utc") or "").strip()
                observation_day = file_day
                if len(timestamp) >= 10:
                    try:
                        observation_day = date.fromisoformat(timestamp[:10])
                    except ValueError:
                        observation_day = file_day

                coins[cmc_id][observation_day] = {
                    "name": (row.get("name") or "").strip(),
                    "symbol": (row.get("symbol") or "").strip(),
                    "volume": volume,
                }

                if latest_day is None or observation_day > latest_day:
                    latest_day = observation_day

    if not coins or latest_day is None:
        raise RuntimeError("CSV dosyalarinda okunabilir hacim kaydi bulunamadi.")

    print(f"Yuklenen coin sayisi: {len(coins)}")
    print(f"En yeni veri tarihi (UTC): {latest_day.isoformat()}")
    return coins, latest_day


def average_for_window(history, latest_day: date, days: int):
    """Average the previous N calendar days, excluding the latest observation day."""
    start_day = latest_day - timedelta(days=days)
    values = [
        item["volume"]
        for day, item in history.items()
        if start_day <= day < latest_day
    ]

    minimum = max(1, int(days * MIN_COVERAGE))
    if len(values) < minimum:
        return None, len(values)

    return fmean(values), len(values)


def money(value):
    return f"${value:,.0f}"

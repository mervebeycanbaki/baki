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


def run_scan():
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    coins, latest_day = load_daily_data()

    results = []
    skipped = 0
    evaluated = 0

    for cmc_id, history in coins.items():
        latest = history.get(latest_day)
        if latest is None:
            continue

        evaluated += 1
        averages = {}
        counts = {}
        passed = 0
        ratios = []

        for window in WINDOWS:
            avg, count = average_for_window(history, latest_day, window)
            averages[window] = avg
            counts[window] = count

            if avg is not None and avg > 0:
                ratio = latest["volume"] / avg
                ratios.append(ratio)
                if latest["volume"] > avg:
                    passed += 1

        available_windows = sum(
            1 for value in averages.values() if value is not None
        )
        if available_windows < SIGNAL_MIN_PASSES:
            skipped += 1
            continue

        max_ratio = max(ratios) if ratios else 0.0
        results.append(
            {
                "date_utc": latest_day.isoformat(),
                "cmc_id": cmc_id,
                "name": latest["name"],
                "symbol": latest["symbol"],
                "volume_24h_usd": latest["volume"],
                "avg_30d_usd": averages[30],
                "avg_90d_usd": averages[90],
                "avg_180d_usd": averages[180],
                "avg_365d_usd": averages[365],
                "samples_30d": counts[30],
                "samples_90d": counts[90],
                "samples_180d": counts[180],
                "samples_365d": counts[365],
                "passed_windows": passed,
                "available_windows": available_windows,
                "max_volume_multiple": max_ratio,
                "signal": "VOLUME_SURGE" if passed >= SIGNAL_MIN_PASSES else "",
            }
        )

    results.sort(
        key=lambda row: (
            row["passed_windows"],
            row["max_volume_multiple"],
            row["volume_24h_usd"],
        ),
        reverse=True,
    )

    csv_path = REPORT_DIR / "volume_scan_latest.csv"
    fields = [
        "date_utc",
        "cmc_id",
        "name",
        "symbol",
        "volume_24h_usd",
        "avg_30d_usd",
        "avg_90d_usd",
        "avg_180d_usd",
        "avg_365d_usd",
        "samples_30d",
        "samples_90d",
        "samples_180d",
        "samples_365d",
        "passed_windows",
        "available_windows",
        "max_volume_multiple",
        "signal",
    ]

    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    signals = [row for row in results if row["signal"]]
    report_path = REPORT_DIR / "latest_report.txt"
    lines = [
        "CMC HACIM TARAMA RAPORU",
        f"Veri tarihi (UTC): {latest_day.isoformat()}",
        f"Taranan coin: {evaluated}",
        f"Yeterli gecmisi olmayan / degerlendirilemeyen coin: {skipped}",
        f"Degerlendirilen coin: {len(results)}",
        f"Sinyal: {len(signals)}",
        "",
        "Kural: Son 24 saatlik hacim, mevcut 30/90/180/365 gunluk ortalamalarin en az 3'unden buyuk olmali.",
        "730 gunluk ortalama: Yeterli veri birikene kadar devre disi.",
        "Not: Hacim verisi UTC gununun ilk aninda olculen 24 saatlik kayan hacimdir; mumun gunluk hacmi degildir.",
        "",
    ]

    if signals:
        lines.append("SINYAL LISTESI")
        for row in signals[:100]:
            lines.append(
                f'{row["symbol"]} | {row["name"]} | '
                f'Sinyal: {row["passed_windows"]}/{row["available_windows"]} | '
                f'Son hacim: {money(row["volume_24h_usd"])} | '
                f'En yuksek oran: {row["max_volume_multiple"]:.2f}x'
            )
    else:
        lines.append("Bu veri tarihinde kurala uyan coin bulunamadi.")

    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines[:12]))
    print(f"\nCSV raporu: {csv_path}")
    print(f"Metin raporu: {report_path}")


if __name__ == "__main__":
    run_scan()

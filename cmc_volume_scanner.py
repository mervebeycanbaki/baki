        if start_day < day < latest_day
    ]
    minimum = max(1, int(days * MIN_COVERAGE))
    if len(values) < minimum:
        return None, len(values)
    return fmean(values), len(values)


def money(value):
    return f"${value:,.0f}"


def run_scan():
    os.makedirs(REPORT_DIR, exist_ok=True)
    coins, latest_day = load_daily_data()

    results = []
    skipped = 0

    for cmc_id, history in coins.items():
        latest = history.get(latest_day)
        if latest is None:
            continue

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

        available_windows = sum(1 for value in averages.values() if value is not None)
        if available_windows < SIGNAL_MIN_PASSES:
            skipped += 1
            continue

        max_ratio = max(ratios) if ratios else 0.0
        results.append({
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
        })

    results.sort(
        key=lambda row: (row["passed_windows"], row["max_volume_multiple"], row["volume_24h_usd"]),
        reverse=True,
    )

    csv_path = os.path.join(REPORT_DIR, "volume_scan_latest.csv")
    fields = [
        "date_utc", "cmc_id", "name", "symbol", "volume_24h_usd",
        "avg_30d_usd", "avg_90d_usd", "avg_180d_usd", "avg_365d_usd",
        "samples_30d", "samples_90d", "samples_180d", "samples_365d",
        "passed_windows", "available_windows", "max_volume_multiple", "signal",
    ]
    with open(csv_path, "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    signals = [row for row in results if row["signal"]]
    report_path = os.path.join(REPORT_DIR, "latest_report.txt")
    lines = [
        "CMC HACIM TARAMA RAPORU",
        f"Veri tarihi (UTC): {latest_day.isoformat()}",
        f"Taranan coin: {sum(1 for h in coins.values() if latest_day in h)}",
        f"Yeterli gecmisi olmayan / degerlendirilemeyen coin: {skipped}",
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

    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")

    print("\n".join(lines[:12]))
    print(f"\nCSV raporu: {csv_path}")
    print(f"Metin raporu: {report_path}")


if __name__ == "__main__":
    run_scan()

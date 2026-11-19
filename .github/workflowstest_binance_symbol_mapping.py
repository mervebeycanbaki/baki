#!/usr/bin/env python3
"""Map CoinMarketCap daily snapshot symbols to Binance active USDT spot pairs.

This is a conservative preliminary mapping by ticker only. It deliberately
marks duplicate CMC symbols as ambiguous and does not claim token identity.
"""

import csv
import json
import os
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path("data/daily")
REPORT_DIR = Path("reports")
API_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"


def get_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CoinSinyal-RSI-Mapping/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            payload = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Binance HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Binance baglanti hatasi: {exc.reason}") from exc

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Binance yaniti gecerli JSON degil.") from exc

    if not isinstance(data, dict) or "symbols" not in data:
        raise RuntimeError(f"Binance yaniti beklenen formatta degil: {str(data)[:500]}")
    return data


def load_latest_cmc_snapshot() -> tuple[list[dict], Path]:
    csv_files = sorted(DATA_DIR.glob("????-??-??.csv"))
    if not csv_files:
        raise RuntimeError("data/daily klasorunde YYYY-MM-DD.csv dosyasi bulunamadi.")

    latest_file = csv_files[-1]
    with latest_file.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"cmc_id", "name", "symbol"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise RuntimeError(
                f"{latest_file} icinde cmc_id, name ve symbol sutunlari bulunamadi."
            )

        coins = []
        for row in reader:
            cmc_id = (row.get("cmc_id") or "").strip()
            name = (row.get("name") or "").strip()
            symbol = (row.get("symbol") or "").strip().upper()
            if cmc_id and symbol:
                coins.append(
                    {"cmc_id": cmc_id, "name": name, "cmc_symbol": symbol}
                )

    if not coins:
        raise RuntimeError(f"{latest_file} dosyasinda coin kaydi bulunamadi.")
    return coins, latest_file


def main() -> None:
    coins, latest_file = load_latest_cmc_snapshot()
    print(f"CMC veri dosyasi: {latest_file}")
    print(f"CMC coin kaydi: {len(coins)}")
    print("Binance aktif USDT spot pariteleri aliniyor...")

    exchange = get_json(API_URL)
    binance_by_base: dict[str, list[str]] = defaultdict(list)

    for item in exchange.get("symbols", []):
        if not isinstance(item, dict):
            continue
        if (
            item.get("status") == "TRADING"
            and item.get("quoteAsset") == "USDT"
            and item.get("isSpotTradingAllowed") is True
        ):
            base = str(item.get("baseAsset") or "").strip().upper()
            pair = str(item.get("symbol") or "").strip().upper()
            if base and pair:
                binance_by_base[base].append(pair)

    cmc_symbol_counts = Counter(coin["cmc_symbol"] for coin in coins)
    rows = []
    counts = Counter()

    for coin in coins:
        symbol = coin["cmc_symbol"]
        candidates = sorted(set(binance_by_base.get(symbol, [])))

        if cmc_symbol_counts[symbol] > 1:
            status = "AMBIGUOUS_CMC_SYMBOL"
            pair = ""
        elif len(candidates) == 1:
            status = "SYMBOL_MATCH_REVIEW_IDENTITY"
            pair = candidates[0]
        elif not candidates:
            status = "NO_BINANCE_USDT_SPOT_PAIR"
            pair = ""
        else:
            status = "MULTIPLE_BINANCE_USDT_PAIRS"
            pair = ""

        counts[status] += 1
        rows.append(
            {
                "cmc_id": coin["cmc_id"],
                "name": coin["name"],
                "cmc_symbol": symbol,
                "binance_symbol": pair,
                "status": status,
            }
        )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output_csv = REPORT_DIR / "binance_symbol_mapping.csv"
    fields = ["cmc_id", "name", "cmc_symbol", "binance_symbol", "status"]
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    matched = [row for row in rows if row["status"] == "SYMBOL_MATCH_REVIEW_IDENTITY"]
    ambiguous = (
        counts["AMBIGUOUS_CMC_SYMBOL"]
        + counts["MULTIPLE_BINANCE_USDT_PAIRS"]
    )
    no_pair = counts["NO_BINANCE_USDT_SPOT_PAIR"]

    print("\nBINANCE SEMBOL ESLESTIRME SONUCU")
    print(f"CMC coin kaydi: {len(coins)}")
    print(f"Tekil USDT sembol eslesmesi (kimlik dogrulamasi gerekir): {len(matched)}")
    print(f"USDT spot paritesi bulunamayan: {no_pair}")
    print(f"Belirsiz eslesme: {ambiguous}")
    print(f"CSV raporu: {output_csv}")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write("# Binance coin sembol eslestirme testi\n\n")
            summary.write(f"- CMC veri dosyasi: `{latest_file.name}`\n")
            summary.write(f"- CMC coin kaydi: **{len(coins)}**\n")
            summary.write(
                f"- Tekil USDT sembol eslesmesi (kimlik dogrulamasi gerekir): **{len(matched)}**\n"
            )
            summary.write(f"- USDT spot paritesi bulunamayan: **{no_pair}**\n")
            summary.write(f"- Belirsiz eslesme: **{ambiguous}**\n\n")
            summary.write(
                "> Not: Ayni ticker ayni varlik oldugunu kanitlamaz. Eslesen coinler RSI taramasina alinmadan once kimlikleri dogrulanmalidir.\n\n"
            )
            summary.write("### Ilk 30 sembol eslesmesi\n\n")
            summary.write("| CMC sembolu | Coin | Binance paritesi |\n")
            summary.write("|---|---|---|\n")
            for row in matched[:30]:
                safe_name = row["name"].replace("|", "/")
                summary.write(
                    f'| `{row["cmc_symbol"]}` | {safe_name} | `{row["binance_symbol"]}` |\n'
                )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # Make workflow logs actionable.
        print(f"HATA: {exc}")
        raise SystemExit(1)

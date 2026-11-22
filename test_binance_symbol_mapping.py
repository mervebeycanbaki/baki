#!/usr/bin/env python3
"""Map CMC coin tickers to active Binance USDT spot markets.

This is a preliminary ticker-based mapping only. Duplicate or ambiguous
symbols are explicitly skipped; a ticker match is not proof of token identity.
Uses Python standard library only (no requests package or API credentials).
"""

from __future__ import annotations

import csv
import json
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path("data/daily")
REPORT_DIR = Path("reports")
OUTPUT_CSV = REPORT_DIR / "binance_symbol_mapping.csv"
SUMMARY_TXT = REPORT_DIR / "binance_symbol_mapping_summary.txt"
EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"


def fetch_exchange_info() -> dict:
    request = urllib.request.Request(
        EXCHANGE_INFO_URL,
        headers={"User-Agent": "CoinSinyal-Binance-Mapping/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Binance HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Binance baglanti hatasi: {exc.reason}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError("Binance yaniti gecerli JSON degil.") from exc

    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise RuntimeError(
            f"Binance yaniti beklenen formatta degil: {str(payload)[:500]}"
        )
    return payload


def load_latest_cmc_snapshot() -> tuple[list[dict[str, str]], Path]:
    files = sorted(DATA_DIR.glob("????-??-??.csv"))
    if not files:
        raise RuntimeError(
            "data/daily klasorunde YYYY-MM-DD.csv dosyasi bulunamadi."
        )

    latest_file = files[-1]
    with latest_file.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"cmc_id", "name", "symbol"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise RuntimeError(
                f"{latest_file} dosyasinda cmc_id, name, symbol sutunlari bulunamadi."
            )

        coins: list[dict[str, str]] = []
        for row in reader:
            cmc_id = (row.get("cmc_id") or "").strip()
            name = (row.get("name") or "").strip()
            symbol = (row.get("symbol") or "").strip().upper()
            if cmc_id and symbol:
                coins.append(
                    {"cmc_id": cmc_id, "name": name, "cmc_symbol": symbol}
                )

    if not coins:
        raise RuntimeError(f"{latest_file} dosyasinda gecerli coin kaydi bulunamadi.")
    return coins, latest_file


def build_binance_symbol_map(exchange_info: dict) -> dict[str, list[str]]:
    pairs_by_base: dict[str, list[str]] = defaultdict(list)

    for item in exchange_info["symbols"]:
        if not isinstance(item, dict):
            continue

        status = item.get("status")
        quote_asset = str(item.get("quoteAsset") or "").upper()
        base_asset = str(item.get("baseAsset") or "").strip().upper()
        pair = str(item.get("symbol") or "").strip().upper()
        spot_allowed = item.get("isSpotTradingAllowed")
        permissions = item.get("permissions") or []
        permission_sets = item.get("permissionSets") or []
        spot_permission = (
            "SPOT" in permissions
            or any(
                isinstance(group, list) and "SPOT" in group
                for group in permission_sets
            )
        )

        if (
            status == "TRADING"
            and quote_asset == "USDT"
            and (spot_allowed is True or spot_permission)
            and base_asset
            and pair
        ):
            pairs_by_base[base_asset].append(pair)

    return {base: sorted(set(pairs)) for base, pairs in pairs_by_base.items()}


def write_reports(rows: list[dict[str, str]], latest_file: Path) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "cmc_id",
        "name",
        "cmc_symbol",
        "binance_symbol",
        "status",
    ]

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = Counter(row["status"] for row in rows)
    matched = [row for row in rows if row["status"] == "MATCHED"]
    ambiguous = counts["AMBIGUOUS_CMC_SYMBOL"] + counts["MULTIPLE_BINANCE_USDT_PAIRS"]
    summary_lines = [
        "BINANCE COIN ESLESTIRME RAPORU",
        f"CMC veri dosyasi: {latest_file.name}",
        f"Toplam CMC coin kaydi: {len(rows)}",
        f"Tekil Binance USDT spot paritesi bulunan: {len(matched)}",
        f"Binance USDT paritesi bulunamayan: {counts['NO_BINANCE_USDT_PAIR']}",
        f"Belirsiz eslesme nedeniyle atlanan: {ambiguous}",
        "",
        "Ilk 30 eslesen coin:",
    ]
    for row in matched[:30]:
        summary_lines.append(
            f"{row['cmc_symbol']} | {row['name']} | {row['binance_symbol']}"
        )

    SUMMARY_TXT.write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    if not OUTPUT_CSV.is_file() or OUTPUT_CSV.stat().st_size == 0:
        raise RuntimeError(f"Eslesme CSV dosyasi olusturulamadi: {OUTPUT_CSV}")
    if not SUMMARY_TXT.is_file() or SUMMARY_TXT.stat().st_size == 0:
        raise RuntimeError(f"Ozet raporu olusturulamadi: {SUMMARY_TXT}")

    print("\n".join(summary_lines))
    print(f"CSV hazir: {OUTPUT_CSV}")
    print(f"Ozet hazir: {SUMMARY_TXT}")


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    coins, latest_file = load_latest_cmc_snapshot()
    print(f"CMC veri dosyasi: {latest_file}")
    print(f"CMC coin kaydi: {len(coins)}")
    print("Binance aktif USDT spot pariteleri aliniyor...")

    exchange_info = fetch_exchange_info()
    pairs_by_base = build_binance_symbol_map(exchange_info)
    cmc_symbol_counts = Counter(coin["cmc_symbol"] for coin in coins)

    rows: list[dict[str, str]] = []
    for coin in coins:
        ticker = coin["cmc_symbol"]
        candidates = pairs_by_base.get(ticker, [])

        if cmc_symbol_counts[ticker] > 1:
            status, pair = "AMBIGUOUS_CMC_SYMBOL", ""
        elif len(candidates) == 1:
            status, pair = "MATCHED", candidates[0]
        elif len(candidates) == 0:
            status, pair = "NO_BINANCE_USDT_PAIR", ""
        else:
            status, pair = "MULTIPLE_BINANCE_USDT_PAIRS", ""

        rows.append(
            {
                "cmc_id": coin["cmc_id"],
                "name": coin["name"],
                "cmc_symbol": ticker,
                "binance_symbol": pair,
                "status": status,
            }
        )

    write_reports(rows, latest_file)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"HATA: {exc}")
        raise SystemExit(1)

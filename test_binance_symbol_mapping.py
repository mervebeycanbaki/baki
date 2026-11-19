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

#!/usr/bin/env python3
"""Map CMC symbols to active Binance USDT spot pairs.

This is a preliminary ticker mapping, not a guaranteed token-identity match.
Duplicate CMC tickers and non-unique exchange matches are marked ambiguous.
"""
from __future__ import annotations

import csv
import json
import os
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

DATA_DIR = Path("data/daily")
REPORT_DIR = Path("reports")
OUTPUT_CSV = REPORT_DIR / "binance_symbol_mapping.csv"
SUMMARY_FILE = REPORT_DIR / "binance_symbol_mapping_summary.txt"
BINANCE_EXCHANGE_INFO_URL = "https://data-api.binance.vision/api/v3/exchangeInfo"


def get_json(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"User-Agent": "CoinSinyal-Binance-Mapping/1.0"}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Binance HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Binance baglanti hatasi: {exc.reason}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Binance yaniti gecerli JSON degil.") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("symbols"), list):
        raise RuntimeError(f"Binance yaniti beklenen formatta degil: {str(payload)[:500]}")
    return payload


def load_latest_cmc_snapshot() -> tuple[list[dict], Path]:
    files = sorted(DATA_DIR.glob("????-??-??.csv"))
    if not files:
        raise RuntimeError("data/daily klasorunde YYYY-MM-DD.csv dosyasi bulunamadi.")
    latest_file = files[-1]
    with latest_file.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"cmc_id", "name", "symbol"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise RuntimeError(
                f"{latest_file} dosyasinda cmc_id, name, symbol sutunlari bulunamadi."
            )
        coins = []
        for row in reader:
            cmc_id = (row.get("cmc_id") or "").strip()
            name = (row.get("name") or "").strip()
            symbol = (row.get("symbol") or "").strip().upper()
            if cmc_id and symbol:
                coins.append({"cmc_id": cmc_id, "name": name, "cmc_symbol": symbol})
    if not coins:
        raise RuntimeError(f"{latest_file} dosyasinda gecerli coin kaydi bulunamadi.")
    return coins, latest_file


def build_binance_symbol_map(exchange_info: dict) -> dict[str, list[str]]:
    pairs_by_base: dict[str, list[str]] = defaultdict(list)
    for item in exchange_info["symbols"]:
        if not isinstance(item, dict):
            continue
        permissions = item.get("permissions") or []
        permission_sets = item.get("permissionSets") or []
        has_spot_permission = (
            "SPOT" in permissions
            or any("SPOT" in group for group in permission_sets if isinstance(group, list))
        )
        if (
            item.get("status") == "TRADING"
            and str(item.get("quoteAsset") or "").upper() == "USDT"
            and (item.get("isSpotTradingAllowed") is True or has_spot_permission)
        ):
            base = str(item.get("baseAsset") or "").strip().upper()
            pair = str(item.get("symbol") or "").strip().upper()
            if base and pair:
                pairs_by_base[base].append(pair)
    return {base: sorted(set(pairs)) for base, pairs in pairs_by_base.items()}


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    coins, latest_file = load_latest_cmc_snapshot()
    print(f"CMC veri dosyasi: {latest_file}")
    print(f"CMC coin kaydi: {len(coins)}")

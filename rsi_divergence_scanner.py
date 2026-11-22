#!/usr/bin/env python3
"""Coin Sinyal RSI divergence scanner using Binance public spot klines.

No Binance API key or account credentials are required.
Only fully closed candles are used. Signals use confirmed price pivots
(three candles on the left and three closed candles on the right).
CMC ticker-to-Binance base-asset matching is preliminary, not proof of
project identity; the report explicitly flags this limitation.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path("data/daily")
REPORT_DIR = Path("reports")
CSV_REPORT = REPORT_DIR / "rsi_divergence_latest.csv"
TEXT_REPORT = REPORT_DIR / "rsi_divergence_latest.txt"
MAPPING_REPORT = REPORT_DIR / "rsi_binance_mapping_latest.csv"
SEEN_STATE = REPORT_DIR / "rsi_divergence_seen.json"

BASE_URL = "https://data-api.binance.vision"
EXCHANGE_INFO_URL = f"{BASE_URL}/api/v3/exchangeInfo"
SERVER_TIME_URL = f"{BASE_URL}/api/v3/time"
KLINES_URL = f"{BASE_URL}/api/v3/klines"

TIMEFRAMES = {
    "4h": "4 saat",
    "1d": "1 gun",
    "1w": "1 hafta",
}

RSI_PERIOD = 14
PIVOT_LEFT = 3
PIVOT_RIGHT = 3
KLINE_LIMIT = 250
MIN_PIVOT_GAP = 4
MAX_PIVOT_GAP = 40
MIN_PRICE_DIFFERENCE_PCT = 0.10
MIN_RSI_DIFFERENCE = 1.5
MAX_PIVOT_AGE_BARS = PIVOT_RIGHT + 1
REQUEST_PAUSE_SECONDS = 0.16
MAX_RETRIES = 5
SEEN_RETENTION_SECONDS = 180 * 24 * 60 * 60

CSV_FIELDS = [
    "cmc_id",
    "coin",
    "cmc_symbol",
    "binance_symbol",
    "timeframe",
    "divergence",
    "signal",
    "new_this_scan",
    "pivot_1_utc",
    "pivot_2_utc",
    "price_1",
    "price_2",
    "price_change_pct",
    "rsi_1",
    "rsi_2",
    "rsi_change",
    "pivot_age_bars",
]


def utc_string(milliseconds: int) -> str:
    return datetime.fromtimestamp(
        milliseconds / 1000, tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")


def http_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "CoinSinyal-RSI-Scanner/1.0"},
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                body = response.read().decode("utf-8")
            return json.loads(body)

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
            if exc.code in (418, 429):
                retry_after = exc.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else min(30 * attempt, 180)
                except (TypeError, ValueError):
                    delay = min(30 * attempt, 180)
                if attempt < MAX_RETRIES:
                    print(f"Binance HTTP {exc.code}; {delay:.0f} sn bekleniyor.")
                    time.sleep(delay)
                    continue
            elif 500 <= exc.code < 600 and attempt < MAX_RETRIES:
                time.sleep(min(2 * attempt, 10))
                continue
            raise RuntimeError(f"Binance HTTP {exc.code}: {detail}") from exc

        except urllib.error.URLError as exc:
            if attempt < MAX_RETRIES:
                time.sleep(min(2 * attempt, 10))
                continue
            raise RuntimeError(f"Binance baglanti hatasi: {exc.reason}") from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError("Binance yaniti gecerli JSON degil.") from exc

    raise RuntimeError("Binance istegi tekrar denemelerinden sonra basarisiz.")


def load_latest_cmc_coins() -> tuple[list[dict[str, str]], Path]:
    files = sorted(DATA_DIR.glob("????-??-??.csv"))
    if not files:
        raise RuntimeError("data/daily klasorunde tarihli CMC CSV dosyasi bulunamadi.")

    latest_file = files[-1]
    with latest_file.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        required = {"cmc_id", "name", "symbol"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise RuntimeError(
                f"{latest_file} dosyasinda cmc_id, name ve symbol sutunlari gerekli."
            )
        coins = []
        for row in reader:
            cmc_id = (row.get("cmc_id") or "").strip()
            name = (row.get("name") or "").strip()
            symbol = (row.get("symbol") or "").strip().upper()
            if cmc_id and symbol:
                coins.append({"cmc_id": cmc_id, "name": name, "cmc_symbol": symbol})

    if not coins:
        raise RuntimeError(f"{latest_file} dosyasinda coin kaydi bulunamadi.")
    return coins, latest_file


def build_binance_candidates(coins: list[dict[str, str]]) -> tuple[list[dict], dict[str, int]]:
    exchange = http_json(EXCHANGE_INFO_URL)
    if not isinstance(exchange, dict) or not isinstance(exchange.get("symbols"), list):
        raise RuntimeError("Binance exchangeInfo yaniti beklenen formatta degil.")

    pairs_by_base: dict[str, list[str]] = defaultdict(list)
    for item in exchange["symbols"]:
        if not isinstance(item, dict):
            continue
        permissions = item.get("permissions") or []
        permission_sets = item.get("permissionSets") or []
        has_spot_permission = (
            "SPOT" in permissions
            or any(
                "SPOT" in group
                for group in permission_sets
                if isinstance(group, list)
            )
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

    ticker_counts = Counter(coin["cmc_symbol"] for coin in coins)
    candidates: list[dict] = []
    counts: Counter = Counter()
    mapping_rows = []

    for coin in coins:
        ticker = coin["cmc_symbol"]
        pairs = sorted(set(pairs_by_base.get(ticker, [])))
        if ticker_counts[ticker] > 1:
            status, pair = "AMBIGUOUS_CMC_SYMBOL", ""
        elif len(pairs) == 1:
            status, pair = "MATCHED_TICKER_CANDIDATE", pairs[0]
            candidates.append({**coin, "binance_symbol": pair})
        elif len(pairs) == 0:
            status, pair = "NO_ACTIVE_USDT_SPOT_PAIR", ""
        else:
            status, pair = "MULTIPLE_BINANCE_PAIRS", ""
        counts[status] += 1
        mapping_rows.append({
            "cmc_id": coin["cmc_id"],
            "name": coin["name"],
            "cmc_symbol": ticker,
            "binance_symbol": pair,
            "status": status,
        })

    return candidates, dict(counts), mapping_rows


def fetch_closed_klines(symbol: str, interval: str, server_time_ms: int) -> list[dict]:
    query = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": interval,
        "limit": KLINE_LIMIT,
    })
    payload = http_json(f"{KLINES_URL}?{query}")
    if not isinstance(payload, list):
        raise RuntimeError(f"{symbol} {interval}: kline yaniti liste degil: {str(payload)[:250]}")

    candles = []
    for row in payload:
        if not isinstance(row, list) or len(row) < 7:
            continue
        try:
            open_time = int(row[0])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            close_time = int(row[6])
        except (TypeError, ValueError, IndexError):
            continue
        # Binance kline close time is inclusive; accept only if it is in the past.
        if close_time >= server_time_ms:
            continue
        if min(high, low, close) <= 0:
            continue
        candles.append({
            "open_time": open_time,
            "close_time": close_time,
            "high": high,
            "low": low,
            "close": close,
        })
    return candles


def calculate_rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder RSI, aligned to closes; the first period values are None."""
    result: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return result

    gains = []
    losses = []
    for index in range(1, period + 1):
        change = closes[index] - closes[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    def to_rsi(gain: float, loss: float) -> float:
        if loss == 0:
            return 100.0 if gain > 0 else 50.0
        rs = gain / loss
        return 100.0 - 100.0 / (1.0 + rs)

    result[period] = to_rsi(avg_gain, avg_loss)
    for index in range(period + 1, len(closes)):
        change = closes[index] - closes[index - 1]
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period
        result[index] = to_rsi(avg_gain, avg_loss)
    return result


def find_pivots(values: list[float], side: str) -> list[int]:
    pivots = []
    stop = len(values) - PIVOT_RIGHT
    for index in range(PIVOT_LEFT, stop):
        current = values[index]
        left = values[index - PIVOT_LEFT:index]
        right = values[index + 1:index + PIVOT_RIGHT + 1]
        if side == "low":
            if current < min(left) and current <= min(right):
                pivots.append(index)
        elif side == "high":
            if current > max(left) and current >= max(right):
                pivots.append(index)
        else:
            raise ValueError("side must be low or high")
    return pivots


def classify_divergence(
    side: str,
    price_1: float,
    price_2: float,
    rsi_1: float,
    rsi_2: float,
) -> tuple[str | None, float, float]:
    price_change_pct = (price_2 - price_1) / price_1 * 100.0
    rsi_change = rsi_2 - rsi_1
    divergence = None

    if side == "low":
        if price_change_pct <= -MIN_PRICE_DIFFERENCE_PCT and rsi_change >= MIN_RSI_DIFFERENCE:
            divergence = "REGULAR_BULLISH"
        elif price_change_pct >= MIN_PRICE_DIFFERENCE_PCT and rsi_change <= -MIN_RSI_DIFFERENCE:
            divergence = "HIDDEN_BULLISH"
    else:
        if price_change_pct >= MIN_PRICE_DIFFERENCE_PCT and rsi_change <= -MIN_RSI_DIFFERENCE:
            divergence = "REGULAR_BEARISH"
        elif price_change_pct <= -MIN_PRICE_DIFFERENCE_PCT and rsi_change >= MIN_RSI_DIFFERENCE:
            divergence = "HIDDEN_BEARISH"

    return divergence, price_change_pct, rsi_change


def detect_divergences(candles: list[dict], coin: dict, interval: str) -> list[dict]:
    if len(candles) < RSI_PERIOD + PIVOT_LEFT + PIVOT_RIGHT + MIN_PIVOT_GAP + 2:
        return []

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    rsi = calculate_rsi(closes)
    results = []

    for side, price_series in (("low", lows), ("high", highs)):
        pivots = find_pivots(price_series, side)
        if len(pivots) < 2:
            continue

        latest = pivots[-1]
        age = len(candles) - 1 - latest
        # The pivot has already been confirmed by PIVOT_RIGHT closed candles.
        # Keep a small grace window in case a scheduled run starts late.
        if age > MAX_PIVOT_AGE_BARS:
            continue

        current_rsi = rsi[latest]
        if current_rsi is None:
            continue

        # Compare the newest confirmed pivot with recent prior pivots;
        # use the nearest qualifying comparison only.
        for previous in reversed(pivots[:-1]):
            gap = latest - previous
            if gap > MAX_PIVOT_GAP:
                break
            if gap < MIN_PIVOT_GAP:
                continue
            previous_rsi = rsi[previous]
            if previous_rsi is None:
                continue

            divergence, price_pct, rsi_delta = classify_divergence(
                side,
                price_series[previous],
                price_series[latest],
                previous_rsi,
                current_rsi,
            )
            if divergence:
                results.append({
                    "cmc_id": coin["cmc_id"],
                    "coin": coin["name"],
                    "cmc_symbol": coin["cmc_symbol"],
                    "binance_symbol": coin["binance_symbol"],
                    "timeframe": interval,
                    "divergence": divergence,
                    "signal": "BULLISH" if divergence.endswith("BULLISH") else "BEARISH",
                    "new_this_scan": "",  # Filled by deduplication state.
                    "pivot_1_utc": utc_string(candles[previous]["open_time"]),
                    "pivot_2_utc": utc_string(candles[latest]["open_time"]),
                    "pivot_2_open_ms": candles[latest]["open_time"],
                    "price_1": f"{price_series[previous]:.12g}",
                    "price_2": f"{price_series[latest]:.12g}",
                    "price_change_pct": f"{price_pct:.3f}",
                    "rsi_1": f"{previous_rsi:.2f}",
                    "rsi_2": f"{current_rsi:.2f}",
                    "rsi_change": f"{rsi_delta:.2f}",
                    "pivot_age_bars": str(age),
                })
                break

    return results


def signal_key(row: dict) -> str:
    return "|".join([
        str(row["cmc_id"]),
        row["binance_symbol"],
        row["timeframe"],
        row["divergence"],
        str(row["pivot_2_open_ms"]),
    ])


def load_seen_state() -> dict[str, int]:
    if not SEEN_STATE.exists():
        return {}
    try:
        payload = json.loads(SEEN_STATE.read_text(encoding="utf-8"))
        seen = payload.get("seen", {}) if isinstance(payload, dict) else {}
        return {
            str(key): int(value)
            for key, value in seen.items()
            if isinstance(value, (int, float))
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        print("UYARI: RSI sinyal gecmisi okunamadi; yeni sinyaller yeniden isaretlenebilir.")
        return {}


def save_seen_state(seen: dict[str, int], now_seconds: int) -> None:
    cutoff = now_seconds - SEEN_RETENTION_SECONDS
    pruned = {key: value for key, value in seen.items() if value >= cutoff}
    SEEN_STATE.write_text(
        json.dumps({"version": 1, "seen": pruned}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def mark_new_signals(signals: list[dict]) -> tuple[int, int]:
    now_seconds = int(time.time())
    seen = load_seen_state()
    new_count = 0
    for row in signals:
        key = signal_key(row)
        if key not in seen:
            row["new_this_scan"] = "YES"
            seen[key] = now_seconds
            new_count += 1
        else:
            row["new_this_scan"] = "NO"
    save_seen_state(seen, now_seconds)
    return new_count, len(seen)


def write_reports(
    signals: list[dict],
    coins_total: int,
    candidates_total: int,
    mapping_counts: dict[str, int],
    error_rows: list[str],
    source_file: Path,
    requests_done: int,
) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    with CSV_REPORT.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in signals:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})

    new_signals = [row for row in signals if row["new_this_scan"] == "YES"]
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "COIN SINYAL - RSI UYUMSUZLUK RAPORU",
        f"Tarama zamani: {generated_at}",
        f"CMC kaynak dosyasi: {source_file.name}",
        f"Toplam CMC coin: {coins_total}",
        f"Tekil Binance USDT sembol adayi: {candidates_total}",
        f"Tamamlanan coin/zaman dilimi istegi: {requests_done}",
        "Zaman dilimleri: 4 saat (4h), 1 gun (1d), 1 hafta (1w)",
        f"RSI: Wilder {RSI_PERIOD}",
        f"Pivot: {PIVOT_LEFT} sol / {PIVOT_RIGHT} sag kapanmis mum",
        "Yalnizca kapanmis Binance mumlari kullanildi.",
        "Sinyal, pivotun sagindaki mumlar kapandiktan sonra dogrulanir.",
        "CMC sembol - Binance base asset eslesmesi aday eslesmedir; proje kimligini garanti etmez.",
        "RSI uyumsuzlugu tek basina AL/SAT emri veya garanti degildir.",
        "",
        "ESLESTIRME OZETI",
    ]
    for status, count in sorted(mapping_counts.items()):
        lines.append(f"{status}: {count}")
    lines.extend([
        "",
        f"Guncel onayli uyumsuzluk: {len(signals)}",
        f"Bu taramada yeni tespit: {len(new_signals)}",
        f"Veri hatasi / atlanan istek: {len(error_rows)}",
        "",
        "SINYALLER (en fazla 150)",
    ])

    if not signals:
        lines.append("Bu taramada esiklere uyan guncel ve onayli uyumsuzluk bulunamadi.")
    else:
        ordered = sorted(
            signals,
            key=lambda row: (
                {"4h": 0, "1d": 1, "1w": 2}.get(row["timeframe"], 9),
                row["divergence"],
                row["coin"],
            ),
        )
        for row in ordered[:150]:
            tag = "YENI" if row["new_this_scan"] == "YES" else "Daha once bildirildi"
            lines.append(
                f'{tag} | {row["cmc_symbol"]} ({row["binance_symbol"]}) | '
                f'{TIMEFRAMES[row["timeframe"]]} | {row["divergence"]} | '
                f'Fiyat: {row["price_1"]} -> {row["price_2"]} '
                f'({row["price_change_pct"]}%) | '
                f'RSI: {row["rsi_1"]} -> {row["rsi_2"]} | '
                f'Son pivot: {row["pivot_2_utc"]}'
            )

    if error_rows:
        lines.extend(["", "ILK 30 VERI HATASI"])
        lines.extend(error_rows[:30])
        if len(error_rows) > 30:
            lines.append(f"... ve {len(error_rows) - 30} ek hata")

    TEXT_REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")

    for output in (CSV_REPORT, TEXT_REPORT, MAPPING_REPORT, SEEN_STATE):
        if not output.is_file() or output.stat().st_size == 0:
            raise RuntimeError(f"Rapor veya durum dosyasi olusturulamadi: {output}")


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    coins, source_file = load_latest_cmc_coins()
    candidates, mapping_counts, mapping_rows = build_binance_candidates(coins)

    print(f"CMC kaynak dosyasi: {source_file}")
    print(f"CMC coin kaydi: {len(coins)}")
    print(f"Binance USDT aday coin: {len(candidates)}")
    print("Binance eslestirme ozeti:")
    for status, count in sorted(mapping_counts.items()):
        print(f"  {status}: {count}")

    # Persist full mapping for later review.
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with MAPPING_REPORT.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["cmc_id", "name", "cmc_symbol", "binance_symbol", "status"],
        )
        writer.writeheader()
        writer.writerows(mapping_rows)

    server_info = http_json(SERVER_TIME_URL)
    server_time_ms = int(server_info["serverTime"])

    signals: list[dict] = []
    errors: list[str] = []
    requests_done = 0
    total_requests = len(candidates) * len(TIMEFRAMES)

    for coin in candidates:
        symbol = coin["binance_symbol"]
        for interval, label in TIMEFRAMES.items():
            requests_done += 1
            print(
                f"[{requests_done}/{total_requests}] "
                f'{coin["cmc_symbol"]} {symbol} {label}'
            )
            try:
                candles = fetch_closed_klines(symbol, interval, server_time_ms)
                signals.extend(detect_divergences(candles, coin, interval))
            except Exception as exc:
                message = f'{coin["cmc_symbol"]} {symbol} {interval}: {exc}'
                errors.append(message)
                print(f"  VERI HATASI: {exc}")
            time.sleep(REQUEST_PAUSE_SECONDS)

    # If every kline request failed, do not publish an apparently valid empty scan.
    successful_requests = total_requests - len(errors)
    if total_requests > 0 and successful_requests == 0:
        raise RuntimeError("Tum Binance mum istekleri basarisiz; rapor yayinlanmadi.")

    new_count, seen_count = mark_new_signals(signals)
    write_reports(
        signals=signals,
        coins_total=len(coins),
        candidates_total=len(candidates),
        mapping_counts=mapping_counts,
        error_rows=errors,
        source_file=source_file,
        requests_done=successful_requests,
    )

    print("\nRSI TARAMA SONUCU")
    print(f"Tarama adayi: {len(candidates)}")
    print(f"Basarili mum istegi: {successful_requests}/{total_requests}")
    print(f"Uyumsuzluk sinyali: {len(signals)}")
    print(f"Yeni sinyal: {new_count}")
    print(f"Veri hatasi: {len(errors)}")
    print(f"Kayitli sinyal anahtari: {seen_count}")
    print(f"Metin raporu: {TEXT_REPORT}")
    print(f"CSV raporu: {CSV_REPORT}")

    if errors:
        print("UYARI: Bazi istekler basarisiz; rapordaki veri hatasi sayisini kontrol edin.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"HATA: {exc}")
        raise SystemExit(1)

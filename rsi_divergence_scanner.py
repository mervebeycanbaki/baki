
#!/usr/bin/env python3
# COIN SINYAL - RSI DIVERGENCE SCANNER
# Binance Public API - API key not required.
# Uses closed candles only and confirmed price pivots.

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


# ============================================================
# 1. FILES AND SETTINGS
# ============================================================

DATA_DIR = Path("data/daily")
REPORT_DIR = Path("reports")

CSV_REPORT = REPORT_DIR / "rsi_divergence_latest.csv"
TEXT_REPORT = REPORT_DIR / "rsi_divergence_latest.txt"
MATRIX_REPORT = REPORT_DIR / "rsi_divergence_matrix_latest.csv"
MAPPING_REPORT = REPORT_DIR / "rsi_binance_mapping_latest.csv"
SEEN_STATE = REPORT_DIR / "rsi_divergence_seen.json"

BASE_URL = "https://data-api.binance.vision"
EXCHANGE_INFO_URL = f"{BASE_URL}/api/v3/exchangeInfo"
SERVER_TIME_URL = f"{BASE_URL}/api/v3/time"
KLINES_URL = f"{BASE_URL}/api/v3/klines"

TIMEFRAMES = {
    "4h": "4 Saat",
    "1d": "1 Gun",
    "1w": "1 Hafta",
}

RSI_PERIOD = 14

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# Only a pivot confirmed exactly three closed candles ago
# is included in the current signal matrix.
MAX_PIVOT_AGE_BARS = PIVOT_RIGHT

MIN_PIVOT_GAP = 4
MAX_PIVOT_GAP = 40

MIN_PRICE_DIFFERENCE_PCT = 0.10
MIN_RSI_DIFFERENCE = 1.5

KLINE_LIMIT = 250
REQUEST_PAUSE_SECONDS = 0.16
MAX_RETRIES = 5

SEEN_RETENTION_SECONDS = 180 * 24 * 60 * 60


# ============================================================
# 2. REPORT COLUMNS
# ============================================================

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
    "confirmation_utc",
    "pivot_2_open_ms",
    "price_1",
    "price_2",
    "price_change_pct",
    "rsi_1",
    "rsi_2",
    "rsi_change",
    "pivot_age_bars",
]

MATRIX_FIELDS = [
    "cmc_id",
    "coin",
    "cmc_symbol",
    "binance_symbol",
    "4h_status",
    "4h_types",
    "1d_status",
    "1d_types",
    "1w_status",
    "1w_types",
    "bullish_timeframes",
    "bearish_timeframes",
    "alignment",
    "signal_count",
    "new_signal_count",
]

DIVERGENCE_NAMES = {
    "REGULAR_BULLISH": "Normal Pozitif",
    "REGULAR_BEARISH": "Normal Negatif",
    "HIDDEN_BULLISH": "Gizli Pozitif",
    "HIDDEN_BEARISH": "Gizli Negatif",
}


# ============================================================
# 3. GENERAL UTILITIES
# ============================================================

def utc_string(milliseconds: int) -> str:
    return datetime.fromtimestamp(
        milliseconds / 1000,
        tz=timezone.utc,
    ).strftime("%Y-%m-%d %H:%M UTC")


def http_json(url: str):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "CoinSinyal-RSI-Scanner/2.0"
        },
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(
                request,
                timeout=35,
            ) as response:
                body = response.read().decode("utf-8")

            return json.loads(body)

        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(
                "utf-8",
                errors="replace",
            )[:400]

            if exc.code in (418, 429) and attempt < MAX_RETRIES:
                retry_after = exc.headers.get("Retry-After")

                try:
                    delay = (
                        float(retry_after)
                        if retry_after
                        else min(30 * attempt, 180)
                    )
                except (TypeError, ValueError):
                    delay = min(30 * attempt, 180)

                print(
                    f"Binance HTTP {exc.code}; "
                    f"{delay:.0f} saniye bekleniyor."
                )
                time.sleep(delay)
                continue

            if 500 <= exc.code < 600 and attempt < MAX_RETRIES:
                time.sleep(min(2 * attempt, 10))
                continue

            raise RuntimeError(
                f"Binance HTTP {exc.code}: {detail}"
            ) from exc

        except urllib.error.URLError as exc:
            if attempt < MAX_RETRIES:
                time.sleep(min(2 * attempt, 10))
                continue

            raise RuntimeError(
                f"Binance baglanti hatasi: {exc.reason}"
            ) from exc

        except json.JSONDecodeError as exc:
            raise RuntimeError(
                "Binance yaniti gecerli JSON degil."
            ) from exc

    raise RuntimeError(
        "Binance istegi tekrar denemelerinden sonra basarisiz."
    )


# ============================================================
# 4. LOAD COINMARKETCAP COINS
# ============================================================

def load_latest_cmc_coins():
    files = sorted(
        DATA_DIR.glob("????-??-??.csv")
    )

    if not files:
        raise RuntimeError(
            "data/daily klasorunde tarihli CMC CSV bulunamadi."
        )

    latest_file = files[-1]

    with latest_file.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:

        reader = csv.DictReader(handle)

        required = {
            "cmc_id",
            "name",
            "symbol",
        }

        if (
            not reader.fieldnames
            or not required.issubset(set(reader.fieldnames))
        ):
            raise RuntimeError(
                "CMC CSV dosyasinda cmc_id, name ve symbol gerekli."
            )

        coins = []

        for row in reader:
            cmc_id = (row.get("cmc_id") or "").strip()
            name = (row.get("name") or "").strip()
            symbol = (row.get("symbol") or "").strip().upper()

            if cmc_id and symbol:
                coins.append({
                    "cmc_id": cmc_id,
                    "name": name,
                    "cmc_symbol": symbol,
                })

    if not coins:
        raise RuntimeError(
            "CMC CSV dosyasinda coin kaydi bulunamadi."
        )

    return coins, latest_file


# ============================================================
# 5. MATCH BINANCE USDT SPOT PAIRS
# ============================================================

def build_binance_candidates(coins):

    exchange = http_json(EXCHANGE_INFO_URL)

    if (
        not isinstance(exchange, dict)
        or not isinstance(exchange.get("symbols"), list)
    ):
        raise RuntimeError(
            "Binance exchangeInfo yaniti beklenen formatta degil."
        )

    pairs_by_base = defaultdict(list)

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
            and (
                item.get("isSpotTradingAllowed") is True
                or has_spot_permission
            )
        ):
            base = str(
                item.get("baseAsset") or ""
            ).strip().upper()

            pair = str(
                item.get("symbol") or ""
            ).strip().upper()

            if base and pair:
                pairs_by_base[base].append(pair)

    ticker_counts = Counter(
        coin["cmc_symbol"] for coin in coins
    )

    candidates = []
    counts = Counter()
    mapping_rows = []

    for coin in coins:

        ticker = coin["cmc_symbol"]
        pairs = sorted(
            set(pairs_by_base.get(ticker, []))
        )

        if ticker_counts[ticker] > 1:
            status, pair = "AMBIGUOUS_CMC_SYMBOL", ""

        elif len(pairs) == 1:
            status, pair = "MATCHED_TICKER_CANDIDATE", pairs[0]

            candidates.append({
                **coin,
                "binance_symbol": pair,
            })

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


# ============================================================
# 6. FETCH ONLY CLOSED BINANCE CANDLES
# ============================================================

def fetch_closed_klines(symbol, interval, server_time_ms):

    query = urllib.parse.urlencode({
        "symbol": symbol,
        "interval": interval,
        "limit": KLINE_LIMIT,
    })

    payload = http_json(
        f"{KLINES_URL}?{query}"
    )

    if not isinstance(payload, list):
        raise RuntimeError(
            f"{symbol} {interval}: Kline yaniti liste degil."
        )

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

        # Current/open candle is excluded.
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


# ============================================================
# 7. WILDER RSI 14
# ============================================================

def calculate_rsi(closes, period=RSI_PERIOD):

    result = [None] * len(closes)

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

    def to_rsi(gain, loss):

        if loss == 0:
            return 100.0 if gain > 0 else 50.0

        rs = gain / loss

        return 100.0 - (100.0 / (1.0 + rs))

    result[period] = to_rsi(
        avg_gain,
        avg_loss,
    )

    for index in range(period + 1, len(closes)):

        change = closes[index] - closes[index - 1]

        gain = max(change, 0.0)
        loss = max(-change, 0.0)

        avg_gain = (
            avg_gain * (period - 1) + gain
        ) / period

        avg_loss = (
            avg_loss * (period - 1) + loss
        ) / period

        result[index] = to_rsi(
            avg_gain,
            avg_loss,
        )

    return result


# ============================================================
# 8. PRICE PIVOTS
# ============================================================

def find_pivots(values, side):

    pivots = []

    stop = len(values) - PIVOT_RIGHT

    for index in range(PIVOT_LEFT, stop):

        current = values[index]

        left = values[
            index - PIVOT_LEFT:index
        ]

        right = values[
            index + 1:index + PIVOT_RIGHT + 1
        ]

        if side == "low":

            if (
                current < min(left)
                and current <= min(right)
            ):
                pivots.append(index)

        elif side == "high":

            if (
                current > max(left)
                and current >= max(right)
            ):
                pivots.append(index)

        else:
            raise ValueError(
                "side must be low or high"
            )

    return pivots


# ============================================================
# 9. DIVERGENCE CLASSIFICATION
# ============================================================

def classify_divergence(
    side,
    price_1,
    price_2,
    rsi_1,
    rsi_2,
):

    if price_1 <= 0:
        return None, 0.0, 0.0

    price_change_pct = (
        (price_2 - price_1) / price_1
    ) * 100.0

    rsi_change = rsi_2 - rsi_1

    divergence = None

    if side == "low":

        # Regular bullish:
        # Price lower low, RSI higher low.
        if (
            price_change_pct <= -MIN_PRICE_DIFFERENCE_PCT
            and rsi_change >= MIN_RSI_DIFFERENCE
        ):
            divergence = "REGULAR_BULLISH"

        # Hidden bullish:
        # Price higher low, RSI lower low.
        elif (
            price_change_pct >= MIN_PRICE_DIFFERENCE_PCT
            and rsi_change <= -MIN_RSI_DIFFERENCE
        ):
            divergence = "HIDDEN_BULLISH"

    else:

        # Regular bearish:
        # Price higher high, RSI lower high.
        if (
            price_change_pct >= MIN_PRICE_DIFFERENCE_PCT
            and rsi_change <= -MIN_RSI_DIFFERENCE
        ):
            divergence = "REGULAR_BEARISH"

        # Hidden bearish:
        # Price lower high, RSI higher high.
        elif (
            price_change_pct <= -MIN_PRICE_DIFFERENCE_PCT
            and rsi_change >= MIN_RSI_DIFFERENCE
        ):
            divergence = "HIDDEN_BEARISH"

    return divergence, price_change_pct, rsi_change


# ============================================================
# 10. DETECT ONLY CURRENTLY CONFIRMED DIVERGENCES
# ============================================================

def detect_divergences(candles, coin, interval):

    minimum_candles = (
        RSI_PERIOD
        + PIVOT_LEFT
        + PIVOT_RIGHT
        + MIN_PIVOT_GAP
        + 2
    )

    if len(candles) < minimum_candles:
        return []

    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]

    rsi = calculate_rsi(closes)

    results = []

    for side, price_series in (
        ("low", lows),
        ("high", highs),
    ):

        pivots = find_pivots(
            price_series,
            side,
        )

        if len(pivots) < 2:
            continue

        latest = pivots[-1]

        age = len(candles) - 1 - latest

        # The pivot must have exactly three closed candles
        # confirming it. This prevents old weekly signals
        # from being reported as current.
        if age != MAX_PIVOT_AGE_BARS:
            continue

        current_rsi = rsi[latest]

        if current_rsi is None:
            continue

        for previous in reversed(pivots[:-1]):

            gap = latest - previous

            if gap > MAX_PIVOT_GAP:
                break

            if gap < MIN_PIVOT_GAP:
                continue

            previous_rsi = rsi[previous]

            if previous_rsi is None:
                continue

            divergence, price_pct, rsi_delta = (
                classify_divergence(
                    side,
                    price_series[previous],
                    price_series[latest],
                    previous_rsi,
                    current_rsi,
                )
            )

            if not divergence:
                continue

            confirmation_index = latest + PIVOT_RIGHT

            confirmation_time = candles[
                confirmation_index
            ]["close_time"]

            results.append({
                "cmc_id": coin["cmc_id"],
                "coin": coin["name"],
                "cmc_symbol": coin["cmc_symbol"],
                "binance_symbol": coin["binance_symbol"],
                "timeframe": interval,
                "divergence": divergence,
                "signal": (
                    "BULLISH"
                    if divergence.endswith("BULLISH")
                    else "BEARISH"
                ),
                "new_this_scan": "",
                "pivot_1_utc": utc_string(
                    candles[previous]["open_time"]
                ),
                "pivot_2_utc": utc_string(
                    candles[latest]["open_time"]
                ),
                "confirmation_utc": utc_string(
                    confirmation_time
                ),
                "pivot_2_open_ms": candles[
                    latest
                ]["open_time"],
                "price_1": f"{price_series[previous]:.12g}",
                "price_2": f"{price_series[latest]:.12g}",
                "price_change_pct": f"{price_pct:.3f}",
                "rsi_1": f"{previous_rsi:.2f}",
                "rsi_2": f"{current_rsi:.2f}",
                "rsi_change": f"{rsi_delta:.2f}",
                "pivot_age_bars": str(age),
            })

            # Use the nearest qualifying previous pivot.
            break

    return results


# ============================================================
# 11. SIGNAL HISTORY / DUPLICATE CONTROL
# ============================================================

def signal_key(row):

    return "|".join([
        str(row["cmc_id"]),
        row["binance_symbol"],
        row["timeframe"],
        row["divergence"],
        str(row["pivot_2_open_ms"]),
    ])


def load_seen_state():

    if not SEEN_STATE.exists():
        return {}

    try:

        payload = json.loads(
            SEEN_STATE.read_text(
                encoding="utf-8"
            )
        )

        seen = (
            payload.get("seen", {})
            if isinstance(payload, dict)
            else {}
        )

        return {
            str(key): int(value)
            for key, value in seen.items()
            if isinstance(value, (int, float))
        }

    except (
        OSError,
        json.JSONDecodeError,
        TypeError,
        ValueError,
    ):

        print(
            "UYARI: RSI sinyal gecmisi okunamadi. "
            "Bazi eski sinyaller yeniden isaretlenebilir."
        )

        return {}


def save_seen_state(seen, now_seconds):

    cutoff = (
        now_seconds - SEEN_RETENTION_SECONDS
    )

    pruned = {
        key: value
        for key, value in seen.items()
        if value >= cutoff
    }

    SEEN_STATE.write_text(
        json.dumps(
            {
                "version": 2,
                "seen": pruned,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def mark_new_signals(signals):

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

    save_seen_state(
        seen,
        now_seconds,
    )

    return new_count, len(seen)


# ============================================================
# 12. BUILD 4H / 1D / 1W MATRIX
# ============================================================

def build_matrix_rows(candidates, signals):

    signal_groups = defaultdict(list)

    for row in signals:

        signal_groups[
            (
                row["cmc_id"],
                row["timeframe"],
            )
        ].append(row)

    matrix_rows = []

    for coin in candidates:

        timeframe_details = {}

        bullish_timeframes = []
        bearish_timeframes = []

        coin_signals = []

        for timeframe in TIMEFRAMES:

            items = signal_groups.get(
                (
                    coin["cmc_id"],
                    timeframe,
                ),
                [],
            )

            coin_signals.extend(items)

            divergence_types = sorted({
                item["divergence"]
                for item in items
            })

            directions = {
                item["signal"]
                for item in items
            }

            if items:

                status = "VAR"

                translated = [
                    DIVERGENCE_NAMES.get(
                        item,
                        item,
                    )
                    for item in divergence_types
                ]

                type_text = " + ".join(translated)

            else:

                status = "YOK"
                type_text = "-"

            timeframe_details[timeframe] = {
                "status": status,
                "types": type_text,
                "directions": directions,
            }

            if "BULLISH" in directions:
                bullish_timeframes.append(timeframe)

            if "BEARISH" in directions:
                bearish_timeframes.append(timeframe)

        # If directions disagree across timeframes,
        # mark the coin as conflicting.
        if bullish_timeframes and bearish_timeframes:

            alignment = "CELISKILI"

        elif len(bullish_timeframes) == 3:

            alignment = "3/3 POZITIF UYUM"

        elif len(bullish_timeframes) == 2:

            alignment = "2/3 POZITIF UYUM"

        elif len(bullish_timeframes) == 1:

            alignment = "1/3 POZITIF SINYAL"

        elif len(bearish_timeframes) == 3:

            alignment = "3/3 NEGATIF UYUM"

        elif len(bearish_timeframes) == 2:

            alignment = "2/3 NEGATIF UYUM"

        elif len(bearish_timeframes) == 1:

            alignment = "1/3 NEGATIF SINYAL"

        else:

            alignment = "SINYAL YOK"

        new_count = sum(
            1
            for item in coin_signals
            if item["new_this_scan"] == "YES"
        )

        matrix_row = {
            "cmc_id": coin["cmc_id"],
            "coin": coin["name"],
            "cmc_symbol": coin["cmc_symbol"],
            "binance_symbol": coin["binance_symbol"],

            "4h_status": timeframe_details["4h"]["status"],
            "4h_types": timeframe_details["4h"]["types"],

            "1d_status": timeframe_details["1d"]["status"],
            "1d_types": timeframe_details["1d"]["types"],

            "1w_status": timeframe_details["1w"]["status"],
            "1w_types": timeframe_details["1w"]["types"],

            "bullish_timeframes": ",".join(bullish_timeframes),
            "bearish_timeframes": ",".join(bearish_timeframes),

            "alignment": alignment,

            "signal_count": len(coin_signals),
            "new_signal_count": new_count,
        }

        matrix_rows.append(matrix_row)

    return matrix_rows


# ============================================================
# 13. MATRIX DISPLAY TEXT
# ============================================================

def timeframe_text(row, timeframe, short_name):

    status = row[f"{timeframe}_status"]
    types = row[f"{timeframe}_types"]

    if status == "YOK":
        return f"{short_name}: YOK"

    return f"{short_name}: VAR ({types})"


def write_reports(
    signals,
    candidates,
    matrix_rows,
    coins_total,
    mapping_counts,
    error_rows,
    source_file,
    requests_done,
    requests_total,
):

    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    # Detailed signal CSV.
    with CSV_REPORT.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=CSV_FIELDS,
        )

        writer.writeheader()

        for row in signals:

            writer.writerow({
                field: row.get(field, "")
                for field in CSV_FIELDS
            })

    # Full coin x timeframe matrix CSV.
    with MATRIX_REPORT.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=MATRIX_FIELDS,
        )

        writer.writeheader()
        writer.writerows(matrix_rows)

    # Full CMC-to-Binance mapping report.
    # Mapping rows are written in main().
    new_signals = [
        row
        for row in signals
        if row["new_this_scan"] == "YES"
    ]

    generated_at = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")

    alignment_counts = Counter(
        row["alignment"]
        for row in matrix_rows
    )

    lines = [
        "COIN SINYAL - RSI UYUMSUZLUK RAPORU",
        f"Tarama zamani: {generated_at}",
        f"CMC kaynak dosyasi: {source_file.name}",
        f"Toplam CMC coin: {coins_total}",
        f"Binance USDT aday coin: {len(candidates)}",
        f"Basarili istek: {requests_done}/{requests_total}",
        "",
        "ZAMAN DILIMLERI",
        "4 Saat (4h) | 1 Gun (1d) | 1 Hafta (1w)",
        "",
        f"RSI: Wilder {RSI_PERIOD}",
        f"Pivot: {PIVOT_LEFT} sol / {PIVOT_RIGHT} sag",
        f"Min fiyat farki: %{MIN_PRICE_DIFFERENCE_PCT}",
        f"Min RSI farki: {MIN_RSI_DIFFERENCE}",
        "",
        "Yalnizca kapanmis Binance mumlari kullanildi.",
        "Pivot, sagindaki uc mum kapandiktan sonra onaylanir.",
        "Eski pivotlar guncel sinyal olarak raporlanmaz.",
        "",
        "ESLESTIRME OZETI",
    ]

    for status, count in sorted(mapping_counts.items()):
        lines.append(f"{status}: {count}")

    lines.extend([
        "",
        "GENEL SONUC",
        f"Toplam aktif uyumsuzluk: {len(signals)}",
        f"Bu taramada yeni sinyal: {len(new_signals)}",
        f"Veri hatasi / atlanan istek: {len(error_rows)}",
        "",
        "ZAMAN DILIMI MATRISI",
        f"3/3 pozitif uyum: {alignment_counts['3/3 POZITIF UYUM']}",
        f"2/3 pozitif uyum: {alignment_counts['2/3 POZITIF UYUM']}",
        f"1/3 pozitif sinyal: {alignment_counts['1/3 POZITIF SINYAL']}",
        f"3/3 negatif uyum: {alignment_counts['3/3 NEGATIF UYUM']}",
        f"2/3 negatif uyum: {alignment_counts['2/3 NEGATIF UYUM']}",
        f"1/3 negatif sinyal: {alignment_counts['1/3 NEGATIF SINYAL']}",
        f"Celiski: {alignment_counts['CELISKILI']}",
        f"Sinyal yok: {alignment_counts['SINYAL YOK']}",
        "",
        "AKTIF COINLER - VAR / YOK",
    ])

    active_rows = [
        row
        for row in matrix_rows
        if row["alignment"] != "SINYAL YOK"
    ]

    if not active_rows:

        lines.append(
            "Bu taramada guncel ve onaylanmis RSI uyumsuzlugu bulunamadi."
        )

    else:

        ordered = sorted(
            active_rows,
            key=lambda row: (
                0 if row["alignment"] == "3/3 POZITIF UYUM" else
                1 if row["alignment"] == "3/3 NEGATIF UYUM" else
                2 if row["alignment"] == "2/3 POZITIF UYUM" else
                3 if row["alignment"] == "2/3 NEGATIF UYUM" else
                4 if row["alignment"] == "CELISKILI" else
                5,
                row["coin"],
            ),
        )

        for row in ordered:

            is_new = (
                "YENI"
                if row["new_signal_count"] > 0
                else "Daha once bildirildi"
            )

            lines.append(
                f'{is_new} | {row["cmc_symbol"]} '
                f'({row["binance_symbol"]}) | '
                f'{timeframe_text(row, "4h", "4S")} | '
                f'{timeframe_text(row, "1d", "1G")} | '
                f'{timeframe_text(row, "1w", "1Hf")} | '
                f'SONUC: {row["alignment"]}'
            )

    # Detailed signal lines.
    lines.extend([
        "",
        "DETAYLI SINYALLER",
    ])

    if not signals:

        lines.append("Detayli sinyal bulunamadi.")

    else:

        ordered_signals = sorted(
            signals,
            key=lambda row: (
                row["coin"],
                row["timeframe"],
                row["divergence"],
            ),
        )

        for row in ordered_signals:

            tag = (
                "YENI"
                if row["new_this_scan"] == "YES"
                else "ESKI"
            )

            type_name = DIVERGENCE_NAMES.get(
                row["divergence"],
                row["divergence"],
            )

            lines.append(
                f'{tag} | {row["cmc_symbol"]} | '
                f'{TIMEFRAMES[row["timeframe"]]} | '
                f'{type_name} | '
                f'Fiyat: {row["price_1"]} -> {row["price_2"]} | '
                f'RSI: {row["rsi_1"]} -> {row["rsi_2"]} | '
                f'Pivot: {row["pivot_2_utc"]} | '
                f'Onay: {row["confirmation_utc"]}'
            )

    if error_rows:

        lines.extend([
            "",
            "VERI HATALARI - ILK 30",
        ])

        lines.extend(error_rows[:30])

        if len(error_rows) > 30:
            lines.append(
                f"... ve {len(error_rows) - 30} ek hata"
            )

    lines.extend([
        "",
        "NOTLAR",
        "RSI uyumsuzlugu tek basina AL/SAT emri degildir.",
        "VAR bilgisi yalnizca guncel ve onaylanmis pivotu ifade eder.",
        "CMC sembol eslesmesi proje kimligini garanti etmez.",
        "YOK, o zaman diliminde bu taramada aktif uyumsuzluk bulunmadigi anlamina gelir.",
    ])

    TEXT_REPORT.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    required_files = (
        CSV_REPORT,
        TEXT_REPORT,
        MATRIX_REPORT,
        MAPPING_REPORT,
        SEEN_STATE,
    )

    for output in required_files:

        if (
            not output.is_file()
            or output.stat().st_size == 0
        ):
            raise RuntimeError(
                f"Rapor veya durum dosyasi olusturulamadi: {output}"
            )


# ============================================================
# 14. MAIN SCANNER
# ============================================================

def main():

    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    coins, source_file = load_latest_cmc_coins()

    candidates, mapping_counts, mapping_rows = (
        build_binance_candidates(coins)
    )

    print(f"CMC kaynak dosyasi: {source_file}")
    print(f"CMC coin sayisi: {len(coins)}")
    print(f"Binance USDT aday coin: {len(candidates)}")

    print("\nESLESTIRME OZETI")

    for status, count in sorted(mapping_counts.items()):
        print(f"{status}: {count}")

    if not candidates:
        raise RuntimeError(
            "Binance USDT adayi bulunamadi."
        )

    # Save the mapping report.
    with MAPPING_REPORT.open(
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "cmc_id",
                "name",
                "cmc_symbol",
                "binance_symbol",
                "status",
            ],
        )

        writer.writeheader()
        writer.writerows(mapping_rows)

    server_info = http_json(SERVER_TIME_URL)

    server_time_ms = int(
        server_info["serverTime"]
    )

    signals = []
    errors = []

    requests_done = 0

    requests_total = (
        len(candidates) * len(TIMEFRAMES)
    )

    print("\nRSI TARAMASI BASLADI")

    for coin in candidates:

        symbol = coin["binance_symbol"]

        for interval, label in TIMEFRAMES.items():

            requests_done += 1

            print(
                f"[{requests_done}/{requests_total}] "
                f'{coin["cmc_symbol"]} {symbol} {label}'
            )

            try:

                candles = fetch_closed_klines(
                    symbol,
                    interval,
                    server_time_ms,
                )

                found = detect_divergences(
                    candles,
                    coin,
                    interval,
                )

                signals.extend(found)

            except Exception as exc:

                message = (
                    f'{coin["cmc_symbol"]} '
                    f'{symbol} {interval}: {exc}'
                )

                errors.append(message)

                print(
                    f"VERI HATASI: {exc}"
                )

            time.sleep(
                REQUEST_PAUSE_SECONDS
            )

    successful_requests = (
        requests_total - len(errors)
    )

    if successful_requests == 0:

        raise RuntimeError(
            "Tum Binance mum istekleri basarisiz. "
            "Eski rapor korunuyor."
        )

    new_count, seen_count = mark_new_signals(
        signals
    )

    matrix_rows = build_matrix_rows(
        candidates,
        signals,
    )

    write_reports(
        signals=signals,
        candidates=candidates,
        matrix_rows=matrix_rows,
        coins_total=len(coins),
        mapping_counts=mapping_counts,
        error_rows=errors,
        source_file=source_file,
        requests_done=successful_requests,
        requests_total=requests_total,
    )

    print("\nRSI TARAMA SONUCU")

    print(
        f"Aday coin: {len(candidates)}"
    )

    print(
        f"Basarili mum istegi: "
        f"{successful_requests}/{requests_total}"
    )

    print(
        f"Aktif uyumsuzluk: {len(signals)}"
    )

    print(
        f"Yeni sinyal: {new_count}"
    )

    print(
        f"Veri hatasi: {len(errors)}"
    )

    print(
        f"Kayitli sinyal anahtari: {seen_count}"
    )

    print(
        f"Detay CSV: {CSV_REPORT}"
    )

    print(
        f"VAR/YOK matrisi: {MATRIX_REPORT}"
    )

    print(
        f"Metin raporu: {TEXT_REPORT}"
    )

    if errors:
        print(
            "UYARI: Bazi Binance istekleri basarisiz. "
            "Rapor eksik veri icerebilir."
        )


# ============================================================
# 15. ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(f"HATA: {exc}")
        raise SystemExit(1)

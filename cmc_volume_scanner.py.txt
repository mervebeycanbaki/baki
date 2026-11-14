
import os
import time
import json
import math
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

# ==========================================================
# 1. CONFIGURATION
# ==========================================================

load_dotenv()

CMC_API_KEY = os.getenv("CMC_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://pro-api.coinmarketcap.com"

LISTING_URL = (
    BASE_URL + "/v3/cryptocurrency/listings/latest"
)

HISTORY_URL = (
    BASE_URL + "/v3/cryptocurrency/quotes/historical"
)

# Basic: 365, Builder: 730
HISTORY_DAYS = int(os.getenv("CMC_HISTORY_DAYS", "365"))

DAILY_CREDIT_BUDGET = int(
    os.getenv("DAILY_CREDIT_BUDGET", "450")
)

MAX_CANDIDATES = int(
    os.getenv("MAX_CANDIDATES", "110")
)

LISTING_LIMIT = 5000
PREFILTER_RATIO = 0.05
EXTREME_RATIO = 0.15
MIN_STRONG_PERIODS = 3

WINDOWS = [30, 90, 180, 365, 730]

# 50 ID per historical request
BATCH_SIZE = 50

DB_FILE = "crypto_scanner.db"
CSV_FILE = "crypto_scan_results.csv"

HEADERS = {
    "X-CMC_PRO_API_KEY": CMC_API_KEY,
    "Accept": "application/json"
}

# ==========================================================
# 2. LOGGING
# ==========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(
            "crypto_scanner.log",
            encoding="utf-8"
        ),
        logging.StreamHandler()
    ]
)

log = logging.getLogger("CryptoScanner")

# ==========================================================
# 3. VALIDATION
# ==========================================================

if not CMC_API_KEY:
    raise RuntimeError("CMC_API_KEY bulunamadi!")

if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
    raise RuntimeError("Telegram bilgileri eksik!")

if HISTORY_DAYS not in (365, 730):
    raise ValueError(
        "CMC_HISTORY_DAYS sadece 365 veya 730 olabilir."
    )

ACTIVE_WINDOWS = [
    days for days in WINDOWS
    if days <= HISTORY_DAYS
]

# ==========================================================
# 4. DATABASE
# ==========================================================

def init_database():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            coin_id INTEGER,
            scan_date TEXT,
            signal_type TEXT,
            PRIMARY KEY (coin_id, scan_date)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_history (
            coin_id INTEGER,
            scan_date TEXT,
            volume_24h REAL,
            market_cap REAL,
            volume_ratio REAL,
            strong_periods INTEGER,
            PRIMARY KEY (coin_id, scan_date)
        )
    """)

    conn.commit()
    conn.close()


def already_notified(coin_id, scan_date):

    conn = sqlite3.connect(DB_FILE)

    result = conn.execute(
        """
        SELECT 1 FROM notifications
        WHERE coin_id = ? AND scan_date = ?
        """,
        (coin_id, scan_date)
    ).fetchone()

    conn.close()

    return result is not None


def save_notification(coin_id, scan_date):

    conn = sqlite3.connect(DB_FILE)

    conn.execute(
        """
        INSERT OR IGNORE INTO notifications
        VALUES (?, ?, ?)
        """,
        (coin_id, scan_date, "STRONG")
    )

    conn.commit()
    conn.close()


# ==========================================================
# 5. API REQUEST ENGINE
# ==========================================================

session = requests.Session()
session.headers.update(HEADERS)

credit_usage = 0


def api_get(url, params=None, retries=4):

    global credit_usage

    for attempt in range(retries):

        try:

            response = session.get(
                url,
                params=params,
                timeout=40
            )

            if response.status_code == 429:

                wait = 60

                log.warning(
                    "Rate limit! %s saniye bekleniyor.",
                    wait
                )

                time.sleep(wait)
                continue

            response.raise_for_status()

            result = response.json()

            status = result.get("status", {})

            if status.get("error_code", 0) not in (0, "0"):

                raise RuntimeError(
                    status.get("error_message")
                )

            credit_usage += int(
                status.get("credit_count", 0)
            )

            time.sleep(1.2)

            return result

        except Exception as exc:

            log.error(
                "API hatasi (%s/%s): %s",
                attempt + 1,
                retries,
                exc
            )

            if attempt == retries - 1:
                raise

            time.sleep(2 ** attempt)

    return None


# ==========================================================
# 6. STAGE 1 - MARKET SCANNING
# ==========================================================

def get_all_coins():

    log.info("Tum coinler taraniyor...")

    result = api_get(
        LISTING_URL,
        {
            "start": 1,
            "limit": LISTING_LIMIT,
            "convert": "USD",
            "sort": "market_cap",
            "sort_dir": "desc"
        }
    )

    return result.get("data", [])


def prefilter_coins(coins):

    candidates = []

    for coin in coins:

        quote = coin.get("quote", [{}])[0]

        volume = quote.get("volume_24h") or 0
        market_cap = quote.get("market_cap") or 0

        if market_cap <= 0 or volume <= 0:
            continue

        ratio = volume / market_cap

        if ratio >= PREFILTER_RATIO:

            candidates.append({
                "id": coin["id"],
                "name": coin["name"],
                "symbol": coin["symbol"],
                "rank": coin.get("cmc_rank"),
                "price": quote.get("price"),
                "volume_24h": volume,
                "market_cap": market_cap,
                "volume_ratio": ratio,
                "change_24h": quote.get(
                    "percent_change_24h"
                )
            })

    # Highest volume / market cap first
    candidates.sort(
        key=lambda x: x["volume_ratio"],
        reverse=True
    )

    log.info(
        "On filtreyi gecen coin sayisi: %s",
        len(candidates)
    )

    return candidates


# ==========================================================
# 7. STAGE 2 - HISTORICAL VOLUME DATA
# ==========================================================

def get_historical_volumes(coin_ids):

    historical = {}

    now = datetime.now(timezone.utc)

    # Incomplete current UTC day is excluded
    end_date = now.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    ) - timedelta(days=1)

    start_date = end_date - timedelta(
        days=HISTORY_DAYS
    )

    start = start_date.strftime("%Y-%m-%d")
    end = end_date.strftime("%Y-%m-%d")

    for i in range(0, len(coin_ids), BATCH_SIZE):

        batch = coin_ids[i:i + BATCH_SIZE]

        estimated_points = len(batch) * HISTORY_DAYS

        estimated_credits = math.ceil(
            estimated_points / 100
        )

        if credit_usage + estimated_credits > DAILY_CREDIT_BUDGET:

            log.warning(
                "Gunluk kredi butcesi dolacak. "
                "Kalan coinler atlandi."
            )

            break

        log.info(
            "Gecmis veri aliniyor: %s coin",
            len(batch)
        )

        result = api_get(
            HISTORY_URL,
            {
                "id": ",".join(map(str, batch)),
                "time_start": start,
                "time_end": end,
                "interval": "daily",
                "aux": "volume,market_cap",
                "skip_invalid": "true"
            }
        )

        if not result:
            continue

        data = result.get("data", {})

        for coin_id in batch:

            coin_data = data.get(str(coin_id))

            if not coin_data:
                continue

            quotes = coin_data.get("quotes", [])

            daily_volumes = []

            for item in quotes:

                usd = (
                    item.get("quote", {})
                    .get("USD", {})
                )

                volume = usd.get("volume_24h")

                if volume is not None and volume > 0:
                    daily_volumes.append(float(volume))

            historical[coin_id] = daily_volumes

    return historical


# ==========================================================
# 8. VOLUME ANALYSIS
# ==========================================================

def analyze_coin(coin, daily_volumes):

    current_volume = coin["volume_24h"]

    averages = {}
    comparisons = {}

    for days in ACTIVE_WINDOWS:

        values = daily_volumes[-days:]

        # Require at least 80% of expected observations.
        minimum_data = math.ceil(days * 0.80)

        if len(values) < minimum_data:

            averages[days] = None
            comparisons[days] = False

            continue

        average = sum(values) / len(values)

        averages[days] = average

        comparisons[days] = (
            current_volume > average
        )

    passed = sum(comparisons.values())

    available = sum(
        value is not None
        for value in averages.values()
    )

    strong = (
        available >= MIN_STRONG_PERIODS
        and passed >= MIN_STRONG_PERIODS
    )

    extreme = (
        coin["volume_ratio"] >= EXTREME_RATIO
    )

    return {
        "coin": coin,
        "averages": averages,
        "comparisons": comparisons,
        "passed": passed,
        "available": available,
        "strong": strong,
        "extreme": extreme
    }


# ==========================================================
# 9. TELEGRAM NOTIFICATIONS
# ==========================================================

def send_telegram(message):

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=30
    )

    response.raise_for_status()

    result = response.json()

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram hatasi: {result}"
        )


def money(value):

    if value is None:
        return "Veri yok"

    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"

    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"

    if value >= 1_000:
        return f"${value / 1_000:.2f}K"

    return f"${value:,.2f}"


def create_message(result):

    coin = result["coin"]

    passed = result["passed"]

    lines = [
        "🚨 <b>GUCLU HACIM SINYALI</b>",
        "",
        f"🪙 <b>{coin['name']} ({coin['symbol']})</b>",
        f"📊 CMC Rank: #{coin['rank']}",
        f"💵 Fiyat: ${coin['price']:,.8f}",
        "",
        f"📈 Son 24 Saat Hacim: "
        f"<b>{money(coin['volume_24h'])}</b>",
        f"🏦 Market Cap: {money(coin['market_cap'])}",
        f"⚖️ Hacim / Market Cap: "
        f"<b>%{coin['volume_ratio'] * 100:.2f}</b>",
        "",
        f"🔥 Ortalama ustu: "
        f"<b>{passed}/{result['available']}</b>",
        "",
        "<b>HACIM ORTALAMALARI</b>"
    ]

    for days in ACTIVE_WINDOWS:

        avg = result["averages"][days]

        if avg is None:
            status = "⚪ Yetersiz veri"
        elif result["comparisons"][days]:
            status = "🟢 USTUNDE"
        else:
            status = "🔴 ALTINDA"

        label = f"{days} gun"

        lines.append(
            f"{label}: {money(avg)} | {status}"
        )

    if result["extreme"]:

        lines.extend([
            "",
            "⚠️ <b>ASIRI HACIM ANOMALISI</b>",
            "Hacim / Market Cap orani %15 veya uzerinde."
        ])

    lines.extend([
        "",
        f"📉 24s Fiyat Degisimi: "
        f"%{coin['change_24h'] or 0:.2f}",
        "",
        f"🕒 UTC: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
        "",
        "⚠️ Bu bir hacim uyarisidir, "
        "tek basina AL sinyali degildir."
    ])

    return "\n".join(lines)


# ==========================================================
# 10. CSV EXPORT
# ==========================================================

def export_results(results):

    import csv

    rows = []

    for result in results:

        coin = result["coin"]

        row = {
            "name": coin["name"],
            "symbol": coin["symbol"],
            "volume_24h": coin["volume_24h"],
            "market_cap": coin["market_cap"],
            "volume_ratio": coin["volume_ratio"],
            "strong_periods": result["passed"],
            "strong_signal": result["strong"],
            "extreme_anomaly": result["extreme"]
        }

        for days in ACTIVE_WINDOWS:

            row[f"avg_{days}d"] = (
                result["averages"][days]
            )

            row[f"above_{days}d"] = (
                result["comparisons"][days]
            )

        rows.append(row)

    if not rows:
        return

    with open(
        CSV_FILE,
        "w",
        newline="",
        encoding="utf-8-sig"
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=rows[0].keys()
        )

        writer.writeheader()
        writer.writerows(rows)


# ==========================================================
# 11. MAIN SCANNER
# ==========================================================

def run_scanner():

    global credit_usage

    credit_usage = 0

    init_database()

    scan_date = datetime.now(
        timezone.utc
    ).strftime("%Y-%m-%d")

    log.info("=" * 55)
    log.info("CRYPTO VOLUME SCANNER BASLADI")
    log.info("=" * 55)

    # Stage 1
    coins = get_all_coins()

    candidates = prefilter_coins(coins)

    # Safety cap
    candidates = candidates[:MAX_CANDIDATES]

    if not candidates:

        log.info("Aday coin bulunamadi.")
        return

    coin_ids = [
        coin["id"] for coin in candidates
    ]

    # Stage 2
    historical = get_historical_volumes(
        coin_ids
    )

    results = []

    for coin in candidates:

        daily_volumes = historical.get(
            coin["id"], []
        )

        if not daily_volumes:
            continue

        analysis = analyze_coin(
            coin,
            daily_volumes
        )

        results.append(analysis)

    # Export
    export_results(results)

    strong_signals = [
        result for result in results
        if result["strong"]
    ]

    strong_signals.sort(
        key=lambda x: (
            x["passed"],
            x["coin"]["volume_ratio"]
        ),
        reverse=True
    )

    # Notifications
    sent = 0

    for result in strong_signals:

        coin_id = result["coin"]["id"]

        if already_notified(
            coin_id,
            scan_date
        ):
            continue

        message = create_message(result)

        try:

            send_telegram(message)

            save_notification(
                coin_id,
                scan_date
            )

            sent += 1

            log.info(
                "Sinyal gonderildi: %s",
                result["coin"]["symbol"]
            )

            time.sleep(1.2)

        except Exception as exc:

            log.error(
                "Telegram gonderim hatasi: %s",
                exc
            )

    # Summary
    log.info("=" * 55)
    log.info("TARAMA TAMAMLANDI")
    log.info("Toplam coin: %s", len(coins))
    log.info("On filtre: %s", len(candidates))
    log.info("Derin analiz: %s", len(results))
    log.info("Guclu sinyal: %s", len(strong_signals))
    log.info("Yeni bildirim: %s", sent)
    log.info("CMC kredi kullanimi: %s", credit_usage)
    log.info("=" * 55)


# ==========================================================
# 12. EXECUTION
# ==========================================================

if __name__ == "__main__":

    try:

        run_scanner()

    except KeyboardInterrupt:

        log.warning("Program kullanici tarafindan durduruldu.")

    except Exception as exc:

        log.exception("Sistem hatasi: %s", exc)

        try:
            send_telegram(
                "❌ <b>CRYPTO SCANNER HATASI</b>\n\n"
                f"<code>{str(exc)[:1000]}</code>"
            )
        except Exception:
            pass

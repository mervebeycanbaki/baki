import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests

API_KEY = os.getenv("CMC_API_KEY")
BASE_URL = "https://pro-api.coinmarketcap.com"
COIN_LIMIT = 1000
BATCH_SIZE = 50
HISTORY_DAYS = 365
OUTPUT_DIR = Path("data/daily")
SUMMARY_PATH = Path("data/coverage.json")

SESSION = requests.Session()
SESSION.headers.update({
    "Accept": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY or "",
})


def api_get(endpoint, params, label):
    url = BASE_URL + endpoint
    last_error = None

    for attempt in range(5):
        try:
            response = SESSION.get(url, params=params, timeout=120)
            try:
                payload = response.json()
            except ValueError:
                payload = {}

            status = payload.get("status") or {}
            error_code = str(status.get("error_code", "0"))
            is_temporary = response.status_code in (429, 500, 502, 503, 504)

            if response.status_code == 200 and error_code == "0":
                return payload, int(status.get("credit_count") or 0)

            message = status.get("error_message") or response.text[:500]
            last_error = RuntimeError(
                f"{label}: HTTP {response.status_code}; CMC {error_code}; {message}"
            )

            if is_temporary and attempt < 4:
                wait = 3 * (2 ** attempt)
                print(f"Gecici API hatasi. {wait} saniye sonra tekrar denenecek.")
                time.sleep(wait)
                continue

            raise last_error

        except requests.RequestException as exc:
            last_error = exc
            if attempt < 4:
                wait = 3 * (2 ** attempt)
                print(f"Baglanti sorunu. {wait} saniye sonra tekrar denenecek.")
                time.sleep(wait)
                continue
            raise RuntimeError(f"{label}: baglanti hatasi: {exc}") from exc

    raise RuntimeError(f"{label}: istek basarisiz: {last_error}")


def main():
    if not API_KEY:
        raise RuntimeError("CMC_API_KEY GitHub Secret bulunamadi.")

    if any(OUTPUT_DIR.glob("*.csv")):
        raise RuntimeError(
            "data/daily klasorunde CSV dosyalari zaten var. "
            "Tekrar ilk yukleme yapmamak icin islem durduruldu."
        )

    print("Ilk 1 yillik veri yuklemesi basliyor...")
    print(f"Hedef coin sayisi: {COIN_LIMIT}")
    print(f"Coin basina hedef gunluk kayit: {HISTORY_DAYS}")

    listing_payload, listing_credits = api_get(
        "/v3/cryptocurrency/listings/latest",
        {"limit": COIN_LIMIT, "convert": "USD"},
        "Coin listesi",
    )

    coins = listing_payload.get("data") or []
    if not coins:
        raise RuntimeError("CMC coin listesi bos dondu.")

    # CoinMarketCap ID is used as the stable key; symbols are display labels only.
    assets = []
    seen_ids = set()
    for coin in coins:
        coin_id = int(coin["id"])
        if coin_id in seen_ids:
            continue
        seen_ids.add(coin_id)
        assets.append({
            "id": coin_id,
            "name": coin.get("name") or "",
            "symbol": coin.get("symbol") or "",
        })

    if len(assets) > COIN_LIMIT:
        assets = assets[:COIN_LIMIT]

    print(f"CMC listesinden alinan benzersiz coin: {len(assets)}")

    daily_rows = defaultdict(list)
    received_ids = set()
    total_points = 0
    historical_credits = 0
    batch_count = (len(assets) + BATCH_SIZE - 1) // BATCH_SIZE

    for batch_no, start in enumerate(range(0, len(assets), BATCH_SIZE), start=1):
        batch = assets[start:start + BATCH_SIZE]
        ids = [str(asset["id"]) for asset in batch]
        print(f"Gecmis veri paketi {batch_no}/{batch_count}: {len(ids)} coin")

        payload, credits = api_get(
            "/v3/cryptocurrency/quotes/historical",
            {
                "id": ",".join(ids),
                "count": HISTORY_DAYS,
                "interval": "daily",
                "convert": "USD",
            },
            f"Gecmis veri paketi {batch_no}",
        )
        historical_credits += credits

        data = payload.get("data") or {}
        batch_points = 0

        for id_text, asset_data in data.items():
            coin_id = int(id_text)
            received_ids.add(coin_id)
            symbol = asset_data.get("symbol") or ""
            name = asset_data.get("name") or ""

            for snapshot in asset_data.get("quotes") or []:
                timestamp = snapshot.get("timestamp") or ""
                if len(timestamp) < 10:
                    continue

                usd = ((snapshot.get("quote") or {}).get("USD") or {})
                volume = usd.get("volume_24h")
                if volume is None:
                    continue

                day = timestamp[:10]
                daily_rows[day].append([
                    coin_id,
                    name,
                    symbol,
                    timestamp,
                    volume,
                    usd.get("price", ""),
                    usd.get("market_cap", ""),
                ])
                batch_points += 1

        total_points += batch_points
        print(f"  Bu paketten gelen veri noktasi: {batch_points}; kredi: {credits}")

        # 50 IDs per request is deliberately conservative; pause between calls.
        if batch_no < batch_count:
            time.sleep(1.3)

    if total_points == 0 or not daily_rows:
        raise RuntimeError("Gecmis veri hic gelmedi; dosyalar yazilmadi.")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "cmc_id", "name", "symbol", "timestamp_utc",
        "volume_24h_usd", "price_usd", "market_cap_usd",
    ]

    for day, rows in sorted(daily_rows.items()):
        rows.sort(key=lambda row: row[0])
        output_path = OUTPUT_DIR / f"{day}.csv"
        with output_path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            writer.writerow(fieldnames)
            writer.writerows(rows)

    missing_ids = sorted(seen_ids - received_ids)
    summary = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "requested_coins": len(assets),
        "coins_with_historical_data": len(received_ids),
        "coins_without_returned_history": len(missing_ids),
        "missing_coin_ids": missing_ids,
        "history_days_requested_per_coin": HISTORY_DAYS,
        "total_historical_data_points": total_points,
        "daily_files_created": len(daily_rows),
        "first_date": min(daily_rows),
        "last_date": max(daily_rows),
        "listing_credits_reported": listing_credits,
        "historical_credits_reported": historical_credits,
        "note": (
            "volume_24h_usd is the 24-hour rolling-volume quote at the first quote "
            "of each UTC day, not an exchange candle's standalone daily volume."
        ),
    }

    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    SUMMARY_PATH.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nILK VERI YUKLEMESI TAMAMLANDI")
    print("Coin sayisi:", len(assets))
    print("Gecmisi bulunan coin:", len(received_ids))
    print("Toplam veri noktasi:", total_points)
    print("Olusturulan gunluk dosya:", len(daily_rows))
    print("CMC kredi (liste + gecmis):", listing_credits + historical_credits)
    print("Ozet dosyasi:", SUMMARY_PATH)
    print("Not: Yeni coinlerin 365 gunden az gecmisi olabilir; bu normaldir.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("YUKLEME HATASI:", str(error))
        sys.exit(1)

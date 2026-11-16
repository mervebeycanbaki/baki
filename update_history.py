import csv
import json
import os
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from pathlib import Path

import requests

API_KEY = os.getenv("CMC_API_KEY")
BASE_URL = "https://pro-api.coinmarketcap.com"
COIN_LIMIT = 1000
BATCH_SIZE = 25  # 25 coins x 1 daily point = far below the 10,000-point request limit
MAX_CATCHUP_DAYS = 14
REQUEST_PAUSE_SECONDS = 1.3
DAILY_DIR = Path("data/daily")
SUMMARY_PATH = Path("data/coverage.json")
FIELDNAMES = [
    "cmc_id",
    "name",
    "symbol",
    "timestamp_utc",
    "volume_24h_usd",
    "price_usd",
    "market_cap_usd",
]

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Accept": "application/json",
        "X-CMC_PRO_API_KEY": API_KEY or "",
    }
)


def cmc_get(endpoint: str, params: dict, label: str):
    """Call CMC with explicit API errors and limited retries for temporary failures."""
    url = BASE_URL + endpoint
    last_error = None

    for attempt in range(5):
        try:
            response = SESSION.get(url, params=params, timeout=90)
            try:
                payload = response.json()
            except ValueError:
                payload = {}

            status = payload.get("status") or {}
            error_code = str(status.get("error_code", "0"))
            temporary = response.status_code in (429, 500, 502, 503, 504)

            if response.status_code == 200 and error_code == "0":
                return payload, int(status.get("credit_count") or 0)

            message = status.get("error_message") or response.text[:500]
            last_error = RuntimeError(
                f"{label}: HTTP {response.status_code}; CMC {error_code}; {message}"
            )

            if temporary and attempt < 4:
                wait_seconds = 5 * (2 ** attempt)
                print(f"Gecici CMC hatasi. {wait_seconds} saniye beklenecek.")
                time.sleep(wait_seconds)
                continue

            raise last_error

        except requests.RequestException as exc:
            last_error = exc
            if attempt < 4:
                wait_seconds = 5 * (2 ** attempt)
                print(f"Baglanti hatasi. {wait_seconds} saniye sonra tekrar denenecek.")
                time.sleep(wait_seconds)
                continue
            raise RuntimeError(f"{label}: baglanti hatasi: {exc}") from exc

    raise RuntimeError(f"{label}: istek basarisiz: {last_error}")


def find_latest_stored_date() -> date:
    """Return the latest YYYY-MM-DD.csv date already stored in data/daily."""
    if not DAILY_DIR.exists():
        raise RuntimeError("data/daily klasoru bulunamadi. Once ilk veri yuklemesi tamamlanmali.")

    dates = []
    for path in DAILY_DIR.glob("????-??-??.csv"):
        try:
            dates.append(date.fromisoformat(path.stem))
        except ValueError:
            continue

    if not dates:
        raise RuntimeError("data/daily klasorunde tarihli CSV bulunamadi.")

    return max(dates)


def get_dates_to_fill(latest_stored: date):
    """Fill missing UTC days up to yesterday; never store a partial current UTC day."""
    yesterday_utc = datetime.now(timezone.utc).date() - timedelta(days=1)
    if latest_stored >= yesterday_utc:
        return []

    first_missing = latest_stored + timedelta(days=1)
    all_missing = []
    current = first_missing
    while current <= yesterday_utc:
        all_missing.append(current)
        current += timedelta(days=1)

    if len(all_missing) > MAX_CATCHUP_DAYS:
        print(
            f"UYARI: {len(all_missing)} gunluk acik var. Bu calistirmada "
            f"ilk {MAX_CATCHUP_DAYS} gun tamamlanacak."
        )
        return all_missing[:MAX_CATCHUP_DAYS]

    return all_missing


def fetch_current_top_coins():
    print(f"CMC listesinden ilk {COIN_LIMIT} coin aliniyor...")
    payload, credits = cmc_get(
        "/v3/cryptocurrency/listings/latest",
        {"limit": COIN_LIMIT, "convert": "USD"},
        "Guncel coin listesi",
    )

    coins = payload.get("data") or []
    if not coins:
        raise RuntimeError("CMC guncel coin listesi bos dondu.")

    assets = []
    seen = set()
    for coin in coins:
        coin_id = int(coin["id"])
        if coin_id in seen:
            continue
        seen.add(coin_id)
        assets.append(
            {
                "id": coin_id,
                "name": coin.get("name") or "",
                "symbol": coin.get("symbol") or "",
            }
        )
        if len(assets) >= COIN_LIMIT:
            break

    print(f"Benzersiz guncel coin: {len(assets)}; listeleme kredisi: {credits}")
    return assets, credits


def load_existing_day(day: date):
    path = DAILY_DIR / f"{day.isoformat()}.csv"
    rows_by_id = {}
    if not path.exists():
        return path, rows_by_id

    with path.open("r", newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            try:
                rows_by_id[int(row["cmc_id"])] = row
            except (KeyError, ValueError):
                continue
    return path, rows_by_id


def fetch_day_for_coin_batch(day: date, batch: list):
    ids = [str(asset["id"]) for asset in batch]

    end_utc = (
        datetime.combine(day, dt_time.min, tzinfo=timezone.utc)
        + timedelta(days=1)
        - timedelta(milliseconds=1)
    )

    payload, credits = cmc_get(
        "/v3/cryptocurrency/quotes/historical",
        {
            "id": ",".join(ids),
            "time_end": end_utc.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "count": 1,
            "interval": "daily",
            "convert": "USD",
        },
        f"{day.isoformat()} gecmis veri paketi",
    )

    assets_data = payload.get("data") or {}
    output_rows = {}

    for id_text, asset_data in assets_data.items():
        try:
            coin_id = int(id_text)
        except (TypeError, ValueError):
            continue

        snapshots = asset_data.get("quotes") or []

        matching = []
        for snapshot in snapshots:
            timestamp = snapshot.get("timestamp") or ""
            if len(timestamp) >= 10 and timestamp[:10] == day.isoformat():
                matching.append(snapshot)

        if not matching:
            continue

        snapshot = matching[0]
        usd = ((snapshot.get("quote") or {}).get("USD") or {})
        volume = usd.get("volume_24h")

        if volume is None:
            continue

        output_rows[coin_id] = {
            "cmc_id": coin_id,
            "name": asset_data.get("name") or "",
            "symbol": asset_data.get("symbol") or "",
            "timestamp_utc": snapshot.get("timestamp") or "",
            "volume_24h_usd": volume,
            "price_usd": usd.get("price", ""),
            "market_cap_usd": usd.get("market_cap", ""),
        }

    return output_rows, credits


def save_day(path: Path, rows_by_id: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows_by_id.values(), key=lambda row: int(row["cmc_id"]))
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(ordered)


def count_all_rows():
    total = 0
    files = sorted(DAILY_DIR.glob("????-??-??.csv"))
    for path in files:
        with path.open("r", newline="", encoding="utf-8") as file:
            row_count = sum(1 for _ in csv.DictReader(file))
        total += row_count
    return len(files), total


def update_coverage_summary(last_date: date, listing_credits: int, historical_credits: int, run_records: int):
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    if SUMMARY_PATH.exists():
        try:
            summary = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            summary = {}
    else:
        summary = {}

    daily_files, total_points = count_all_rows()
    dates = []
    for path in DAILY_DIR.glob("????-??-??.csv"):
        try:
            dates.append(date.fromisoformat(path.stem))
        except ValueError:
            pass

    summary.update(
        {
            "last_daily_update_utc": datetime.now(timezone.utc).isoformat(),
            "last_daily_update_date": last_date.isoformat(),
            "last_daily_update_records_added_or_refreshed": run_records,
            "last_daily_update_listing_credits_reported": listing_credits,
            "last_daily_update_historical_credits_reported": historical_credits,
            "daily_files_created": daily_files,
            "total_historical_data_points": total_points,
            "first_date": min(dates).isoformat() if dates else summary.get("first_date"),
            "last_date": max(dates).isoformat() if dates else last_date.isoformat(),
            "note": (
                "volume_24h_usd is the 24-hour rolling-volume quote at the first quote "
                "of each UTC day, not an exchange candle's standalone daily volume."
            ),
        }
    )
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    if not API_KEY:
        raise RuntimeError("CMC_API_KEY GitHub Secret bulunamadi.")

    latest_stored = find_latest_stored_date()
    dates_to_fill = get_dates_to_fill(latest_stored)

    print("GUNLUK HACIM VERISI GUNCELLEME BASLADI")
    print("Son kayitli UTC gunu:", latest_stored.isoformat())

    if not dates_to_fill:
        print("Yeni tamamlanmis UTC gunu yok. Veri zaten guncel.")
        return

    print("Tamamlanacak gun sayisi:", len(dates_to_fill))
    assets, listing_credits = fetch_current_top_coins()

    all_historical_credits = 0
    all_records_written = 0
    last_processed_date = latest_stored

    for day in dates_to_fill:
        print(f"\n{day.isoformat()} UTC gunu isleniyor...")
        path, rows_by_id = load_existing_day(day)
        requested_ids = {asset["id"] for asset in assets}
        received_ids = set()
        batch_count = (len(assets) + BATCH_SIZE - 1) // BATCH_SIZE
        day_credits = 0

        for batch_no, start in enumerate(range(0, len(assets), BATCH_SIZE), start=1):
            batch = assets[start:start + BATCH_SIZE]
            print(f"Paket {batch_no}/{batch_count}: {len(batch)} coin")
            batch_rows, credits = fetch_day_for_coin_batch(day, batch)
            day_credits += credits
            received_ids.update(batch_rows.keys())
            rows_by_id.update(batch_rows)

            if batch_no < batch_count:
                time.sleep(REQUEST_PAUSE_SECONDS)

        if not received_ids:
            raise RuntimeError(
                f"{day.isoformat()} icin hic tarihsel kayit donmedi. CSV dosyasi kaydedilmedi."
            )

        save_day(path, rows_by_id)
        missing_count = len(requested_ids - received_ids)
        day_credits_total = day_credits
        all_historical_credits += day_credits_total
        all_records_written += len(received_ids)
        last_processed_date = day

        print("Gelen coin kaydi:", len(received_ids))
        print("Verisi gelmeyen coin:", missing_count)
        print("Kaydedilen toplam satir:", len(rows_by_id))
        print("Bu gunun bildirilen kredisi:", day_credits_total)
        print("Dosya:", path)

    update_coverage_summary(
        last_processed_date,
        listing_credits,
        all_historical_credits,
        all_records_written,
    )

    print("\nGUNLUK GUNCELLEME TAMAMLANDI")
    print("Son tamamlanan gun:", last_processed_date.isoformat())
    print("Guncellenen veri noktasi:", all_records_written)
    print("Listeleme kredisi:", listing_credits)
    print("Gecmis veri kredisi:", all_historical_credits)
    print("Ozet dosyasi: data/coverage.json")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("GUNCELLEME HATASI:", str(error))
        sys.exit(1)


import os
import sys
import requests

API_KEY = os.getenv("CMC_API_KEY")

BASE_URL = "https://pro-api.coinmarketcap.com"

HEADERS = {
    "Accept": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY or ""
}


def get_data(name, endpoint, params):
    if not API_KEY:
        print("HATA: CMC_API_KEY bulunamadi!")
        sys.exit(1)

    url = BASE_URL + endpoint

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            params=params,
            timeout=30
        )

        print(f"\n{name} - HTTP: {response.status_code}")

        try:
            result = response.json()
        except ValueError:
            print("HATA: API JSON cevabi vermedi.")
            sys.exit(1)

        status = result.get("status") or {}
        error_code = status.get("error_code", 0)

        if response.status_code != 200 or error_code not in (0, "0"):
            print("CMC hata kodu:", error_code)
            print("CMC hata mesaji:", status.get("error_message"))
            sys.exit(1)

        print("Kullanilan kredi:", status.get("credit_count"))
        return result

    except requests.RequestException as error:
        print("Baglanti hatasi:", error)
        sys.exit(1)


def main():

    print("COINMARKETCAP TESTI BASLADI")

    # 1. Guncel coin verisini test et
    latest = get_data(
        "GUNCEL VERI",
        "/v3/cryptocurrency/listings/latest",
        {
            "limit": 5,
            "convert": "USD"
        }
    )

    coins = latest.get("data", [])

    if not coins:
        print("HATA: Coin listesi bos!")
        sys.exit(1)

    print("\nILK 5 COIN:")

    for coin in coins:
        quotes = coin.get("quote", [])

        if not quotes:
            continue

        usd = quotes[0]

        print(
            coin["name"],
            coin["symbol"],
            "Fiyat:", round(usd["price"], 6),
            "24s Hacim:", round(usd["volume_24h"], 2)
        )

    # 2. Bitcoin gecmis verisini test et
    history = get_data(
        "GECMIS VERI",
        "/v3/cryptocurrency/quotes/historical",
        {
            "id": "1",
            "count": 7,
            "interval": "daily",
            "convert": "USD"
        }
    )

    history_data = history.get("data", [])

    if not history_data:
        print("HATA: Gecmis veri bulunamadi!")
        sys.exit(1)

    btc = history_data[0]
    records = btc.get("quotes", [])

    if not records:
        print("HATA: Bitcoin gecmis hacim kaydi bos!")
        sys.exit(1)

    print("\nBITCOIN GECMIS HACIM:")

    for item in records:
        usd = item["quote"]["USD"]

        print(
            item["timestamp"],
            "Hacim:",
            round(usd["volume_24h"], 2)
        )

    print("\nBASARILI!")
    print("CMC API BAGLANTISI CALISIYOR.")
    print("GECMIS HACIM VERISI ALINIYOR.")


if __name__ == "__main__":
    main()

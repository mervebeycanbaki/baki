
import os
import sys
import requests

API_KEY = os.getenv("CMC_API_KEY")

if not API_KEY:
    print("HATA: CMC_API_KEY bulunamadi!")
    sys.exit(1)

url = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/listings/latest"

headers = {
    "Accept": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY
}

params = {
    "limit": 5,
    "convert": "USD"
}

print("CoinMarketCap baglantisi test ediliyor...")

try:
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30
    )

    print("HTTP Durum:", response.status_code)

    data = response.json()
    status = data.get("status") or {}

    if response.status_code != 200 or str(status.get("error_code", 0)) != "0":
        print("API HATA KODU:", status.get("error_code"))
        print("API HATA MESAJI:", status.get("error_message"))
        sys.exit(1)

    coins = data.get("data", [])

    if not coins:
        print("Coin listesi bos!")
        sys.exit(1)

    print("\nCOIN LISTESI BASARILI:")

    for coin in coins:
        usd = coin["quote"][0]

        print(
            coin["symbol"],
            "| Fiyat:", round(usd["price"], 6),
            "| 24s Hacim:", round(usd["volume_24h"], 2)
        )

    print("\nBASARILI! CMC API BAGLANTISI CALISIYOR.")

except requests.RequestException as error:
    print("BAGLANTI HATASI:", error)
    sys.exit(1)

except Exception as error:
    print("BEKLENMEYEN HATA:", repr(error))
    sys.exit(1)

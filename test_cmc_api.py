
import os
import sys
import requests

API_KEY = os.getenv("CMC_API_KEY")

if not API_KEY:
    print("API KEY BULUNAMADI!")
    sys.exit(1)

url = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/historical"

headers = {
    "Accept": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY
}

params = {
    "id": "1",
    "count": 7,
    "interval": "daily",
    "convert": "USD"
}

print("BITCOIN GECMIS HACIM TESTI BASLADI")

try:
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=30
    )

    print("HTTP:", response.status_code)

    result = response.json()
    status = result.get("status") or {}

    if response.status_code != 200 or str(status.get("error_code", 0)) != "0":
        print("API HATA:", status.get("error_code"))
        print("ACIKLAMA:", status.get("error_message"))
        sys.exit(1)

    btc = result.get("data", {}).get("1", {})
    quotes = btc.get("quotes", [])

    if not quotes:
        print("GECMIS VERI BULUNAMADI!")
        sys.exit(1)

    print("\nBITCOIN GUNLUK HACIMLARI:")

    for item in quotes:
        usd = item["quote"]["USD"]

        print(
            item["timestamp"],
            "| Hacim:",
            round(usd["volume_24h"], 2),
            "USD"
        )

    print("\nBASARILI!")
    print("GECMIS HACIM VERISI ALINDI.")

except Exception as error:
    print("HATA:", repr(error))
    sys.exit(1)

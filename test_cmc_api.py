
import os
import sys
import requests

API_KEY = os.getenv("CMC_API_KEY")

URL = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/historical"

HEADERS = {
    "Accept": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY or ""
}

PARAMS = {
    "id": "1,1027",
    "count": 365,
    "interval": "daily",
    "convert": "USD"
}

print("365 GUNLUK GECMIS VERI TESTI")

if not API_KEY:
    print("API KEY BULUNAMADI")
    sys.exit(1)

try:
    response = requests.get(
        URL,
        headers=HEADERS,
        params=PARAMS,
        timeout=120
    )

    print("HTTP:", response.status_code)

    result = response.json()
    status = result.get("status") or {}

    if response.status_code != 200 or str(status.get("error_code", 0)) != "0":
        print("API HATASI:", status.get("error_code"))
        print("ACIKLAMA:", status.get("error_message"))
        sys.exit(1)

    print("KREDI:", status.get("credit_count"))

    assets = result.get("data", {})

    for coin_id, name in [("1", "BITCOIN"), ("1027", "ETHEREUM")]:

        coin = assets.get(coin_id)

        if not coin:
            print(name, "BULUNAMADI")
            sys.exit(1)

        quotes = coin.get("quotes", [])

        print("\n", name)
        print("Gelen gunluk kayit:", len(quotes))

        if len(quotes) < 350:
            print("UYARI: 365 gunluk veri tam gelmedi.")
            sys.exit(1)

        volumes = [
            item["quote"]["USD"]["volume_24h"]
            for item in quotes
        ]

        average = sum(volumes) / len(volumes)

        print("Ortalama 24s hacim:", round(average, 2))
        print("En son kayit:", quotes[-1]["timestamp"])

    print("\nBASARILI!")
    print("IKI COIN ICIN 1 YILLIK VERI ALINDI.")

except Exception as error:
    print("HATA:", repr(error))
    sys.exit(1)

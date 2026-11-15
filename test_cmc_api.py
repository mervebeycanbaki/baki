
import os
import sys
import requests

API_KEY = os.getenv("CMC_API_KEY")

if not API_KEY:
    print("API KEY BULUNAMADI")
    sys.exit(1)

url = "https://pro-api.coinmarketcap.com/v3/cryptocurrency/quotes/historical"

headers = {
    "Accept": "application/json",
    "X-CMC_PRO_API_KEY": API_KEY
}

params = {
    "id": "1,1027",
    "count": 7,
    "interval": "daily",
    "convert": "USD"
}

print("COKLU COIN GECMIS VERI TESTI")

try:
    response = requests.get(
        url,
        headers=headers,
        params=params,
        timeout=60
    )

    print("HTTP:", response.status_code)

    result = response.json()
    status = result.get("status") or {}

    if response.status_code != 200 or str(status.get("error_code", 0)) != "0":
        print("API HATASI:", status.get("error_message"))
        sys.exit(1)

    print("Kullanilan kredi:", status.get("credit_count"))

    assets = result.get("data", {})

    for coin_id, name in [("1", "BITCOIN"), ("1027", "ETHEREUM")]:

        asset = assets.get(coin_id)

        if not asset:
            print(name, "VERISI BULUNAMADI")
            sys.exit(1)

        quotes = asset.get("quotes", [])

        if not quotes:
            print(name, "GECMIS VERISI BOS")
            sys.exit(1)

        print("\n", name)

        for item in quotes:
            usd = item["quote"]["USD"]

            print(
                item["timestamp"][:10],
                "24s Hacim:",
                round(usd["volume_24h"], 2),
                "USD"
            )

    print("\nBASARILI!")
    print("IKI COIN TEK ISTEKTE OKUNDU.")

except Exception as error:
    print("HATA:", repr(error))
    sys.exit(1)

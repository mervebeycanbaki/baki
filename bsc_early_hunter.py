
import requests
import time
from datetime import datetime, timezone

BASE_URL = "https://api.dexscreener.com"

# İlk test filtreleri
MIN_LIQUIDITY_USD = 5000
MIN_VOLUME_5M_USD = 1000
MIN_TXNS_5M = 10
MAX_TOKEN_AGE_DAYS = 7


ef get_latest_tokens():
    endpoints = [
        f"{BASE_URL}/token-boosts/latest/v1",
        f"{BASE_URL}/token-boosts/top/v1"
    ]

    tokens = []
    seen = set()

    for url in endpoints:
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()

            data = response.json()

            if not isinstance(data, list):
                continue

            for item in data:
                if item.get("chainId") != "bsc":
                    continue

                address = item.get("tokenAddress")

                if address and address.lower() not in seen:
                    seen.add(address.lower())
                    tokens.append(address)

            print("Kaynak:", url)
            print("Bulunan BSC adresi:", len(tokens))

        except Exception as e:
            print("Kaynak hatası:", url, e)

    return tokens

    for item in data:
        if item.get("chainId") != "bsc":
            continue

        address = item.get("tokenAddress")

        if address and address not in seen:
            seen.add(address)
            tokens.append(address)

    return tokens


def get_token_pairs(addresses):
    results = []

    # API çağrılarında 30 tokenlık gruplar
    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]

        url = f"{BASE_URL}/tokens/v1/bsc/{','.join(batch)}"

        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                results.extend(data)

        except Exception as e:
            print("Veri grubu alınamadı:", e)

        time.sleep(0.3)

    return results


def calculate_score(pair):
    score = 0

    liquidity = (pair.get("liquidity") or {}).get("usd", 0) or 0
    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}

    vol5m = volume.get("m5", 0) or 0
    tx5m = txns.get("m5") or {}
    buys = tx5m.get("buys", 0) or 0
    sells = tx5m.get("sells", 0) or 0

    total_txns = buys + sells

    if liquidity >= 5000:
        score += 15

    if liquidity >= 20000:
        score += 10

    if vol5m >= 1000:
        score += 15

    if vol5m >= 5000:
        score += 10

    if total_txns >= 10:
        score += 10

    if total_txns >= 30:
        score += 10

    if buys > sells:
        score += 10

    if total_txns > 0 and buys / total_txns >= 0.65:
        score += 10

    change5m = price_change.get("m5", 0) or 0

    if 0 < change5m <= 15:
        score += 5

    if vol5m > 0 and total_txns > 0:
        avg_trade = vol5m / total_txns

        if avg_trade < 1000:
            score += 5

    return min(score, 100)


def analyze_pairs(pairs):
    candidates = []
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    for pair in pairs:

        if pair.get("chainId") != "bsc":
            continue

        liquidity = (pair.get("liquidity") or {}).get("usd", 0) or 0
        volume = pair.get("volume") or {}
        txns = pair.get("txns") or {}

        vol5m = volume.get("m5", 0) or 0
        tx5m = txns.get("m5") or {}

        buys = tx5m.get("buys", 0) or 0
        sells = tx5m.get("sells", 0) or 0

        total_txns = buys + sells

        created = pair.get("pairCreatedAt")

        if not created:
            continue

        age_hours = (now_ms - created) / 3600000

        if age_hours < 0:
            continue

        if age_hours > MAX_TOKEN_AGE_DAYS * 24:
            continue

        if liquidity < MIN_LIQUIDITY_USD:
            continue

        if vol5m < MIN_VOLUME_5M_USD:
            continue

        if total_txns < MIN_TXNS_5M:
            continue

        score = calculate_score(pair)

        candidates.append({
            "pair": pair,
            "score": score,
            "age_hours": age_hours,
            "liquidity": liquidity,
            "volume5m": vol5m,
            "buys": buys,
            "sells": sells
        })

    candidates.sort(key=lambda x: x["score"], reverse=True)

    return candidates


def main():
    print("=" * 50)
    print("BSC ERKEN COIN AVCISI")
    print("Tarama:", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("=" * 50)

    try:
        tokens = get_latest_tokens()

        print("Bulunan BSC token adresi:", len(tokens))

        if not tokens:
            print("Token bulunamadı.")
            return

        pairs = get_token_pairs(tokens)

        print("Toplam parite:", len(pairs))

        candidates = analyze_pairs(pairs)

        print("Filtreyi geçen parite:", len(candidates))
        print()

        for item in candidates[:20]:

            pair = item["pair"]
            base = pair.get("baseToken") or {}
            quote = pair.get("quoteToken") or {}

            print("-" * 50)
            print("TOKEN:", base.get("name", "Unknown"))
            print("SYMBOL:", base.get("symbol", "?"))
            print("SKOR:", item["score"], "/100")
            print("YAŞ:", round(item["age_hours"], 2), "saat")
            print("LIKIDITE: $", round(item["liquidity"], 2))
            print("5 DK HACIM: $", round(item["volume5m"], 2))
            print("5 DK ALIM:", item["buys"])
            print("5 DK SATIM:", item["sells"])
            print("KONTRAT:", base.get("address", ""))
            print("DEX:", pair.get("dexId", ""))
            print("PARITE:", quote.get("symbol", "?"))
            print("LINK:", pair.get("url", ""))
            print()

    except Exception as e:
        print("TARAMA HATASI:", str(e))

    print("=" * 50)
    print("TEST TARAMASI TAMAMLANDI")


if __name__ == "__main__":
    main()

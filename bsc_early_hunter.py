
import requests
import time
import os
import json
from pathlib import Path
from datetime import datetime, timezone

BASE_URL = "https://api.dexscreener.com"

# İlk test filtreleri
MIN_LIQUIDITY_USD = 5000
MIN_VOLUME_5M_USD = 1000
MIN_TXNS_5M = 10
MAX_TOKEN_AGE_DAYS = 7
def send_telegram(message):
    bot_token = os.getenv("BSC_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("BSC_TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("TELEGRAM HATASI: Token veya Chat ID bulunamadi.")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": message,
                "disable_web_page_preview": True
            },
            timeout=20
        )

        response.raise_for_status()
        result = response.json()

        if result.get("ok"):
            print("TELEGRAM: Mesaj basariyla gonderildi.")
            return True

        print("TELEGRAM HATASI:", result)
        return False

    except Exception as e:
        print("TELEGRAM GONDERIM HATASI:", str(e))
        return False



def get_latest_tokens():
    tokens = []
    seen = set()

    def add_token(address):
        if not isinstance(address, str):
            return

        address = address.strip()

        if not address.startswith("0x") or len(address) != 42:
            return

        key = address.lower()

        if key not in seen:
            seen.add(key)
            tokens.append(address)

    # 1. DexScreener kaynaklari
    endpoints = [
        f"{BASE_URL}/token-profiles/latest/v1",
        f"{BASE_URL}/token-boosts/latest/v1",
        f"{BASE_URL}/token-boosts/top/v1"
    ]

    for url in endpoints:
        try:
            response = requests.get(url, timeout=20)
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                for item in data:
                    if item.get("chainId") == "bsc":
                        add_token(item.get("tokenAddress"))

            print("DexScreener kaynagi tamamlandi:", url)

        except Exception as e:
            print("DexScreener kaynak hatasi:", e)

    # 2. GeckoTerminal BSC yeni havuzlar
    gecko_url = (
        "https://api.geckoterminal.com/api/v2/"
        "networks/bsc/new_pools"
    )

    headers = {
        "Accept": "application/json;version=20230203"
    }

    params = {
        "include": "base_token,quote_token",
        "page": 1
    }

    try:
        response = requests.get(
            gecko_url,
            headers=headers,
            params=params,
            timeout=25
        )

        response.raise_for_status()
        data = response.json()

        # Dahil edilen token bilgileri
        for item in data.get("included", []):
            if item.get("type") == "token":
                attributes = item.get("attributes") or {}
                address = attributes.get("address")

                if not address:
                    resource_id = item.get("id", "")
                    if "_" in resource_id:
                        address = resource_id.split("_", 1)[1]

                add_token(address)

        # Havuz iliskilerinden token adreslerini bul
        for pool in data.get("data", []):
            relationships = pool.get("relationships") or {}

            for token_type in ("base_token", "quote_token"):
                token_data = (
                    relationships.get(token_type, {}).get("data")
                )

                if not token_data:
                    continue

                token_id = token_data.get("id", "")

                if "_" in token_id:
                    address = token_id.split("_", 1)[1]
                    add_token(address)

        print("GeckoTerminal yeni havuz taramasi tamamlandi.")

    except Exception as e:
        print("GeckoTerminal kaynak hatasi:", e)

    print("Toplam benzersiz BSC token adresi:", len(tokens))

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




def get_goplus_security(address):
    url = "https://api.gopluslabs.io/api/v1/token_security/56"

    def failed(status, message):
        return {
            "status": status,
            "flags": [message],
            "data": None,
            "critical": False,
            "verified": False
        }

    try:
        response = requests.get(
            url,
            params={"contract_addresses": address.lower()},
            headers={"accept": "application/json"},
            timeout=20
        )

        response.raise_for_status()
        payload = response.json()

        if str(payload.get("code")) != "1":
            return failed(
                "API YANITI HATALI",
                "GoPlus güvenlik verisi doğrulanamadı"
            )

        result = payload.get("result") or {}
        info = None

        for key, value in result.items():
            if key.lower() == address.lower():
                info = value
                break

        if not isinstance(info, dict):
            return failed(
                "VERİ BULUNAMADI",
                "Token için GoPlus kaydı bulunamadı"
            )

        warnings = []
        critical = []

        if str(info.get("is_honeypot", "")) == "1":
            critical.append("HONEYPOT tespit edildi")

        if str(info.get("cannot_buy", "")) == "1":
            critical.append("Token satın alınamıyor")

        # Vergi oranlarını kontrol et
        try:
            sell_tax = float(info.get("sell_tax", ""))
            if sell_tax >= 0.50:
                critical.append(
                    f"Kritik satış vergisi: %{sell_tax * 100:.1f}"
                )
            elif sell_tax >= 0.20:
                warnings.append(
                    f"Yüksek satış vergisi: %{sell_tax * 100:.1f}"
                )
        except (ValueError, TypeError):
            warnings.append("Satış vergisi doğrulanamadı")

        try:
            buy_tax = float(info.get("buy_tax", ""))
            if buy_tax >= 0.20:
                warnings.append(
                    f"Yüksek alış vergisi: %{buy_tax * 100:.1f}"
                )
        except (ValueError, TypeError):
            warnings.append("Alış vergisi doğrulanamadı")

        risk_fields = {
            "cannot_sell_all": "Tek seferde tüm token satılamayabilir",
            "is_mintable": "Yeni token basma yetkisi var",
            "owner_change_balance": "Sahip bakiyeleri değiştirebilir",
            "hidden_owner": "Gizli sahiplik işareti",
            "can_take_back_ownership": "Sahiplik geri alınabilir",
            "slippage_modifiable": "Vergi oranı değiştirilebilir",
            "transfer_pausable": "Transferler durdurulabilir",
            "is_blacklisted": "Kara liste özelliği var"
        }

        for field, message in risk_fields.items():
            if str(info.get(field, "")) == "1":
                warnings.append(message)

        if str(info.get("is_open_source", "")) == "0":
            warnings.append("Kontrat kaynak kodu açık değil")

        if critical:
            status = "KRİTİK RİSK - ELE"
        elif warnings:
            status = "RİSK UYARISI"
        else:
            status = "BELİRGİN RİSK İŞARETİ BULUNMADI"

        return {
            "status": status,
            "flags": critical + warnings,
            "data": info,
            "critical": bool(critical),
            "verified": True
        }

    except requests.exceptions.HTTPError as e:
        return failed("GOPLUS HTTP HATASI", str(e))

    except Exception as e:
        return failed("GOPLUS KONTROL EDİLEMEDİ", str(e))





def analyze_pairs(pairs):
    # Her kontrat için yalnızca en iyi pariteyi sakla
    best_candidates = {}

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    security_cache = {}

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

        if age_hours < 0 or age_hours > MAX_TOKEN_AGE_DAYS * 24:
            continue

        if liquidity < MIN_LIQUIDITY_USD:
            continue

        if vol5m < MIN_VOLUME_5M_USD:
            continue

        if total_txns < MIN_TXNS_5M:
            continue

        base = pair.get("baseToken") or {}
        address = base.get("address", "")

        if not address:
            continue

        key = address.lower()

        # Aynı kontrat için güvenlik kontrolünü bir kez yap
        if key not in security_cache:
            security_cache[key] = get_goplus_security(address)
            time.sleep(2.1)

        security = security_cache[key]

        if not security.get("verified", False):
            print("GUVENLIK DOGRULANAMADI:", address)
            continue

        if security.get("critical", False):
            print("KRITIK RISK NEDENIYLE ELENDI:", address)
            continue

        score = calculate_score(pair)

        warning_count = len(security.get("flags") or [])
        score -= min(warning_count * 5, 25)
        score = max(0, score)

        candidate = {
            "pair": pair,
            "score": score,
            "age_hours": age_hours,
            "liquidity": liquidity,
            "volume5m": vol5m,
            "buys": buys,
            "sells": sells,
            "security": security
        }

        # Aynı kontratın en yüksek puanlı paritesini tut
        previous = best_candidates.get(key)

        if previous is None:
            best_candidates[key] = candidate

        elif (
            score > previous["score"]
            or (
                score == previous["score"]
                and liquidity > previous["liquidity"]
            )
        ):
            best_candidates[key] = candidate

    candidates = list(best_candidates.values())

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    print("Benzersiz kontrat sayisi:", len(candidates))

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

            security = item.get("security") or {}

            print("GOPLUS DURUM:", security.get("status", "Kontrol yok"))

            if security.get("flags"):
                print("GOPLUS UYARILARI:",
                      "; ".join(security["flags"]))

            risk_data = security.get("data") or {}

            print("HONEYPOT:",
                  risk_data.get("is_honeypot", "Bilinmiyor"))
            print()

    except Exception as e:
        print("TARAMA HATASI:", str(e))

    print("=" * 50)
    print("TEST TARAMASI TAMAMLANDI")


if __name__ == "__main__":
    send_telegram("TEST: BSC Erken Coin Avcisi Telegram baglantisi basarili!")
    main()

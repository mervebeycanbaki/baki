
import requests
import time
import os
import json
from pathlib import Path
from datetime import datetime, timezone

BASE_URL = "https://api.dexscreener.com"

# TARMA FILTRELERI
MIN_LIQUIDITY_USD = 5000
MIN_VOLUME_5M_USD = 1000
MIN_TXNS_5M = 10
MAX_TOKEN_AGE_DAYS = 7

# SINYAL AYARLARI
SIGNAL_HISTORY_FILE = Path("bsc_signal_history.json")
SCORE_ALERT_INCREASE = 10
MAX_ALERTS_PER_SCAN = 20
MIN_SIGNAL_SCORE = 50

# API AYARLARI
REQUEST_TIMEOUT = 25
GOPLUS_DELAY = 2.1


def send_telegram(message):
    bot_token = os.getenv("BSC_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("BSC_TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("TELEGRAM: Token veya Chat ID bulunamadi.")
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
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        if response.json().get("ok"):
            print("TELEGRAM: Mesaj gonderildi.")
            return True

        print("TELEGRAM: Gonderim basarisiz.")
        return False

    except Exception as e:
        print("TELEGRAM HATASI:", e)
        return False


def load_signal_history():
    if not SIGNAL_HISTORY_FILE.exists():
        return {}

    try:
        data = json.loads(
            SIGNAL_HISTORY_FILE.read_text(encoding="utf-8")
        )
        return data if isinstance(data, dict) else {}

    except Exception as e:
        print("GECMIS OKUNAMADI:", e)
        return {}


def save_signal_history(history):
    try:
        SIGNAL_HISTORY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        print("Sinyal gecmisi kaydedildi.")

    except Exception as e:
        print("GECMIS KAYDEDILEMEDI:", e)


def process_signals(candidates, history):
    alert_count = 0

    for item in candidates:
        if alert_count >= MAX_ALERTS_PER_SCAN:
            break

        pair = item["pair"]
        base = pair.get("baseToken") or {}
        security = item.get("security") or {}

        address = base.get("address", "")
        if not address:
            continue

        if not security.get("verified") or security.get("critical"):
            continue

        score = item["score"]

        if score < MIN_SIGNAL_SCORE:
            continue

        key = address.lower()
        previous = history.get(key, {})
        previous_score = previous.get("alert_score")

        is_new = previous_score is None

        score_increased = (
            previous_score is not None
            and score >= previous_score + SCORE_ALERT_INCREASE
        )

        if not is_new and not score_increased:
            continue

        flags = security.get("flags") or []

        warning_text = (
            "\n".join(f"⚠️ {x}" for x in flags)
            if flags else "Ek guvenlik uyarisi bulunmadi"
        )

        message = (
            "🚨 BSC ERKEN COIN SINYALI\n\n"
            f"🪙 Coin: {base.get('name', 'Unknown')}\n"
            f"🔖 Sembol: {base.get('symbol', '?')}\n"
            f"⭐ Skor: {score}/100\n"
            f"💧 Likidite: ${item['liquidity']:,.2f}\n"
            f"📊 5 DK Hacim: ${item['volume5m']:,.2f}\n"
            f"🟢 Alim: {item['buys']}\n"
            f"🔴 Satim: {item['sells']}\n"
            f"⏱️ Yas: {item['age_hours']:.2f} saat\n\n"
            "🔐 GUVENLIK: KONTROLLER GECTI\n"
            "✅ Alis vergisi: %0\n"
            "✅ Satis vergisi: %0\n"
            f"{warning_text}\n\n"
            f"📄 Kontrat:\n{address}\n\n"
            f"🔗 DexScreener: {pair.get('url', '')}\n"
            "⚠️ Erken uyaridir, yatirim tavsiyesi degildir.\n"
            "⚠️ Guvenlik kontrolleri kesin garanti saglamaz."
        )

        if send_telegram(message):
            history[key] = {
                "alert_score": score,
                "alert_at": datetime.now(timezone.utc).isoformat(),
                "name": base.get("name", "Unknown"),
                "symbol": base.get("symbol", "?")
            }

            alert_count += 1

        time.sleep(1)

    print("Gonderilen sinyal:", alert_count)


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

    endpoints = [
        f"{BASE_URL}/token-profiles/latest/v1",
        f"{BASE_URL}/token-boosts/latest/v1",
        f"{BASE_URL}/token-boosts/top/v1"
    ]

    for url in endpoints:
        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                for item in data:
                    if item.get("chainId") == "bsc":
                        add_token(item.get("tokenAddress"))

            print("DexScreener tamamlandi:", url)

        except Exception as e:
            print("DexScreener hatasi:", e)

        time.sleep(0.5)

    gecko_url = (
        "https://api.geckoterminal.com/api/v2/"
        "networks/bsc/new_pools"
    )

    headers = {
        "Accept": "application/json;version=20230203"
    }

    try:
        response = requests.get(
            gecko_url,
            headers=headers,
            params={
                "include": "base_token,quote_token",
                "page": 1
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()
        data = response.json()

        for item in data.get("included", []):
            if item.get("type") == "token":
                attributes = item.get("attributes") or {}
                address = attributes.get("address")

                if not address:
                    resource_id = item.get("id", "")
                    if "_" in resource_id:
                        address = resource_id.split("_", 1)[1]

                add_token(address)

        for pool in data.get("data", []):
            relationships = pool.get("relationships") or {}

            for token_type in ("base_token", "quote_token"):
                token_data = (
                    relationships.get(token_type, {}).get("data")
                )

                if token_data:
                    token_id = token_data.get("id", "")

                    if "_" in token_id:
                        add_token(token_id.split("_", 1)[1])

        print("GeckoTerminal tamamlandi.")

    except Exception as e:
        print("GeckoTerminal hatasi:", e)

    print("Benzersiz BSC adresi:", len(tokens))

    return tokens


def get_token_pairs(addresses):
    results = []

    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]

        url = f"{BASE_URL}/tokens/v1/bsc/{','.join(batch)}"

        try:
            response = requests.get(url, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                results.extend(data)

        except Exception as e:
            print("Parite verisi hatasi:", e)

        time.sleep(0.5)

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
            "critical": True,
            "verified": False
        }

    try:
        response = requests.get(
            url,
            params={"contract_addresses": address.lower()},
            headers={"accept": "application/json"},
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()
        payload = response.json()

        if str(payload.get("code")) != "1":
            return failed(
                "API HATASI",
                "GoPlus guvenlik verisi dogrulanamadi"
            )

        result = payload.get("result") or {}
        info = None

        for key, value in result.items():
            if key.lower() == address.lower():
                info = value
                break

        if not isinstance(info, dict):
            return failed(
                "VERI YOK",
                "GoPlus token kaydi bulunamadi"
            )

        warnings = []
        critical = []

        # ALIS VE SATIS VERGISI: SIFIR TOLERANS

        for field, label in (
            ("buy_tax", "Alis"),
            ("sell_tax", "Satis")
        ):
            raw_tax = info.get(field)

            if raw_tax is None or raw_tax == "":
                critical.append(f"{label} vergisi dogrulanamadi")
                continue

            try:
                tax = float(raw_tax)

                if tax != 0:
                    critical.append(
                        f"{label} vergisi sifir degil: %{tax * 100:.4f}"
                    )

            except (ValueError, TypeError):
                critical.append(
                    f"{label} vergisi okunamadi"
                )

        # HONEYPOT VE SATIS KONTROLLERI

        if str(info.get("is_honeypot", "")) == "1":
            critical.append("HONEYPOT tespit edildi")

        if str(info.get("cannot_buy", "")) == "1":
            critical.append("Token satin alinamiyor")

        if str(info.get("cannot_sell_all", "")) == "1":
            critical.append("Tum tokenlar satilamayabilir")

        # DIGER KONTRAT RISKLERI

        risk_fields = {
            "is_mintable": "Yeni token basma yetkisi var",
            "owner_change_balance": "Sahip bakiyeleri degistirebilir",
            "hidden_owner": "Gizli sahiplik isareti",
            "can_take_back_ownership": "Sahiplik geri alinabilir",
            "slippage_modifiable": "Vergi orani degistirilebilir",
            "transfer_pausable": "Transferler durdurulabilir",
            "is_blacklisted": "Kara liste ozelligi var"
        }

        for field, message in risk_fields.items():
            if str(info.get(field, "")) == "1":
                warnings.append(message)

        if str(info.get("is_open_source", "")) == "0":
            warnings.append("Kontrat kaynak kodu acik degil")

        if critical:
            status = "KRITIK RISK - ELE"
        elif warnings:
            status = "RISK UYARISI"
        else:
            status = "KONTROLLER GECTI"

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
        return failed("GOPLUS HATASI", str(e))


def analyze_pairs(pairs):
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

        if key not in security_cache:
            security_cache[key] = get_goplus_security(address)
            time.sleep(GOPLUS_DELAY)

        security = security_cache[key]

        if not security.get("verified", False):
            print("GUVENLIK DOGRULANAMADI:", address)
            continue

        if security.get("critical", False):
            print(
                "VERGI VEYA KRITIK RISK NEDENIYLE ELENDI:",
                address
            )
            continue

        score = calculate_score(pair)

        warning_count = len(security.get("flags") or [])
        score = max(0, score - min(warning_count * 5, 25))

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

    print("Benzersiz kontrat:", len(candidates))

    return candidates


def main():
    history = load_signal_history()

    print("=" * 50)
    print("BSC ERKEN COIN AVCISI")
    print(
        "Tarama:",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    print("=" * 50)

    try:
        tokens = get_latest_tokens()

        print("Bulunan token:", len(tokens))

        if not tokens:
            print("Token bulunamadi.")
            return

        pairs = get_token_pairs(tokens)

        print("Toplam parite:", len(pairs))

        candidates = analyze_pairs(pairs)

        process_signals(candidates, history)

        save_signal_history(history)

        print("Filtreyi gecen parite:", len(candidates))

        for item in candidates[:20]:

            pair = item["pair"]
            base = pair.get("baseToken") or {}
            quote = pair.get("quoteToken") or {}

            print("-" * 50)
            print("TOKEN:", base.get("name", "Unknown"))
            print("SYMBOL:", base.get("symbol", "?"))
            print("SKOR:", item["score"], "/100")
            print("YAS:", round(item["age_hours"], 2), "saat")
            print("LIKIDITE: $", round(item["liquidity"], 2))
            print("5 DK HACIM: $", round(item["volume5m"], 2))
            print("5 DK ALIM:", item["buys"])
            print("5 DK SATIM:", item["sells"])
            print("KONTRAT:", base.get("address", ""))
            print("DEX:", pair.get("dexId", ""))
            print("PARITE:", quote.get("symbol", "?"))
            print("LINK:", pair.get("url", ""))

            security = item.get("security") or {}

            print("GOPLUS:", security.get("status"))

            if security.get("flags"):
                print("UYARILAR:", "; ".join(security["flags"]))

        print()

    except Exception as e:
        print("TARAMA HATASI:", str(e))

    print("=" * 50)
    print("TARAMA TAMAMLANDI")


if __name__ == "__main__":
    main()

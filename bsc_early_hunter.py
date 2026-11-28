
import requests
import time
import os
import json
from pathlib import Path
from datetime import datetime, timezone

BASE_URL = "https://api.dexscreener.com"
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/56"

# ILK FILTRELER
MIN_LIQUIDITY_USD = 5000
MIN_VOLUME_5M_USD = 1000
MIN_TXNS_5M = 10
MAX_TOKEN_AGE_DAYS = 7

# SINYAL AYARLARI
MIN_SIGNAL_SCORE = 50
SCORE_ALERT_INCREASE = 10
MAX_ALERTS_PER_SCAN = 20

# API AYARLARI
REQUEST_TIMEOUT = 25
GOPLUS_DELAY = 2.1

# DOSYALAR
SIGNAL_HISTORY_FILE = Path("bsc_signal_history.json")


def number(value, default=0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def send_telegram(message):
    bot_token = os.getenv("BSC_TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("BSC_TELEGRAM_CHAT_ID")

    if not bot_token or not chat_id:
        print("TELEGRAM HATASI: Token veya Chat ID eksik.")
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
        result = response.json()

        if result.get("ok"):
            print("TELEGRAM: Mesaj gonderildi.")
            return True

        print("TELEGRAM HATASI:", result)
        return False

    except Exception as e:
        print("TELEGRAM HATASI:", str(e))
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
        print("GECMIS OKUMA HATASI:", str(e))
        return {}


def save_signal_history(history):
    try:
        SIGNAL_HISTORY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

        print("Sinyal gecmisi kaydedildi.")

    except Exception as e:
        print("GECMIS KAYDETME HATASI:", str(e))


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
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                for item in data:
                    if item.get("chainId") == "bsc":
                        add_token(item.get("tokenAddress"))

            print("DexScreener tamamlandi:", url)

        except Exception as e:
            print("DexScreener hatasi:", str(e))

        time.sleep(0.5)

    gecko_url = (
        "https://api.geckoterminal.com/api/v2/"
        "networks/bsc/new_pools"
    )

    try:
        response = requests.get(
            gecko_url,
            headers={
                "Accept": "application/json;version=20230203"
            },
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
        print("GeckoTerminal hatasi:", str(e))

    print("Benzersiz BSC adresi:", len(tokens))

    return tokens


def get_token_pairs(addresses):
    results = []

    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]

        url = f"{BASE_URL}/tokens/v1/bsc/{','.join(batch)}"

        try:
            response = requests.get(
                url,
                timeout=REQUEST_TIMEOUT
            )
            response.raise_for_status()

            data = response.json()

            if isinstance(data, list):
                results.extend(data)

        except Exception as e:
            print("Parite verisi hatasi:", str(e))

        time.sleep(0.5)

    return results


def get_goplus_security(address):
    def failed(message):
        return {
            "verified": False,
            "critical": True,
            "status": "DOGRULANAMADI",
            "flags": [message],
            "data": None
        }

    try:
        response = requests.get(
            GOPLUS_URL,
            params={
                "contract_addresses": address.lower()
            },
            headers={
                "accept": "application/json"
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()
        payload = response.json()

        if str(payload.get("code")) != "1":
            return failed(
                "GoPlus API basarili cevap vermedi"
            )

        result = payload.get("result") or {}
        info = None

        for key, value in result.items():
            if key.lower() == address.lower():
                info = value
                break

        if not isinstance(info, dict):
            return failed(
                "GoPlus kontrat bilgisi bulunamadi"
            )

        critical = []
        warnings = []

        # ALIS VE SATIS VERGISI
        # IKISI DE KESINLIKLE %0 OLMALI

        tax_values = {}

        for field, label in (
            ("buy_tax", "ALIS"),
            ("sell_tax", "SATIS")
        ):
            raw_tax = info.get(field)

            if raw_tax is None or raw_tax == "":
                critical.append(
                    f"{label} VERGISI BILINMIYOR"
                )
                continue

            try:
                tax = float(raw_tax)
                tax_values[field] = tax

                if tax != 0:
                    critical.append(
                        f"{label} VERGISI SIFIR DEGIL: "
                        f"%{tax * 100:.4f}"
                    )

            except (ValueError, TypeError):
                critical.append(
                    f"{label} VERGISI OKUNAMADI"
                )

        # HONEYPOT VE ALIM-SATIM ENGELI

        if str(info.get("is_honeypot", "")) == "1":
            critical.append("HONEYPOT TESPIT EDILDI")

        if str(info.get("cannot_buy", "")) == "1":
            critical.append("TOKEN SATIN ALINAMIYOR")

        if str(info.get("cannot_sell_all", "")) == "1":
            critical.append("TOKENLARIN TAMAMI SATILAMAYABILIR")

        # DIGER KONTRAT RISKLERI

        risk_fields = {
            "is_mintable": "YENI TOKEN BASMA YETKISI VAR",
            "owner_change_balance": "SAHIP BAKIYE DEGISTIREBILIR",
            "hidden_owner": "GIZLI SAHIPLIK ISARETI",
            "can_take_back_ownership": "SAHIPLIK GERI ALINABILIR",
            "slippage_modifiable": "VERGI ORANI DEGISTIRILEBILIR",
            "transfer_pausable": "TRANSFERLER DURDURULABILIR",
            "is_blacklisted": "KARA LISTE OZELLIGI VAR"
        }

        for field, message in risk_fields.items():
            if str(info.get(field, "")) == "1":
                warnings.append(message)

        if str(info.get("is_open_source", "")) == "0":
            warnings.append(
                "KONTRAT KAYNAK KODU ACIK DEGIL"
            )

        if str(info.get("is_proxy", "")) == "1":
            warnings.append(
                "PROXY KONTRAT TESPIT EDILDI"
            )

        flags = critical + warnings

        if critical:
            status = "KRITIK RISK"
        elif warnings:
            status = "UYARI"
        else:
            status = "KONTROLLER GECTI"

        return {
            "verified": True,
            "critical": bool(critical),
            "status": status,
            "flags": flags,
            "data": info,
            "tax_values": tax_values
        }

    except requests.exceptions.HTTPError as e:
        return failed(f"GoPlus HTTP hatasi: {e}")

    except Exception as e:
        return failed(f"GoPlus hatasi: {e}")


def calculate_score(pair):
    score = 0

    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}

    vol5m = number(volume.get("m5"))

    tx5m = txns.get("m5") or {}

    buys = int(number(tx5m.get("buys")))
    sells = int(number(tx5m.get("sells")))
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

    change5m = number(price_change.get("m5"))

    if 0 < change5m <= 15:
        score += 5

    if vol5m > 0 and total_txns > 0:
        avg_trade = vol5m / total_txns

        if avg_trade < 1000:
            score += 5

    return min(score, 100)


def analyze_pairs(pairs):
    best_candidates = {}
    security_cache = {}

    now_ms = int(
        datetime.now(timezone.utc).timestamp() * 1000
    )

    stats = {
        "total_pairs": len(pairs),
        "non_bsc": 0,
        "missing_creation_date": 0,
        "too_old_or_invalid_age": 0,
        "low_liquidity": 0,
        "low_volume": 0,
        "low_transactions": 0,
        "missing_address": 0,
        "goplus_checked": 0,
        "goplus_unverified": 0,
        "security_rejected": 0,
        "security_passed": 0,
        "score_rejected": 0
    }

    print("\n========== ON FILTRE KONTROLU ==========")

    for pair in pairs:

        if pair.get("chainId") != "bsc":
            stats["non_bsc"] += 1
            continue

        base = pair.get("baseToken") or {}
        address = base.get("address", "")

        if not address:
            stats["missing_address"] += 1
            print("ELENDI: Kontrat adresi bulunamadi.")
            continue

        created = pair.get("pairCreatedAt")

        if not created:
            stats["missing_creation_date"] += 1
            print("ELENDI: Parite olusturma tarihi yok:", address)
            continue

        age_hours = (now_ms - created) / 3600000

        if age_hours < 0 or age_hours > MAX_TOKEN_AGE_DAYS * 24:
            stats["too_old_or_invalid_age"] += 1
            print(
                f"ELENDI: Yas siniri ({age_hours:.2f} saat):",
                address
            )
            continue

        liquidity = number(
            (pair.get("liquidity") or {}).get("usd")
        )

        if liquidity < MIN_LIQUIDITY_USD:
            stats["low_liquidity"] += 1
            print(
                f"ELENDI: Dusuk likidite (${liquidity:.2f}):",
                address
            )
            continue

        volume = pair.get("volume") or {}
        vol5m = number(volume.get("m5"))

        if vol5m < MIN_VOLUME_5M_USD:
            stats["low_volume"] += 1
            print(
                f"ELENDI: Dusuk 5 dakika hacmi (${vol5m:.2f}):",
                address
            )
            continue

        txns = pair.get("txns") or {}
        tx5m = txns.get("m5") or {}

        buys = int(number(tx5m.get("buys")))
        sells = int(number(tx5m.get("sells")))
        total_txns = buys + sells

        if total_txns < MIN_TXNS_5M:
            stats["low_transactions"] += 1
            print(
                f"ELENDI: Dusuk islem sayisi ({total_txns}):",
                address
            )
            continue

        # AYNI KONTRAT ICIN GOPLUS CAGRISINI TEKRARLAMA

        key = address.lower()

        if key not in security_cache:
            print("\nGOPLUS KONTROL EDILIYOR:", address)

            security_cache[key] = get_goplus_security(address)
            stats["goplus_checked"] += 1

            time.sleep(GOPLUS_DELAY)

        security = security_cache[key]

        if not security.get("verified", False):
            stats["goplus_unverified"] += 1

            print(
                "GOPLUS DOGRULANAMADI:",
                address,
                "|",
                " ; ".join(security.get("flags", []))
            )
            continue

        if security.get("critical", False):
            stats["security_rejected"] += 1

            print(
                "GUVENLIK NEDENIYLE ELENDI:",
                address,
                "|",
                " ; ".join(security.get("flags", []))
            )
            continue

        stats["security_passed"] += 1

        print(
            "GOPLUS GECTI:",
            address,
            "| Alis vergisi:",
            security.get("tax_values", {}).get("buy_tax"),
            "| Satis vergisi:",
            security.get("tax_values", {}).get("sell_tax")
        )

        score = calculate_score(pair)

        warnings = security.get("flags") or []
        score -= min(len(warnings) * 5, 25)
        score = max(0, score)

        if score < MIN_SIGNAL_SCORE:
            stats["score_rejected"] += 1

            print(
                f"ELENDI: Skor yetersiz ({score}):",
                address
            )
            continue

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

    print("\n========== FILTRE ISTATISTIKLERI ==========")

    for name, value in stats.items():
        print(f"{name}: {value}")

    print("Benzersiz kontrat:", len(candidates))
    print("============================================\n")

    return candidates


def process_signals(candidates, history):
    alert_count = 0

    for item in candidates:

        if alert_count >= MAX_ALERTS_PER_SCAN:
            break

        pair = item["pair"]
        base = pair.get("baseToken") or {}

        address = base.get("address", "")

        if not address:
            continue

        security = item.get("security") or {}

        # SON GUVENLIK KONTROLU

        if not security.get("verified"):
            print("SINYAL ENGELLENDI: Guvenlik dogrulanamadi.")
            continue

        if security.get("critical"):
            print("SINYAL ENGELLENDI: Kritik risk var.")
            continue

        tax_values = security.get("tax_values") or {}

        if (
            tax_values.get("buy_tax") != 0
            or tax_values.get("sell_tax") != 0
        ):
            print("SINYAL ENGELLENDI: Vergiler sifir degil.")
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
            print(
                "TEKRAR SINYAL ENGELLENDI:",
                base.get("symbol", "?"),
                "| Onceki skor:",
                previous_score,
                "| Yeni skor:",
                score
            )
            continue

        warnings = security.get("flags") or []

        warning_text = (
            "\n".join(f"⚠️ {x}" for x in warnings)
            if warnings else "Ek guvenlik uyarisi yok."
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
            "🔐 GUVENLIK KONTROLU\n"
            "✅ Alis vergisi: %0\n"
            "✅ Satis vergisi: %0\n"
            f"{warning_text}\n\n"
            f"📄 Kontrat:\n{address}\n\n"
            f"🔗 DexScreener: {pair.get('url', '')}\n\n"
            "⚠️ Guvenlik kontrolleri kesin garanti degildir.\n"
            "⚠️ Otomatik alim yapilmaz. Yatirim tavsiyesi degildir."
        )

        if send_telegram(message):
            history[key] = {
                "alert_score": score,
                "alert_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "name": base.get("name", "Unknown"),
                "symbol": base.get("symbol", "?")
            }

            alert_count += 1

        time.sleep(1)

    print("Gonderilen sinyal:", alert_count)


def main():
    print("=" * 55)
    print("BSC ERKEN COIN AVCISI")
    print(
        "Tarama:",
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    print("=" * 55)

    history = load_signal_history()

    try:
        tokens = get_latest_tokens()

        print("Bulunan token:", len(tokens))

        if not tokens:
            print("Yeni token bulunamadi.")
            return

        pairs = get_token_pairs(tokens)

        print("Toplam parite:", len(pairs))

        if not pairs:
            print("Parite verisi bulunamadi.")
            return

        candidates = analyze_pairs(pairs)

        print("Filtreyi gecen parite:", len(candidates))

        process_signals(candidates, history)

        save_signal_history(history)

        print("\n========== TARAMA SONU ==========")
        print("Toplam token adresi:", len(tokens))
        print("Toplam parite:", len(pairs))
        print("Filtreyi gecen:", len(candidates))
        print("TARAMA TAMAMLANDI")
        print("=================================")

    except Exception as e:
        print("TARAMA HATASI:", repr(e))
        raise


if __name__ == "__main__":
    main()

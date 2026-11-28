
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

BASE_URL = "https://api.dexscreener.com"
GECKO_URL = "https://api.geckoterminal.com/api/v2"
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/56"

# ON FILTRELER
MIN_LIQUIDITY_USD = 5000
MIN_VOLUME_5M_USD = 1000
MIN_TXNS_5M = 10
MAX_TOKEN_AGE_DAYS = 7

# API LIMITLERI
GECKO_NEW_POOL_PAGES = 3
MAX_TRADE_CHECKS_PER_SCAN = 3
REQUEST_TIMEOUT = 25
GOPLUS_DELAY = 2.1
GECKO_DELAY = 2.2

# SINYAL
MIN_SIGNAL_SCORE = 50
SCORE_ALERT_INCREASE = 10
MAX_ALERTS_PER_SCAN = 20
SCORE_VERSION = 3

SIGNAL_HISTORY_FILE = Path("bsc_signal_history.json")


def number(value, default=0.0):
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
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()

        result = response.json()

        if result.get("ok"):
            print("TELEGRAM: Mesaj gonderildi.")
            return True

        print("TELEGRAM HATASI:", result)
        return False

    except Exception as exc:
        print("TELEGRAM HATASI:", str(exc))
        return False


def load_signal_history():
    if not SIGNAL_HISTORY_FILE.exists():
        return {}

    try:
        data = json.loads(
            SIGNAL_HISTORY_FILE.read_text(encoding="utf-8")
        )
        return data if isinstance(data, dict) else {}

    except Exception as exc:
        print("GECMIS OKUMA HATASI:", str(exc))
        return {}


def save_signal_history(history):
    try:
        SIGNAL_HISTORY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("Sinyal gecmisi kaydedildi.")

    except Exception as exc:
        print("GECMIS KAYDETME HATASI:", str(exc))


# ==================================================
# TOKEN KESFI
# ==================================================

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
        f"{BASE_URL}/token-boosts/top/v1",
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

        except Exception as exc:
            print("DexScreener hatasi:", str(exc))

        time.sleep(0.5)

    gecko_url = f"{GECKO_URL}/networks/bsc/new_pools"

    headers = {
        "Accept": "application/json;version=20230203"
    }

    for page in range(1, GECKO_NEW_POOL_PAGES + 1):
        print(f"\nGeckoTerminal sayfa {page} taraniyor...")

        try:
            response = requests.get(
                gecko_url,
                headers=headers,
                params={
                    "include": "base_token,quote_token",
                    "page": page
                },
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            payload = response.json()
            pools = payload.get("data", [])

            for item in payload.get("included", []):
                if item.get("type") == "token":

                    attributes = item.get("attributes") or {}
                    address = attributes.get("address")

                    if not address:
                        resource_id = item.get("id", "")

                        if "_" in resource_id:
                            address = resource_id.split("_", 1)[1]

                    add_token(address)

            for pool in pools:
                relationships = pool.get("relationships") or {}

                for token_type in ("base_token", "quote_token"):
                    token_data = (
                        relationships.get(token_type, {}).get("data")
                    )

                    if token_data:
                        token_id = token_data.get("id", "")

                        if "_" in token_id:
                            add_token(token_id.split("_", 1)[1])

            print(
                f"GeckoTerminal sayfa {page} tamamlandi.",
                "| Havuz:", len(pools)
            )

        except Exception as exc:
            print(
                f"GeckoTerminal sayfa {page} hatasi:",
                str(exc)
            )

        time.sleep(GECKO_DELAY)

    print("\nToplam benzersiz BSC adresi:", len(tokens))

    return tokens


# ==================================================
# PARITE VERILERI
# ==================================================

def get_token_pairs(addresses):
    results = []

    for index in range(0, len(addresses), 30):
        batch = addresses[index:index + 30]

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

        except Exception as exc:
            print("Parite verisi hatasi:", str(exc))

        time.sleep(0.5)

    return results


# ==================================================
# GOPLUS GUVENLIK
# ==================================================

def get_goplus_security(address):

    def failed(message):
        return {
            "verified": False,
            "critical": True,
            "status": "DOGRULANAMADI",
            "flags": [message],
            "tax_values": {},
        }

    try:
        response = requests.get(
            GOPLUS_URL,
            params={
                "contract_addresses": address.lower()
            },
            headers={"accept": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

        if str(payload.get("code")) != "1":
            return failed("GoPlus API basarili cevap vermedi")

        result = payload.get("result") or {}
        info = None

        for key, value in result.items():
            if key.lower() == address.lower():
                info = value
                break

        if not isinstance(info, dict):
            return failed("GoPlus kontrat bilgisi bulunamadi")

        critical = []
        warnings = []
        tax_values = {}

        # ALIS VE SATIS VERGISI SIFIR OLMALI

        for field, label in (
            ("buy_tax", "ALIS"),
            ("sell_tax", "SATIS")
        ):
            raw_tax = info.get(field)

            if raw_tax is None or raw_tax == "":
                critical.append(f"{label} VERGISI BILINMIYOR")
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
                critical.append(f"{label} VERGISI OKUNAMADI")

        # HONEYPOT

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
            "is_blacklisted": "KARA LISTE OZELLIGI VAR",
        }

        for field, message in risk_fields.items():
            if str(info.get(field, "")) == "1":
                warnings.append(message)

        if str(info.get("is_open_source", "")) == "0":
            warnings.append("KONTRAT KAYNAK KODU ACIK DEGIL")

        if str(info.get("is_proxy", "")) == "1":
            warnings.append("PROXY KONTRAT TESPIT EDILDI")

        flags = critical + warnings

        status = (
            "KRITIK RISK" if critical
            else "UYARI" if warnings
            else "KONTROLLER GECTI"
        )

        return {
            "verified": True,
            "critical": bool(critical),
            "status": status,
            "flags": flags,
            "tax_values": tax_values,
        }

    except Exception as exc:
        return failed(f"GoPlus hatasi: {str(exc)}")


# ==================================================
# BUYUK ALIM / SATIM VERISI
# ==================================================

def get_pool_trade_metrics(pair, liquidity):

    pair_address = pair.get("pairAddress")

    if not pair_address:
        return {
            "available": False,
            "error": "Havuz adresi bulunamadi"
        }

    url = (
        f"{GECKO_URL}/networks/bsc/"
        f"pools/{pair_address}/trades"
    )

    headers = {
        "Accept": "application/json;version=20230203"
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        payload = response.json()
        trades = payload.get("data", [])

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(minutes=5)

        buy_volume = 0.0
        sell_volume = 0.0

        large_buy_volume = 0.0
        large_buy_count = 0
        large_buy_wallets = set()

        recent_count = 0

        # BUYUK ALIM ESIGI
        # 250 DOLAR VEYA LIKIDITENIN %1'I

        large_threshold = max(
            250.0,
            liquidity * 0.01
        )

        for trade in trades:
            attributes = trade.get("attributes") or {}

            kind = str(
                attributes.get("kind", "")
            ).lower()

            volume_usd = number(
                attributes.get("volume_in_usd")
            )

            timestamp = attributes.get("block_timestamp")

            if not timestamp:
                continue

            try:
                trade_time = datetime.fromisoformat(
                    timestamp.replace("Z", "+00:00")
                )

                if trade_time.tzinfo is None:
                    trade_time = trade_time.replace(
                        tzinfo=timezone.utc
                    )

            except (ValueError, TypeError, AttributeError):
                continue

            if trade_time < cutoff or trade_time > now:
                continue

            if kind not in ("buy", "sell"):
                continue

            recent_count += 1

            if kind == "buy":
                buy_volume += volume_usd

                if volume_usd >= large_threshold:
                    large_buy_count += 1
                    large_buy_volume += volume_usd

                    wallet = attributes.get("tx_from_address")

                    if isinstance(wallet, str) and wallet:
                        large_buy_wallets.add(wallet.lower())

            else:
                sell_volume += volume_usd

        total_volume = buy_volume + sell_volume

        buy_share = (
            buy_volume / total_volume
            if total_volume > 0
            else None
        )

        return {
            "available": True,
            "recent_count": recent_count,
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "buy_share": buy_share,
            "large_threshold": large_threshold,
            "large_buy_count": large_buy_count,
            "large_buy_wallets": len(large_buy_wallets),
            "large_buy_volume": large_buy_volume,
        }

    except Exception as exc:
        print(
            "ISLEM DETAYI ALINAMADI:",
            pair_address,
            str(exc)
        )

        return {
            "available": False,
            "error": str(exc)
        }


# ==================================================
# PUANLAMA V3
# ==================================================

def calculate_score(pair, age_hours, trade_metrics=None):

    parts = {}
    score = 0

    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    volume = pair.get("volume") or {}
    txns = pair.get("txns") or {}
    price_change = pair.get("priceChange") or {}

    vol5m = number(volume.get("m5"))
    vol1h = number(volume.get("h1"))

    tx5m = txns.get("m5") or {}

    buys = int(number(tx5m.get("buys")))
    sells = int(number(tx5m.get("sells")))

    total_txns = buys + sells

    buy_ratio = (
        buys / total_txns
        if total_txns
        else 0.0
    )

    turnover = (
        vol5m / liquidity
        if liquidity > 0
        else 0.0
    )

    # 1. LIKIDITE - 15 PUAN

    if liquidity >= 100000:
        points = 15
    elif liquidity >= 50000:
        points = 13
    elif liquidity >= 20000:
        points = 10
    elif liquidity >= 10000:
        points = 8
    elif liquidity >= 5000:
        points = 5
    else:
        points = 0

    parts["liquidity"] = points
    score += points

    # 2. 5 DAKIKALIK HACIM - 10 PUAN

    if vol5m >= 30000:
        points = 10
    elif vol5m >= 15000:
        points = 8
    elif vol5m >= 5000:
        points = 6
    elif vol5m >= 2000:
        points = 4
    elif vol5m >= 1000:
        points = 2
    else:
        points = 0

    parts["volume"] = points
    score += points

    # 3. ISLEM YOGUNLUGU - 10 PUAN

    if total_txns >= 100:
        points = 10
    elif total_txns >= 50:
        points = 8
    elif total_txns >= 25:
        points = 6
    elif total_txns >= 10:
        points = 3
    else:
        points = 0

    parts["transactions"] = points
    score += points

    # 4. ALICI USTUNLUGU - 15 PUAN
    # AZ ISLEM VARSA PUANIN GUVENI DUSURULUR

    if buy_ratio >= 0.75:
        raw_buyer_points = 15
    elif buy_ratio >= 0.70:
        raw_buyer_points = 13
    elif buy_ratio >= 0.65:
        raw_buyer_points = 11
    elif buy_ratio >= 0.60:
        raw_buyer_points = 8
    elif buy_ratio >= 0.55:
        raw_buyer_points = 5
    elif buy_ratio >= 0.50:
        raw_buyer_points = 2
    else:
        raw_buyer_points = 0

    buyer_confidence = min(total_txns / 30.0, 1.0)

    buyer_points = round(
        raw_buyer_points * buyer_confidence
    )

    parts["buyer_balance"] = buyer_points
    score += buyer_points

    # 5. HACIM / LIKIDITE DENGESI - 10 PUAN

    if 0.02 <= turnover <= 0.25:
        points = 10
    elif (
        0.01 <= turnover < 0.02
        or 0.25 < turnover <= 0.40
    ):
        points = 6
    elif (
        0.005 <= turnover < 0.01
        or 0.40 < turnover <= 0.50
    ):
        points = 3
    elif 0 < turnover < 0.005:
        points = 1
    else:
        points = 0

    parts["turnover"] = points
    score += points

    # 6. HACIM IVMESI - 10 PUAN
    # YETERLI GECMIS YOKSA PUAN VERILMEZ

    age_minutes = age_hours * 60

    elapsed_windows = min(
        12,
        int(age_minutes // 5)
    )

    previous_windows = elapsed_windows - 1

    acceleration = None
    acceleration_points = 0

    if previous_windows >= 1 and vol1h > vol5m:

        previous_volume = max(
            vol1h - vol5m,
            0.0
        )

        previous_average = (
            previous_volume / previous_windows
        )

        if previous_average > 0:

            acceleration = (
                vol5m / previous_average
            )

            if acceleration >= 3:
                acceleration_points = 10
            elif acceleration >= 2:
                acceleration_points = 7
            elif acceleration >= 1.5:
                acceleration_points = 4
            elif acceleration >= 1.2:
                acceleration_points = 2

    parts["acceleration"] = acceleration_points
    score += acceleration_points

    # 7. GERCEK BUYUK ALIMLAR - 15 PUAN
    # SADECE ESIGI GECEN ISLEMLER VE FARKLI CUZDANLAR

    trade_metrics = trade_metrics or {}

    large_buy_count = int(
        trade_metrics.get("large_buy_count", 0) or 0
    )

    large_wallets = int(
        trade_metrics.get("large_buy_wallets", 0) or 0
    )

    if large_wallets >= 5:
        large_buy_points = 15
    elif large_wallets >= 3:
        large_buy_points = 12
    elif large_wallets >= 2:
        large_buy_points = 8
    elif large_wallets >= 1:
        large_buy_points = 4
    else:
        large_buy_points = 0

    if large_buy_count <= 0:
        large_buy_points = 0

    parts["large_buys"] = large_buy_points
    score += large_buy_points

    # 8. TOKEN YASI - 5 PUAN

    if age_minutes < 15:
        age_points = 2
    elif age_hours <= 1:
        age_points = 4
    elif age_hours <= 6:
        age_points = 5
    elif age_hours <= 24:
        age_points = 4
    elif age_hours <= 72:
        age_points = 3
    else:
        age_points = 1

    parts["age"] = age_points
    score += age_points

    # 9. FIYAT HAREKETI - 5 PUAN

    change5m = number(
        price_change.get("m5")
    )

    if 0 <= change5m <= 5:
        price_points = 5
    elif 5 < change5m <= 10:
        price_points = 3
    elif -5 <= change5m < 0:
        price_points = 2
    elif -10 <= change5m < -5:
        price_points = 1
    elif change5m > 20 or change5m < -10:
        price_points = -3
    else:
        price_points = 0

    parts["price"] = price_points
    score += price_points

    # 10. ISLEM HACMINDE ALICI PAYI - 5 PUAN
    # ISLEM ADEDINDEN AYRI BIR HACIM OLCUSUDUR

    trade_buy_share = trade_metrics.get("buy_share")

    trade_count = int(
        trade_metrics.get("recent_count", 0) or 0
    )

    raw_trade_points = 0
    trade_points = 0

    trade_confidence = min(
        trade_count / 30.0,
        1.0
    )

    if (
        trade_metrics.get("available")
        and trade_buy_share is not None
    ):

        if trade_buy_share >= 0.75:
            raw_trade_points = 5
        elif trade_buy_share >= 0.70:
            raw_trade_points = 4
        elif trade_buy_share >= 0.65:
            raw_trade_points = 3
        elif trade_buy_share >= 0.60:
            raw_trade_points = 2
        elif trade_buy_share >= 0.55:
            raw_trade_points = 1

        trade_points = round(
            raw_trade_points * trade_confidence
        )

    parts["trade_buy_pressure"] = trade_points
    score += trade_points

    # ASIRI HACIM / LIKIDITE RISKI

    turnover_penalty = (
        10 if turnover > 1
        else 5 if turnover > 0.50
        else 0
    )

    parts["turnover_penalty"] = -turnover_penalty
    score -= turnover_penalty

    score = max(
        0,
        min(score, 100)
    )

    return {
        "score": score,
        "parts": parts,
        "buy_ratio": buy_ratio,
        "buyer_confidence": buyer_confidence,
        "turnover": turnover,
        "acceleration": acceleration,
        "trade_metrics": trade_metrics,
        "trade_buy_share": trade_buy_share,
        "trade_confidence": trade_confidence,
    }


# ==================================================
# ADAYLARI ANALIZ ET
# ==================================================

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
        "score_rejected": 0,
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
            continue

        created = pair.get("pairCreatedAt")

        if not created:
            stats["missing_creation_date"] += 1
            print("ELENDI: Parite tarihi yok:", address)
            continue

        age_hours = (
            now_ms - created
        ) / 3600000

        if (
            age_hours < 0
            or age_hours > MAX_TOKEN_AGE_DAYS * 24
        ):

            stats["too_old_or_invalid_age"] += 1

            print(
                f"ELENDI: Yas ({age_hours:.2f} saat):",
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
                f"ELENDI: Dusuk hacim (${vol5m:.2f}):",
                address
            )

            continue

        tx5m = (
            pair.get("txns") or {}
        ).get("m5") or {}

        buys = int(number(tx5m.get("buys")))
        sells = int(number(tx5m.get("sells")))

        if buys + sells < MIN_TXNS_5M:

            stats["low_transactions"] += 1

            print(
                "ELENDI: Dusuk islem sayisi:",
                address
            )

            continue

        # AYNI KONTRAT ICIN TEK GOPLUS CAGIRISI

        key = address.lower()

        if key not in security_cache:

            print(
                "\nGOPLUS KONTROL EDILIYOR:",
                address
            )

            security_cache[key] = get_goplus_security(address)

            stats["goplus_checked"] += 1

            time.sleep(GOPLUS_DELAY)

        security = security_cache[key]

        if not security.get("verified"):

            stats["goplus_unverified"] += 1

            print(
                "GOPLUS DOGRULANAMADI:",
                address,
                "|",
                security.get("flags")
            )

            continue

        if security.get("critical"):

            stats["security_rejected"] += 1

            print(
                "GUVENLIK NEDENIYLE ELENDI:",
                address,
                "|",
                " ; ".join(security.get("flags", []))
            )

            continue

        taxes = security.get("tax_values") or {}

        if (
            taxes.get("buy_tax") != 0
            or taxes.get("sell_tax") != 0
        ):

            stats["security_rejected"] += 1
            print("GUVENLIK NEDENIYLE ELENDI: Vergi sifir degil:", address)
            continue

        stats["security_passed"] += 1

        candidate = {
            "pair": pair,
            "score": 0,
            "age_hours": age_hours,
            "liquidity": liquidity,
            "volume5m": vol5m,
            "buys": buys,
            "sells": sells,
            "security": security,
            "trade_metrics": {
                "available": False,
                "error": "Henuz kontrol edilmedi"
            },
        }

        previous = best_candidates.get(key)

        if (
            previous is None
            or liquidity > previous["liquidity"]
            or (
                liquidity == previous["liquidity"]
                and vol5m > previous["volume5m"]
            )
        ):

            best_candidates[key] = candidate

    candidates = list(best_candidates.values())

    # ILK PUANLAMA

    for item in candidates:

        result = calculate_score(
            item["pair"],
            item["age_hours"]
        )

        item["score"] = result["score"]
        item["score_details"] = result

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    # API KULLANIMINI SINIRLAMAK ICIN EN IYI 3 ADAY

    print("\n========== BUYUK ALIM KONTROLU ==========")

    for item in candidates[:MAX_TRADE_CHECKS_PER_SCAN]:

        pair = item["pair"]

        symbol = (
            pair.get("baseToken") or {}
        ).get("symbol", "?")

        print("ISLEM DETAYI KONTROL EDILIYOR:", symbol)

        metrics = get_pool_trade_metrics(
            pair,
            item["liquidity"]
        )

        item["trade_metrics"] = metrics

        if metrics.get("available"):

            print(
                "SON 5 DK ISLEM SAYISI:",
                metrics.get("recent_count", 0)
            )

            print(
                "5 DK ALIM HACMI:",
                round(metrics.get("buy_volume", 0), 2)
            )

            print(
                "5 DK SATIM HACMI:",
                round(metrics.get("sell_volume", 0), 2)
            )

            print(
                "BUYUK ALIM ESIGI:",
                round(metrics.get("large_threshold", 0), 2)
            )

            print(
                "BUYUK ALIM SAYISI:",
                metrics.get("large_buy_count", 0)
            )

            print(
                "BUYUK ALICI CUZDAN:",
                metrics.get("large_buy_wallets", 0)
            )

            print(
                "BUYUK ALIM TOPLAMI:",
                round(metrics.get("large_buy_volume", 0), 2)
            )

        else:

            print(
                "ISLEM VERISI ALINAMADI:",
                metrics.get("error")
            )

        time.sleep(GECKO_DELAY)

    # SON PUANLAMA

    for item in candidates:

        result = calculate_score(
            item["pair"],
            item["age_hours"],
            item["trade_metrics"]
        )

        item["score"] = result["score"]
        item["score_details"] = result

        if item["score"] < MIN_SIGNAL_SCORE:

            stats["score_rejected"] += 1

            symbol = (
                item["pair"].get("baseToken") or {}
            ).get("symbol", "?")

            print(
                "SKOR NEDENIYLE ELENDI:",
                symbol,
                "| Skor:",
                item["score"]
            )

    candidates = [
        item
        for item in candidates
        if item["score"] >= MIN_SIGNAL_SCORE
    ]

    candidates.sort(
        key=lambda item: item["score"],
        reverse=True
    )

    print("\n========== FILTRE ISTATISTIKLERI ==========")

    for name, value in stats.items():
        print(f"{name}: {value}")

    print("Benzersiz kontrat:", len(candidates))
    print("============================================")

    return candidates


# ==================================================
# TELEGRAM SINYAL GONDERIMI
# ==================================================

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
        taxes = security.get("tax_values") or {}

        if (
            not security.get("verified")
            or security.get("critical")
            or taxes.get("buy_tax") != 0
            or taxes.get("sell_tax") != 0
        ):

            print("SINYAL ENGELLENDI: Guvenlik/vergi kontrolu gecmedi.")
            continue

        score = item["score"]

        if score < MIN_SIGNAL_SCORE:
            continue

        key = address.lower()
        previous = history.get(key, {})

        previous_version = previous.get(
            "score_version",
            1
        )

        previous_score = previous.get("alert_score")

        # ESKI PUANLAMA GECMISINI YENI SURUME AKTAR
        # ESKI SINYALI TEKRAR GONDERME

        if (
            previous_score is not None
            and previous_version != SCORE_VERSION
        ):

            history[key] = {
                **previous,
                "alert_score": score,
                "score_version": SCORE_VERSION,
                "score_baseline_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }

            print(
                "PUANLAMA SURUMU GUNCELLENDI:",
                base.get("symbol", "?"),
                "| Yeni baz puan:",
                score
            )

            continue

        is_new = previous_score is None

        score_increased = (
            previous_score is not None
            and score >= previous_score + SCORE_ALERT_INCREASE
        )

        if not is_new and not score_increased:

            print(
                "TEKRAR SINYAL ENGELLENDI:",
                base.get("symbol", "?"),
                "| Onceki:", previous_score,
                "| Yeni:", score
            )

            continue

        details = item["score_details"]
        trade = item["trade_metrics"]

        buy_percent = details["buy_ratio"] * 100

        trade_buy_share = details.get("trade_buy_share")

        turnover_percent = details["turnover"] * 100

        buyer_confidence_percent = (
            details["buyer_confidence"] * 100
        )

        acceleration = details.get("acceleration")

        if acceleration is not None:
            acceleration_text = f"{acceleration:.2f} kat"
        else:
            acceleration_text = "Yeterli onceki 5 dk veri yok"

        if trade.get("available"):

            if trade_buy_share is None:
                trade_buy_text = "Hesaplanamadi"
            else:
                trade_buy_text = (
                    f"%{trade_buy_share * 100:.1f}"
                )

            trade_text = (
                f"Son 5 dk islem sayisi: "
                f"{trade.get('recent_count', 0)}\n"

                f"Son 5 dk alim hacmi: "
                f"${trade.get('buy_volume', 0):,.2f}\n"

                f"Son 5 dk satis hacmi: "
                f"${trade.get('sell_volume', 0):,.2f}\n"

                f"Islem bazli alim hacmi orani: "
                f"{trade_buy_text}\n"

                f"Buyuk alim esigi: "
                f"${trade.get('large_threshold', 0):,.2f}\n"

                f"Buyuk alim sayisi: "
                f"{trade.get('large_buy_count', 0)}\n"

                f"Buyuk alici cuzdan: "
                f"{trade.get('large_buy_wallets', 0)}\n"

                f"Buyuk alim toplami: "
                f"${trade.get('large_buy_volume', 0):,.2f}"
            )

        else:

            trade_text = (
                "Islem/buyuk alim verisi alinamadi: "
                + str(trade.get("error", "Bilinmiyor"))
            )

        warning_text = "\n".join(
            f"⚠️ {flag}"
            for flag in (security.get("flags") or [])
        ) or "Ek guvenlik uyarisi yok."

        message = (
            "🚨 BSC ERKEN COIN SINYALI - PUANLAMA V3\n\n"

            f"🪙 Coin: {base.get('name', 'Unknown')}\n"

            f"🔖 Sembol: {base.get('symbol', '?')}\n"

            f"⭐ Skor: {score}/100\n"

            f"💧 Likidite: ${item['liquidity']:,.2f}\n"

            f"📊 5 DK Hacim: ${item['volume5m']:,.2f}\n"

            f"🟢 Alim islemi: {item['buys']}\n"

            f"🔴 Satim islemi: {item['sells']}\n"

            f"📈 Alim islem orani: %{buy_percent:.1f}\n"

            f"🧪 Alim orani veri guveni: %{buyer_confidence_percent:.0f}\n"

            f"📊 Hacim/Likidite: %{turnover_percent:.2f}\n"

            f"🚀 Hacim ivmesi: {acceleration_text}\n"

            f"⏱️ Parite yasi: {item['age_hours']:.2f} saat\n\n"

            "🐋 BUYUK ALIM ANALIZI\n"

            f"{trade_text}\n\n"

            "🔐 GUVENLIK KONTROLU\n"

            "✅ Alis vergisi: %0\n"

            "✅ Satis vergisi: %0\n"

            f"{warning_text}\n\n"

            f"📄 Token kontrati:\n{address}\n\n"

            f"🔗 DexScreener: {pair.get('url', '')}\n\n"

            "⚠️ Guvenlik kontrolleri kesin garanti degildir.\n"

            "⚠️ Otomatik alim yapilmaz. Yatirim tavsiyesi degildir."
        )

        if send_telegram(message):

            history[key] = {
                "alert_score": score,
                "score_version": SCORE_VERSION,
                "alert_at": datetime.now(
                    timezone.utc
                ).isoformat(),
                "name": base.get("name", "Unknown"),
                "symbol": base.get("symbol", "?"),
            }

            alert_count += 1

        time.sleep(1)

    print("Gonderilen sinyal:", alert_count)


# ==================================================
# ADAY RAPORU
# ==================================================

def print_candidates(candidates):

    print("\n========== ADAY RAPORU ==========")

    for item in candidates[:20]:

        pair = item["pair"]
        base = pair.get("baseToken") or {}

        details = item["score_details"]
        trade = item["trade_metrics"]

        print("-" * 50)

        print("TOKEN:", base.get("name", "Unknown"))
        print("SYMBOL:", base.get("symbol", "?"))
        print("SKOR:", item["score"], "/100")

        print("LIKIDITE:", round(item["liquidity"], 2))
        print("5 DK HACIM:", round(item["volume5m"], 2))

        print("ALIM:", item["buys"])
        print("SATIM:", item["sells"])

        print(
            "ALIM ORANI:",
            round(details["buy_ratio"] * 100, 2),
            "%"
        )

        print(
            "ALIM ORANI VERI GUVENI:",
            round(details["buyer_confidence"] * 100),
            "%"
        )

        print(
            "HACIM/LIKIDITE:",
            round(details["turnover"] * 100, 2),
            "%"
        )

        print("HACIM IVMESI:", details["acceleration"])

        print(
            "ISLEM BAZLI ALIM HACMI ORANI:",
            details["trade_buy_share"]
        )

        print(
            "SON 5 DK ISLEM SAYISI:",
            trade.get("recent_count", "YOK")
        )

        print(
            "BUYUK ALIM SAYISI:",
            trade.get("large_buy_count", 0)
        )

        print(
            "BUYUK ALICI CUZDAN:",
            trade.get("large_buy_wallets", 0)
        )

        print("PUAN DAGILIMI:", details["parts"])

        print("KONTRAT:", base.get("address", ""))
        print("LINK:", pair.get("url", ""))


# ==================================================
# ANA PROGRAM
# ==================================================

def main():

    print("=" * 55)

    print("BSC ERKEN COIN AVCISI - PUANLAMA V3")

    print(
        "Tarama:",
        datetime.now(timezone.utc).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    )

    print("=" * 55)

    history = load_signal_history()

    try:

        tokens = get_latest_tokens()

        print("\nBulunan token:", len(tokens))

        if not tokens:
            print("Yeni token bulunamadi.")
            return

        pairs = get_token_pairs(tokens)

        print("Toplam parite:", len(pairs))

        if not pairs:
            print("Parite verisi bulunamadi.")
            return

        candidates = analyze_pairs(pairs)

        print_candidates(candidates)

        print(
            "\nTelegram icin aday sayisi:",
            len(candidates)
        )

        process_signals(
            candidates,
            history
        )

        save_signal_history(history)

        print("\n========== TARAMA SONU ==========")

        print("Toplam token:", len(tokens))
        print("Toplam parite:", len(pairs))
        print("Telegram adayi:", len(candidates))

        print("TARAMA TAMAMLANDI")
        print("=================================")

    except Exception as exc:

        print("TARAMA HATASI:", repr(exc))
        raise


if __name__ == "__main__":
    main()


import requests
import time
import os
import json

from pathlib import Path
from datetime import datetime, timezone, timedelta


BASE_URL = "https://api.dexscreener.com"
GECKO_URL = "https://api.geckoterminal.com/api/v2"
GOPLUS_URL = "https://api.gopluslabs.io/api/v1/token_security/56"

# TARAYICI FILTRELERI
MIN_LIQUIDITY_USD = 5000
MIN_VOLUME_5M_USD = 1000
MIN_TXNS_5M = 10
MAX_TOKEN_AGE_DAYS = 7

# GECKOTERMINAL
GECKO_NEW_POOL_PAGES = 3
MAX_TRADE_CHECKS_PER_SCAN = 3

# SINYAL
MIN_SIGNAL_SCORE = 50
SCORE_ALERT_INCREASE = 10
MAX_ALERTS_PER_SCAN = 20
SCORE_VERSION = 2

# API
REQUEST_TIMEOUT = 25
GOPLUS_DELAY = 2.1
GECKO_DELAY = 2.2

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
            json.dumps(
                history,
                ensure_ascii=False,
                indent=2
            ),
            encoding="utf-8"
        )

        print("Sinyal gecmisi kaydedildi.")

    except Exception as e:

        print("GECMIS KAYDETME HATASI:", str(e))


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

    # DEXSCREENER

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

                        add_token(
                            item.get("tokenAddress")
                        )

            print("DexScreener tamamlandi:", url)

        except Exception as e:

            print("DexScreener hatasi:", str(e))

        time.sleep(0.5)

    # GECKOTERMINAL - 3 SAYFA

    gecko_new_url = (
        f"{GECKO_URL}/networks/bsc/new_pools"
    )

    headers = {
        "Accept": "application/json;version=20230203"
    }

    for page in range(1, GECKO_NEW_POOL_PAGES + 1):

        print(f"\nGeckoTerminal sayfa {page} taraniyor...")

        try:

            response = requests.get(
                gecko_new_url,
                headers=headers,
                params={
                    "include": "base_token,quote_token",
                    "page": page
                },
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            pools = data.get("data", [])

            for item in data.get("included", []):

                if item.get("type") == "token":

                    attributes = item.get("attributes") or {}

                    address = attributes.get("address")

                    if not address:

                        resource_id = item.get("id", "")

                        if "_" in resource_id:

                            address = resource_id.split(
                                "_",
                                1
                            )[1]

                    add_token(address)

            for pool in pools:

                relationships = pool.get("relationships") or {}

                for token_type in (
                    "base_token",
                    "quote_token"
                ):

                    token_data = (
                        relationships.get(token_type, {}).get("data")
                    )

                    if token_data:

                        token_id = token_data.get("id", "")

                        if "_" in token_id:

                            add_token(
                                token_id.split("_", 1)[1]
                            )

            print(
                f"GeckoTerminal sayfa {page} tamamlandi.",
                "| Havuz:",
                len(pools)
            )

        except Exception as e:

            print(
                f"GeckoTerminal sayfa {page} hatasi:",
                str(e)
            )

        time.sleep(GECKO_DELAY)

    print("\nToplam benzersiz BSC adresi:", len(tokens))

    return tokens


# ==================================================
# PARITE VERILERI
# ==================================================

def get_token_pairs(addresses):

    results = []

    for i in range(0, len(addresses), 30):

        batch = addresses[i:i + 30]

        url = (
            f"{BASE_URL}/tokens/v1/bsc/"
            f"{','.join(batch)}"
        )

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
            "tax_values": {}
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
        tax_values = {}

        # ALIS VE SATIS VERGISI SIFIR OLMALI

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

        # HONEYPOT

        if str(info.get("is_honeypot", "")) == "1":

            critical.append("HONEYPOT TESPIT EDILDI")

        if str(info.get("cannot_buy", "")) == "1":

            critical.append("TOKEN SATIN ALINAMIYOR")

        if str(info.get("cannot_sell_all", "")) == "1":

            critical.append(
                "TOKENLARIN TAMAMI SATILAMAYABILIR"
            )

        # KONTRAT RISKLERI

        risk_fields = {

            "is_mintable":
                "YENI TOKEN BASMA YETKISI VAR",

            "owner_change_balance":
                "SAHIP BAKIYE DEGISTIREBILIR",

            "hidden_owner":
                "GIZLI SAHIPLIK ISARETI",

            "can_take_back_ownership":
                "SAHIPLIK GERI ALINABILIR",

            "slippage_modifiable":
                "VERGI ORANI DEGISTIRILEBILIR",

            "transfer_pausable":
                "TRANSFERLER DURDURULABILIR",

            "is_blacklisted":
                "KARA LISTE OZELLIGI VAR"
        }

        for field, message in risk_fields.items():

            if str(info.get(field, "")) == "1":

                warnings.append(message)

        if str(info.get("is_open_source", "")) == "0":

            warnings.append(
                "KONTRAT KAYNAK KODU ACIK DEGIL"
            )

        if str(info.get("is_proxy", "")) == "1":

            warnings.append("PROXY KONTRAT TESPIT EDILDI")

        return {

            "verified": True,

            "critical": bool(critical),

            "status": (
                "KRITIK RISK"
                if critical
                else "UYARI"
                if warnings
                else "KONTROLLER GECTI"
            ),

            "flags": critical + warnings,

            "tax_values": tax_values

        }

    except Exception as e:

        return failed(
            f"GoPlus hatasi: {str(e)}"
        )


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

        data = payload.get("data", [])

        now = datetime.now(timezone.utc)

        cutoff = now - timedelta(minutes=5)

        buy_volume = 0.0
        sell_volume = 0.0

        large_buy_volume = 0.0

        large_buy_count = 0

        large_buy_wallets = set()

        recent_count = 0

        # BUYUK ALIM ESIGI:
        # LIKIDITENIN %1'I VEYA EN AZ 250 USD

        large_threshold = max(
            250,
            liquidity * 0.01
        )

        for trade in data:

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

            except (ValueError, TypeError):

                continue

            # SON 5 DAKIKA

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

                    wallet = attributes.get(
                        "tx_from_address"
                    )

                    if wallet:

                        large_buy_wallets.add(
                            wallet.lower()
                        )

            elif kind == "sell":

                sell_volume += volume_usd

        total_volume = buy_volume + sell_volume

        buy_share = None

        if total_volume > 0:

            buy_share = (
                buy_volume / total_volume
            )

        return {

            "available": True,

            "recent_count": recent_count,

            "buy_volume": buy_volume,

            "sell_volume": sell_volume,

            "buy_share": buy_share,

            "large_threshold": large_threshold,

            "large_buy_count": large_buy_count,

            "large_buy_wallets": len(
                large_buy_wallets
            ),

            "large_buy_volume": large_buy_volume

        }

    except Exception as e:

        print(
            "ISLEM DETAYI ALINAMADI:",
            pair_address,
            str(e)
        )

        return {

            "available": False,

            "error": str(e)

        }


# ==================================================
# YENI PUANLAMA SISTEMI
# ==================================================

def calculate_score(pair, age_hours, trade_metrics=None):

    score = 0
    parts = {}

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
        if total_txns > 0
        else 0
    )

    turnover = (
        vol5m / liquidity
        if liquidity > 0
        else 0
    )

    # 1. LIKIDITE - 15 PUAN

    liquidity_points = 0

    if liquidity >= 100000:
        liquidity_points = 15

    elif liquidity >= 50000:
        liquidity_points = 13

    elif liquidity >= 20000:
        liquidity_points = 10

    elif liquidity >= 10000:
        liquidity_points = 8

    elif liquidity >= 5000:
        liquidity_points = 5

    parts["liquidity"] = liquidity_points

    score += liquidity_points

    # 2. 5 DAKIKA HACIM - 15 PUAN

    volume_points = 0

    if vol5m >= 30000:
        volume_points = 15

    elif vol5m >= 15000:
        volume_points = 12

    elif vol5m >= 5000:
        volume_points = 9

    elif vol5m >= 2000:
        volume_points = 6

    elif vol5m >= 1000:
        volume_points = 3

    parts["volume"] = volume_points

    score += volume_points

    # 3. ISLEM YOGUNLUGU - 10 PUAN

    transaction_points = 0

    if total_txns >= 100:
        transaction_points = 10

    elif total_txns >= 50:
        transaction_points = 8

    elif total_txns >= 25:
        transaction_points = 6

    elif total_txns >= 10:
        transaction_points = 3

    parts["transactions"] = transaction_points

    score += transaction_points

    # 4. ALICI USTUNLUGU - 20 PUAN

    buyer_points = 0

    if buy_ratio >= 0.75:
        buyer_points = 20

    elif buy_ratio >= 0.70:
        buyer_points = 17

    elif buy_ratio >= 0.65:
        buyer_points = 14

    elif buy_ratio >= 0.60:
        buyer_points = 10

    elif buy_ratio >= 0.55:
        buyer_points = 5

    elif buy_ratio >= 0.50:
        buyer_points = 2

    parts["buyer_balance"] = buyer_points

    score += buyer_points

    # 5. HACIM / LIKIDITE DENGESI - 10 PUAN

    turnover_points = 0

    if 0.02 <= turnover <= 0.25:
        turnover_points = 10

    elif (
        0.01 <= turnover < 0.02
        or 0.25 < turnover <= 0.40
    ):
        turnover_points = 6

    elif (
        0.005 <= turnover < 0.01
        or 0.40 < turnover <= 0.50
    ):
        turnover_points = 3

    parts["turnover"] = turnover_points

    score += turnover_points

    # 6. HACIM IVMESI - 10 PUAN

    acceleration = None
    acceleration_points = 0

    age_minutes = age_hours * 60

    elapsed_windows = min(
        12,
        int(age_minutes // 5)
    )

    previous_windows = elapsed_windows - 1

    # Ilk 5 dakikalik donemden once ivme hesaplanmaz

    if previous_windows >= 1 and vol1h > vol5m:

        previous_volume = max(
            vol1h - vol5m,
            0
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

    # 7. BUYUK ALIMLAR - 10 PUAN

    trade_points = 0

    trade_metrics = trade_metrics or {}

    trade_buy_share = trade_metrics.get(
        "buy_share"
    )

    large_wallets = trade_metrics.get(
        "large_buy_wallets",
        0
    )

    if trade_buy_share is not None:

        if trade_buy_share >= 0.70:
            trade_points += 5

        elif trade_buy_share >= 0.60:
            trade_points += 3

        elif trade_buy_share >= 0.55:
            trade_points += 1

    if large_wallets >= 3:
        trade_points += 5

    elif large_wallets >= 2:
        trade_points += 4

    elif large_wallets >= 1:
        trade_points += 2

    parts["large_buys"] = trade_points

    score += trade_points

    # 8. TOKEN YASI - 5 PUAN

    age_points = 0

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

    # 9. FIYAT HAREKETI - 3 PUAN

    change5m = number(
        price_change.get("m5")
    )

    price_points = 0

    if 0 <= change5m <= 5:
        price_points = 3

    elif 5 < change5m <= 10:
        price_points = 1

    elif change5m > 20:
        price_points = -3

    elif change5m < -10:
        price_points = -3

    elif -10 <= change5m < -5:
        price_points = -1

    parts["price"] = price_points

    score += price_points

    # ASIRI HACIM / LIKIDITE RISKI

    risk_penalty = 0

    if turnover > 1:
        risk_penalty = 10

    elif turnover > 0.50:
        risk_penalty = 5

    score -= risk_penalty

    parts["turnover_penalty"] = -risk_penalty

    score = max(
        0,
        min(score, 100)
    )

    return {

        "score": score,

        "parts": parts,

        "buy_ratio": buy_ratio,

        "turnover": turnover,

        "acceleration": acceleration,

        "trade_metrics": trade_metrics

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

        txns = pair.get("txns") or {}

        tx5m = txns.get("m5") or {}

        buys = int(number(tx5m.get("buys")))

        sells = int(number(tx5m.get("sells")))

        total_txns = buys + sells

        if total_txns < MIN_TXNS_5M:

            stats["low_transactions"] += 1

            print(
                "ELENDI: Dusuk islem sayisi:",
                address
            )

            continue

        # GOPLUS AYNI KONTRAT ICIN TEKRAR CAGIRILMAZ

        key = address.lower()

        if key not in security_cache:

            print(
                "\nGOPLUS KONTROL EDILIYOR:",
                address
            )

            security_cache[key] = get_goplus_security(
                address
            )

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
            }

        }

        previous = best_candidates.get(key)

        if previous is None:

            best_candidates[key] = candidate

        elif (
            liquidity > previous["liquidity"]
            or (
                liquidity == previous["liquidity"]
                and vol5m > previous["volume5m"]
            )
        ):

            best_candidates[key] = candidate

    candidates = list(
        best_candidates.values()
    )

    # ISLEM DETAYI ONCESI PUANLAMA

    for item in candidates:

        result = calculate_score(
            item["pair"],
            item["age_hours"]
        )

        item["score"] = result["score"]
        item["score_details"] = result

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    # EN IYI 3 ADAY ICIN BUYUK ALIM KONTROLU

    print("\n========== BUYUK ALIM KONTROLU ==========")

    for item in candidates[:MAX_TRADE_CHECKS_PER_SCAN]:

        pair = item["pair"]

        print(
            "ISLEM DETAYI KONTROL EDILIYOR:",
            (pair.get("baseToken") or {}).get("symbol", "?")
        )

        metrics = get_pool_trade_metrics(
            pair,
            item["liquidity"]
        )

        item["trade_metrics"] = metrics

        if metrics.get("available"):

            print(
                "5 DK ALIM HACMI:",
                round(metrics.get("buy_volume", 0), 2)
            )

            print(
                "5 DK SATIM HACMI:",
                round(metrics.get("sell_volume", 0), 2)
            )

            print(
                "BUYUK ALIM SAYISI:",
                metrics.get("large_buy_count", 0)
            )

            print(
                "BUYUK ALICI CÜZDAN:",
                metrics.get("large_buy_wallets", 0)
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

            print(
                "SKOR NEDENIYLE ELENDI:",
                (item["pair"].get("baseToken") or {}).get("symbol"),
                "| Skor:",
                item["score"]
            )

    candidates = [
        x for x in candidates
        if x["score"] >= MIN_SIGNAL_SCORE
    ]

    candidates.sort(
        key=lambda x: x["score"],
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

        if not security.get("verified"):
            continue

        if security.get("critical"):
            continue

        taxes = security.get("tax_values") or {}

        if (
            taxes.get("buy_tax") != 0
            or taxes.get("sell_tax") != 0
        ):
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

        previous_score = previous.get(
            "alert_score"
        )

        # ESKI PUANLAMA SISTEMINDEN GECIS
        # ESKI MESAJI TEKRAR GONDERME

        if previous_score is not None and previous_version != SCORE_VERSION:

            history[key] = {

                **previous,

                "alert_score": score,

                "score_version": SCORE_VERSION,

                "score_baseline_at": datetime.now(
                    timezone.utc
                ).isoformat()

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
                "| Onceki:",
                previous_score,
                "| Yeni:",
                score
            )

            continue

        details = item["score_details"]

        trade = item["trade_metrics"]

        buy_percent = (
            details["buy_ratio"] * 100
        )

        turnover_percent = (
            details["turnover"] * 100
        )

        if details["acceleration"] is None:

            acceleration_text = (
                "Yeterli onceki 5 dk veri yok"
            )

        else:

            acceleration_text = (
                f"{details['acceleration']:.2f} kat"
            )

        if trade.get("available"):

            trade_text = (

                f"Son 5 dk alis hacmi: "
                f"${trade.get('buy_volume', 0):,.2f}\n"

                f"Son 5 dk satis hacmi: "
                f"${trade.get('sell_volume', 0):,.2f}\n"

                f"Buyuk alis sayisi: "
                f"{trade.get('large_buy_count', 0)}\n"

                f"Buyuk alici cuzdan: "
                f"{trade.get('large_buy_wallets', 0)}\n"

                f"Buyuk alis toplami: "
                f"${trade.get('large_buy_volume', 0):,.2f}"

            )

        else:

            trade_text = (
                "Buyuk alim verisi: Alinamadi / kontrol edilmedi"
            )

        warnings = security.get("flags") or []

        warning_text = (
            "\n".join(
                f"⚠️ {x}" for x in warnings
            )
            if warnings
            else "Ek guvenlik uyarisi yok."
        )

        message = (

            "🚨 BSC ERKEN COIN SINYALI\n\n"

            f"🪙 Coin: {base.get('name', 'Unknown')}\n"

            f"🔖 Sembol: {base.get('symbol', '?')}\n"

            f"⭐ Skor: {score}/100\n"

            f"💧 Likidite: ${item['liquidity']:,.2f}\n"

            f"📊 5 DK Hacim: ${item['volume5m']:,.2f}\n"

            f"🟢 Alim islemi: {item['buys']}\n"

            f"🔴 Satim islemi: {item['sells']}\n"

            f"📈 Alim orani: %{buy_percent:.1f}\n"

            f"📊 Hacim/Likidite: %{turnover_percent:.1f}\n"

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

                "symbol": base.get("symbol", "?")

            }

            alert_count += 1

        time.sleep(1)

    print("Gonderilen sinyal:", alert_count)


# ==================================================
# RAPOR
# ==================================================

def print_candidates(candidates):

    print("\n========== ADAY RAPORU ==========")

    for item in candidates[:20]:

        pair = item["pair"]

        base = pair.get("baseToken") or {}

        details = item["score_details"]

        print("-" * 50)

        print("TOKEN:", base.get("name", "Unknown"))

        print("SYMBOL:", base.get("symbol", "?"))

        print("SKOR:", item["score"], "/100")

        print("LIKIDITE:", item["liquidity"])

        print("5 DK HACIM:", item["volume5m"])

        print("ALIM:", item["buys"])

        print("SATIM:", item["sells"])

        print(
            "ALIM ORANI:",
            round(details["buy_ratio"] * 100, 2),
            "%"
        )

        print(
            "HACIM/LIKIDITE:",
            round(details["turnover"] * 100, 2),
            "%"
        )

        print("PUAN DAGILIMI:", details["parts"])

        print(
            "KONTRAT:",
            base.get("address", "")
        )

        print("LINK:", pair.get("url", ""))


# ==================================================
# ANA PROGRAM
# ==================================================

def main():

    print("=" * 55)

    print("BSC ERKEN COIN AVCISI - PUANLAMA V2")

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

    except Exception as e:

        print("TARAMA HATASI:", repr(e))

        raise


if __name__ == "__main__":

    main()

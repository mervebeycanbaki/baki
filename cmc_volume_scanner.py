import os
import requests
import time
from datetime import datetime, timedelta

# --- YAPILANDIRMA ---
CMC_API_KEY = os.environ.get("CMC_API_KEY")
WA_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
WA_PHONE_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
WA_RECIPIENT = os.environ.get("WHATSAPP_RECIPIENT_NUMBER")

# --- WHATSAPP BİLDİRİM FONKSİYONU ---
def send_whatsapp_alert(message):
    if not all([WA_TOKEN, WA_PHONE_ID, WA_RECIPIENT]):
        print("WhatsApp secret'ları eksik. Bildirim gönderilemedi.")
        return

    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": WA_RECIPIENT, "type": "text", "text": {"body": message}}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code != 200:
            print(f"Bildirim gönderilemedi. Hata: {response.text}")
    except Exception as e:
        print(f"Bildirim hatası: {e}")

# --- GEÇMİŞ HACİM VERİSİ ÇEKME FONKSİYONU ---
def get_historical_volumes(coin_id, days=730):
    url = f"https://pro-api.coinmarketcap.com/v2/cryptocurrency/ohlcv/historical"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')
    params = {"id": coin_id, "time_start": start_date, "interval": "daily", "count": days}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        data = response.json()
        quotes = data['data']['quotes']
        return [q['quote']['USD']['volume'] for q in quotes]
    except Exception:
        return []

# --- ANA MANTIK ---
def main():
    print(f"Tarama başladı: {datetime.now()}")
    
    # 1. Aşama: İlk 1000 coini listele
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"limit": 1000, "convert": "USD"}
    
    try:
        response = requests.get(url, headers=headers, params=params)
        coins = response.json()["data"]
    except Exception as e:
        print(f"Coin listesi çekilemedi: {e}")
        return

    signals = []
    scanned_count = 0
    
    # 2. Aşama: Coinleri tek tek analiz et
    for coin in coins:
        coin_id = coin["id"]
        symbol = coin["symbol"]
        current_volume = coin["quote"]["USD"]["volume_24h"]
        
        # 🛡️ AKILLI FİLTRE (API Kotasını Korumak İçin):
        # Eğer 24 saatlik hacim 1.000.000 Doların altındaysa, geçmişini sorgulama, atla.
        if current_volume < 1000000:
            continue
            
        print(f"[{scanned_count+1}] {symbol} analiz ediliyor...")
        scanned_count += 1
        
        # 2 yıllık veriyi çek
        all_volumes = get_historical_volumes(coin_id, 730)
        
        if not all_volumes or len(all_volumes) < 30:
            time.sleep(0.5) # Kısa bekleme
            continue
            
        # Ortalamaları hesapla
        avg_30d = sum(all_volumes[-30:]) / 30
        avg_90d = sum(all_volumes[-90:]) / 90 if len(all_volumes) >= 90 else avg_30d
        avg_180d = sum(all_volumes[-180:]) / 180 if len(all_volumes) >= 180 else avg_90d
        avg_365d = sum(all_volumes[-365:]) / 365 if len(all_volumes) >= 365 else avg_180d
        avg_730d = sum(all_volumes) / len(all_volumes)
        
        # Trader Mantığı: Kaç ortalama aşıldı?
        breaks = 0
        if current_volume > avg_30d: breaks += 1
        if current_volume > avg_90d: breaks += 1
        if current_volume > avg_180d: breaks += 1
        if current_volume > avg_365d: breaks += 1
        if current_volume > avg_730d: breaks += 1
        
        # En az 3 ortalama aşıldıysa sinyale ekle
        if breaks >= 3:
            sinyal = (f"🚨 *{symbol}* (Güçlü Hacim)\n"
                      f"Fiyat: ${coin['quote']['USD']['price']:.4f}\n"
                      f"24s Hacim: ${current_volume:,.0f}\n"
                      f"1A: {'✅' if current_volume > avg_30d else '❌'} | "
                      f"3A: {'✅' if current_volume > avg_90d else '❌'} | "
                      f"6A: {'✅' if current_volume > avg_180d else '❌'} | "
                      f"1Y: {'✅' if current_volume > avg_365d else '❌'} | "
                      f"2Y: {'✅' if current_volume > avg_730d else '❌'}")
            signals.append(sinyal)
        
        # API Rate-Limit (Dakikada 50 istek) sınırına takılmamak için 1.5 saniye bekle
        time.sleep(1.5)
        
    # 3. Aşama: Sonuçları WhatsApp'a gönder
    if signals:
        # WhatsApp mesaj limitini aşmamak için en fazla ilk 10 sinyali gönder
        top_signals = signals[:10] 
        message = f"🔥 *GÜNLÜK HACİM RAPORU* ({len(signals)} Sinyal Bulundu)\n\n" + "\n\n".join(top_signals)
        if len(signals) > 10:
            message += f"\n\n... ve {len(signals)-10} coin daha."
        send_whatsapp_alert(message)
    else:
        send_whatsapp_alert(f"✅ Tarama tamamlandı. Bugün belirgin bir anomali bulunamadı. ({scanned_count} coin detaylı incelendi)")

if __name__ == "__main__":
    main()

import os
import requests
from datetime import datetime

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
    headers = {
        "Authorization": f"Bearer {WA_TOKEN}",
        "Content-Type": "application/json"
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": WA_RECIPIENT,
        "type": "text",
        "text": {"body": message}
    }

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        if response.status_code == 200:
            print("WhatsApp bildirimi başarıyla gönderildi.")
        else:
            print(f"Bildirim gönderilemedi. Durum kodu: {response.status_code}, Hata: {response.text}")
    except Exception as e:
        print(f"Bildirim gönderilirken hata oluştu: {e}")

# --- COINMARKETCAP VERİ ÇEKME FONKSİYONU ---
def fetch_top_coins():
    print("CoinMarketCap'ten coin listesi çekiliyor...")
    url = "https://pro-api.coinmarketcap.com/v1/cryptocurrency/listings/latest"
    headers = {"X-CMC_PRO_API_KEY": CMC_API_KEY}
    params = {"limit": 100, "convert": "USD"} 
    
    try:
        response = requests.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()["data"]
    except Exception as e:
        print(f"Veri çekme hatası: {e}")
        return []

# --- ANA MANTIK ---
def main():
    print(f"Tarama başladı: {datetime.now()}")
    coins = fetch_top_coins()
    
    if not coins:
        send_whatsapp_alert("⚠️ CMC Hacim Tarayıcısı: Veri çekilemedi!")
        return

    # Test aşaması için basit bir anomali kontrolü (Hacim / Piyasa Değeri > %15)
    signals = []
    for coin in coins:
        symbol = coin["symbol"]
        volume = coin["quote"]["USD"]["volume_24h"]
        mcap = coin["quote"]["USD"]["market_cap"]
        
        if mcap > 0 and (volume / mcap) > 0.15:
            signals.append(f"🚨 {symbol} | Hacim/MCap: %{(volume/mcap)*100:.1f}")

    if signals:
        message = "🚨 *GÜÇLÜ HACİM SİNYALLERİ* 🚨\n\n" + "\n".join(signals)
        send_whatsapp_alert(message)
    else:
        # Eğer sinyal yoksa bile testin çalıştığını görmek için mesaj atalım
        send_whatsapp_alert(f"✅ Tarama tamamlandı. Bugün belirgin bir anomali bulunamadı. ({len(coins)} coin tarandı)")

if __name__ == "__main__":
    main()

import os
import requests

WA_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")
WA_PHONE_ID = os.environ.get("WHATSAPP_PHONE_NUMBER_ID")
WA_RECIPIENT = os.environ.get("WHATSAPP_RECIPIENT_NUMBER")

def send_test_message():
    print("Test mesajı gönderiliyor...")
    url = f"https://graph.facebook.com/v18.0/{WA_PHONE_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}
    payload = {
        "messaging_product": "whatsapp",
        "to": WA_RECIPIENT,
        "type": "text",
        "text": {"body": "🤖 Merhaba! Bu bir test mesajıdır. Eğer bunu görüyorsan sistem çalışıyor demektir."}
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=15)
        print(f"Meta API Cevabı: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"Hata: {e}")

if __name__ == "__main__":
    send_test_message()

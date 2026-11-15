import os
import requests

TG_TOKEN = os.environ.get("WHATSAPP_ACCESS_TOKEN")

if __name__ == "__main__":
    print("--- TELEGRAM BOT BİLGİLERİ ÇEKİLİYOR ---")
    url = f"https://api.telegram.org/bot{TG_TOKEN}/getUpdates"
    response = requests.get(url)
    print("API Yanıtı:")
    print(response.text)

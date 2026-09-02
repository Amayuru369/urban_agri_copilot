import os
import httpx
from dotenv import load_dotenv

load_dotenv()

key = os.getenv("GEMINI_API_KEY")

if not key:
    print("ERROR: GEMINI_API_KEY was not found in your .env file.")
else:
    clean_key = key.strip().strip("'\"")
    print(f"Testing key starting with: {clean_key[:8]}...")

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent"
    headers = {
        "x-goog-api-key": clean_key,
        "Content-Type": "application/json"
    }
    payload = {
        "contents": [{"parts": [{"text": "ping"}]}]
    }

    try:
        response = httpx.post(url, headers=headers, json=payload, timeout=15.0)
        print(f"Status Code: {response.status_code}")
        print(f"Response: {response.text}")
    except Exception as e:
        print(f"Request failed: {e}")
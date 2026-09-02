import os
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import types

load_dotenv()

print("=" * 55)
print(" 🔍 API ENVIRONMENT & LIVE HEALTH CHECK")
print("=" * 55)

# 1. Check Gemini Keys
raw_gemini = os.getenv("GEMINI_API_KEY", "")
gemini_keys = [k.strip() for k in raw_gemini.split(",") if k.strip()]

if not gemini_keys:
    print("❌ GEMINI_API_KEY: Missing in .env")
else:
    print(f"🔑 GEMINI_API_KEY: Found {len(gemini_keys)} key(s)")
    for idx, key in enumerate(gemini_keys, 1):
        masked = key[:6] + "..." + key[-4:] if len(key) > 10 else "INVALID"
        try:
            client = genai.Client(api_key=key)
            res = client.models.generate_content(
                model="gemini-3.6-flash",  # <--- Updated model
                contents="ping"
            )
            print(f"   ├─ Key #{idx} [{masked}]: ✅ ACTIVE & WORKING")
        except Exception as e:
            print(f"   ├─ Key #{idx} [{masked}]: ❌ FAILED -> {e}")

print("-" * 55)

# 2. Check Hugging Face Token
hf_token = os.getenv("HF_TOKEN")
if not hf_token:
    print("❌ HF_TOKEN: Missing in .env")
else:
    masked_hf = hf_token[:6] + "..." + hf_token[-4:] if len(hf_token) > 10 else "INVALID"
    hf_res = requests.get(
        "https://huggingface.co/api/whoami-v2", 
        headers={"Authorization": f"Bearer {hf_token}"}
    )
    if hf_res.status_code == 200:
        user_info = hf_res.json().get("name", "Valid User")
        print(f"✅ HF_TOKEN [{masked_hf}]: ACTIVE (User: {user_info})")
    else:
        print(f"❌ HF_TOKEN [{masked_hf}]: FAILED -> HTTP {hf_res.status_code}")

print("=" * 55)
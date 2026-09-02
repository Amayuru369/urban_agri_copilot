import os
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("HF_TOKEN")
print(f"Token found: {bool(token)}")

if token:
    try:
        from huggingface_hub import InferenceClient
        client = InferenceClient(token=token)
        print("Hugging Face client created successfully!")
    except ImportError:
        print("huggingface-hub library is NOT installed.")
        print("Run: pip install huggingface-hub")
else:
    print("No token found! Check your .env file.")
    print("Make sure your .env file is in the project root and contains:")
    print('HF_TOKEN="hf_your_actual_token_here"')
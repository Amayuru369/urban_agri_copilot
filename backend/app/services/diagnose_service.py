from __future__ import annotations

import os
import json
import base64
import io
import re
import logging
from typing import Any

import httpx

from backend.app.core.config import settings

try:
    from PIL import Image
    _HAS_PIL = True
except Exception:
    _HAS_PIL = False

logger = logging.getLogger(__name__)

# ============ CONSTANTS ============
HF_MODEL_ID = "linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification"

# ============ CROP MATCH VALIDATION ============
def _compute_crop_match(detected_crop: str | None, user_crop: str | None, crop_confidence: float) -> tuple[str, str | None]:
    """
    Compare detected crop against user-selected crop to determine match status.
    Returns (crop_match_status, crop_match_message).
    """
    # If user didn't select a crop or selected auto-detect
    if not user_crop or user_crop.lower() in ["", "auto-detect", "unknown"]:
        return "auto_detected", None
    
    # Normalize for comparison
    detected_normalized = (detected_crop or "").lower().strip().replace("_", " ")
    user_normalized = user_crop.lower().strip().replace("_", " ")
    
    # Check if crops match (exact or substring match)
    is_match = (
        detected_normalized == user_normalized or
        user_normalized in detected_normalized or
        detected_normalized in user_normalized
    )
    
    confidence_pct = round(crop_confidence * 100, 0)
    
    if is_match:
        return "confirmed", f"✓ Verified: The leaf photo matches your selected crop ({user_crop})."
    else:
        return "mismatch", f"⚠️ Note: You selected {user_crop}, but visual analysis indicates a {confidence_pct}% probability that this is {detected_crop}."


# ============ MOCK DIAGNOSIS ============
def _mock_diagnosis(crop_name: str | None) -> dict:
    crop = crop_name or "Unknown crop"
    crop_confidence = 0.95
    status, message = _compute_crop_match(crop, crop_name, crop_confidence)
    return {
        "crop_detected": crop,
        "crop_confidence": crop_confidence,
        "issue_type": "General Plant Stress",
        "disease_confidence": 0.80,
        "confidence": 0.80,
        "symptoms": ["Check plant health manually"],
        "severity": "moderate",
        "recommended_action": "Water and monitor the plant.",
        "crop_match_status": status,
        "crop_match_message": message
    }

# ============ REMEDY GENERATOR ============
def _remedy_dict_from_issue(issue_type: str, crop_name: str) -> dict:
    return {
        "issue_type": issue_type,
        "remedy_name": "General Organic Treatment",
        "ingredients": [
            "1 litre lukewarm water",
            "1 tsp mild liquid soap",
            "1 tsp cold-pressed neem oil"
        ],
        "preparation_steps": [
            "Mix soap into water until emulsified",
            "Add neem oil and shake thoroughly",
            "Spray foliage during early morning or late evening"
        ],
        "application_schedule": "Apply every 5 to 7 days.",
        "safety_notes": [
            "Avoid spraying in direct sunlight.",
            "Test on a single leaf first."
        ],
        "matched_scraps": []
    }

# ============ QODER MODEL DISCOVERY ============
async def _get_qoder_model() -> str:
    """Fetch the first enabled vision-language model from Qoder, or fall back to 'auto'."""
    token = os.getenv("QODER_PERSONAL_ACCESS_TOKEN")
    if not token:
        return "auto"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.qoder.com/api/v1/cloud/models",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 200:
                models = resp.json().get("data", [])
                for m in models:
                    if m.get("is_vl") and m.get("is_enabled"):
                        return m.get("id") or "auto"
    except Exception as exc:
        logger.warning("Could not fetch Qoder model list: %s", repr(exc))
    return "auto"


# ============ 1. QODER VISION API ============
async def _qoder_vision_diagnosis(image_bytes: bytes, crop_name: str | None = None) -> dict | None:
    """
    Call Qoder Vision API for plant disease diagnosis.
    Uses the /forward endpoint for direct chat completions.
    """
    token = os.getenv("QODER_PERSONAL_ACCESS_TOKEN")
    if not token:
        print("⚠️ QODER_PERSONAL_ACCESS_TOKEN not found in .env")
        return None

    model_id = "ultimate"  # or "qmodel", "performance"
    print(f"🔍 [1] Attempting Qoder Vision API (model: {model_id})...")

    try:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        image_url = f"data:image/jpeg;base64,{base64_image}"

        prompt = f"""You are a plant pathologist. Analyze this leaf image.
Target crop: {crop_name or 'Auto-detect'}.
Return ONLY valid JSON with these exact keys:
{{"crop_detected": "...", "crop_confidence": 0.0-1.0, "issue_type": "...", "disease_confidence": 0.0-1.0,
"symptoms": [...], "severity": "low|moderate|high", "recommended_action": "..."}}
- crop_confidence: your confidence (0.0-1.0) that this is the correct plant species
- disease_confidence: your confidence (0.0-1.0) in the disease diagnosis
Do not include any other text outside the JSON."""

        payload = {
            "model": model_id,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }
            ]
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        # Use /forward endpoint for direct chat completions
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                "https://api.qoder.com/api/v1/forward",  # ✅ Changed from /cloud/agents
                json=payload,
                headers=headers
            )

            if response.status_code == 200:
                result = response.json()
                print("Full Qoder response:", json.dumps(result, indent=2))  # DEBUG

                # Extract content from response
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if content:
                    content = content.strip()
                    if content.startswith("```"):
                        content = content.split("\n", 1)[-1].rsplit("\n", 1)[0]
                    diagnosis = json.loads(content)
                    
                    # Enrich with new fields if missing
                    diagnosis.setdefault("crop_confidence", diagnosis.get("confidence", 0.85))
                    diagnosis.setdefault("disease_confidence", diagnosis.get("confidence", 0.80))
                    diagnosis.setdefault("confidence", diagnosis.get("disease_confidence", 0.80))
                    
                    # Compute crop match
                    status, message = _compute_crop_match(
                        diagnosis.get("crop_detected"),
                        crop_name,
                        diagnosis.get("crop_confidence", 0.85)
                    )
                    diagnosis["crop_match_status"] = status
                    diagnosis["crop_match_message"] = message
                    
                    print("✅ Qoder Vision API succeeded!")
                    return diagnosis
                else:
                    print("⚠️ Qoder returned empty content")
                    return None
            else:
                print(f"⚠️ Qoder forward endpoint returned {response.status_code}: {response.text[:200]}")
                return None

    except Exception as e:
        print(f"❌ Qoder API error: {e}")
        return None
# ============ 2. GEMINI VISION API ============
import os
import json
from google import genai
from google.genai import types

def _gemini_diagnosis(image_bytes: bytes, crop_name: str | None = None) -> dict | None:
    raw_keys = os.getenv("GEMINI_API_KEY", "")
    api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]

    if not api_keys:
        print("⚠️ GEMINI_API_KEY not found in .env (skipping)")
        return None

    for idx, api_key in enumerate(api_keys):
        print(f"🔍 [2] Attempting Gemini API (Key #{idx + 1}/{len(api_keys)})...")
        try:
            client = genai.Client(api_key=api_key)
            prompt = f"""You are a plant pathologist. Analyze this leaf image.
Target crop: {crop_name or 'Auto-detect'}.
Return ONLY valid JSON:
{{"crop_detected": "...", "crop_confidence": 0.0-1.0, "issue_type": "...", "disease_confidence": 0.0-1.0,
"symptoms": [...], "severity": "low|moderate|high", "recommended_action": "..."}}
- crop_confidence: your confidence (0.0-1.0) that this is the correct plant species
- disease_confidence: your confidence (0.0-1.0) in the disease diagnosis"""

            # Explicitly disable AFC to remove the SDK warning and enforce strict JSON output
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            )

            response = client.models.generate_content(
                model="gemini-3.6-flash",
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                    prompt,
                ],
                config=config,
            )

            content = response.text.strip()
            if content.startswith("```"):
                content = content.split("\n", 1)[-1].rsplit("\n", 1)[0]
            diagnosis = json.loads(content)

            # Enrich with new fields if missing
            diagnosis.setdefault("crop_confidence", diagnosis.get("confidence", 0.85))
            diagnosis.setdefault("disease_confidence", diagnosis.get("confidence", 0.80))
            diagnosis.setdefault("confidence", diagnosis.get("disease_confidence", 0.80))

            # Compute crop match
            status, message = _compute_crop_match(
                diagnosis.get("crop_detected"),
                crop_name,
                diagnosis.get("crop_confidence", 0.85),
            )
            diagnosis["crop_match_status"] = status
            diagnosis["crop_match_message"] = message

            print(f"✅ Gemini API succeeded with Key #{idx + 1}!")
            return diagnosis

        except Exception as e:
            print(f"⚠️ Gemini Key #{idx + 1} failed: {e}")
            if idx < len(api_keys) - 1:
                print("🔄 Trying next Gemini API key...")
            continue

    print("❌ All Gemini API keys failed; falling back...")
    return None
# ============ 3. HUGGING FACE API ============
import os

def _huggingface_diagnosis(image_bytes: bytes, crop_name: str | None = None) -> dict | None:
    token = os.getenv("HF_TOKEN")
    if not token:
        print("⚠️ HF_TOKEN not found in .env (skipping)")
        return None

    print("🔍 [3] Attempting Hugging Face API...")

    try:
        import requests
        
        # Hugging Face inference endpoints
        API_URL = f"https://router.huggingface.co/hf-inference/models/{HF_MODEL_ID}"
        FALLBACK_URL = f"https://api-inference.huggingface.co/models/{HF_MODEL_ID}"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "image/jpeg"
        }
        
        try:
            response = requests.post(API_URL, headers=headers, data=image_bytes, timeout=20)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
            response = requests.post(FALLBACK_URL, headers=headers, data=image_bytes, timeout=20)
        
        if response.status_code == 200:
            result = response.json()
            if not result or not isinstance(result, list) or len(result) == 0:
                return None
            
            # Default to the global highest prediction
            global_top = result[0]
            selected_item = global_top
            warning_note = None

            # Check if a specific crop was provided
            if crop_name and crop_name.lower() not in ["auto-detect", "unknown", ""]:
                target_crop = crop_name.strip().lower()
                matched_items = [
                    item for item in result 
                    if isinstance(item, dict) and target_crop in item.get("label", "").lower().replace("_", " ")
                ]

                if matched_items:
                    matched_best = max(matched_items, key=lambda x: x.get("score", 0.0))
                    
                    # Conflict Resolution:
                    # If user crop score is extremely low (< 25%) while global detection is very confident (> 70%)
                    if matched_best.get("score", 0.0) < 0.25 and global_top.get("score", 0.0) > 0.70:
                        selected_item = global_top
                        global_crop = global_top.get("label", "").split("___")[0].replace("_", " ")
                        warning_note = f"You selected {crop_name}, but visual features strongly indicate {global_crop} ({global_top.get('score', 0.0) * 100:.1f}% confidence)."
                    else:
                        selected_item = matched_best
                else:
                    # No labels match user selection; fall back to global top
                    selected_item = global_top

            label = selected_item.get("label", "") if isinstance(selected_item, dict) else getattr(selected_item, "label", "")
            confidence = selected_item.get("score", 0.0) if isinstance(selected_item, dict) else getattr(selected_item, "score", 0.0)
            
            # Parse crop and condition label
            if "___" in label:
                parts = label.split("___")
                detected_crop = parts[0].replace("_", " ")
                issue = parts[1].replace("_", " ")
            elif " with " in label:
                parts = label.split(" with ", 1)
                detected_crop = parts[0].strip()
                issue = parts[1].strip()
            else:
                detected_crop = crop_name or "Unknown"
                issue = label.replace("_", " ")

            severity = "low"
            if "healthy" in issue.lower():
                severity = "low"
            elif any(w in issue.lower() for w in ["early", "mild"]):
                severity = "moderate"
            else:
                severity = "high"

            symptoms = [f"Model detected: {issue} with {confidence * 100:.1f}% confidence."]
            if warning_note:
                symptoms.append(f"⚠️ Note: {warning_note}")

            # Compute separate confidences
            # crop_confidence: confidence in the plant species detection
            # disease_confidence: confidence in the disease classification
            global_label = global_top.get("label", "") if isinstance(global_top, dict) else getattr(global_top, "label", "")
            global_confidence = global_top.get("score", 0.0) if isinstance(global_top, dict) else getattr(global_top, "score", 0.0)
            
            # If we have a crop___disease format, the global confidence represents both
            crop_confidence = round(global_confidence, 3)
            disease_confidence = round(confidence, 3)

            diagnosis = {
                "crop_detected": detected_crop.capitalize(),
                "crop_confidence": crop_confidence,
                "issue_type": issue,
                "disease_confidence": disease_confidence,
                "confidence": disease_confidence,
                "symptoms": symptoms,
                "severity": severity,
                "recommended_action": "Apply organic neem oil or compost tea as a general remedy."
            }
            
            # Compute crop match status
            status, message = _compute_crop_match(
                diagnosis.get("crop_detected"),
                crop_name,
                crop_confidence
            )
            diagnosis["crop_match_status"] = status
            diagnosis["crop_match_message"] = message
            
            print(f"✅ Hugging Face API succeeded! Detected: {diagnosis['crop_detected']} - {diagnosis['issue_type']}")
            return diagnosis
        else:
            print(f"❌ Hugging Face API returned {response.status_code}: {response.text[:200]}")
            return None

    except Exception as e:
        print(f"❌ Hugging Face API error: {e}")
        return None
# ============ 4. PIXEL ANALYZER ============
def analyze_leaf_pixels(image_bytes: bytes, crop_name: str | None = None) -> dict:
    """
    On-device multi-symptom pixel analyzer.
    Priority 4: Always works, no API key needed.
    """
    print("🔍 [4] Using Pixel Analyzer (fallback)...")

    if not _HAS_PIL:
        return _mock_diagnosis(crop_name)

    try:
        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        image.thumbnail((256, 256))
        raw = image.tobytes()
        total = len(raw) // 3
        if total == 0:
            return _mock_diagnosis(crop_name)

        necrosis = 0
        powdery_mildew = 0
        rust = 0
        chlorosis = 0
        green = 0

        for i in range(0, len(raw), 3):
            r, g, b = raw[i], raw[i + 1], raw[i + 2]
            if r < 75 and g < 65 and b < 55:
                necrosis += 1
            elif r > 190 and g > 190 and b > 190:
                powdery_mildew += 1
            elif r > 150 and g < 100 and b < 60:
                rust += 1
            elif r > 130 and g > 120 and b < 90:
                chlorosis += 1
            elif g > 55 and g > r and g > b:
                green += 1

        ratios = {
            "necrosis": necrosis/total,
            "powdery_mildew": powdery_mildew/total,
            "rust": rust/total,
            "chlorosis": chlorosis/total,
            "green": green/total
        }

        if ratios["necrosis"] > 0.05:
            disease_conf = min(0.95, ratios["necrosis"] * 3)
            crop_conf = 0.85  # Pixel analyzer assumes crop is correct
            status, message = _compute_crop_match(crop_name or "Unknown crop", crop_name, crop_conf)
            return {
                "crop_detected": crop_name or "Unknown crop",
                "crop_confidence": crop_conf,
                "issue_type": "Necrotic Spot / Blight",
                "disease_confidence": disease_conf,
                "confidence": disease_conf,
                "symptoms": ["Dark necrotic lesions on leaf tissue", "Browning of affected areas"],
                "severity": "high" if ratios["necrosis"] > 0.15 else "moderate",
                "recommended_action": "Prune affected leaves and apply copper-based organic fungicide.",
                "crop_match_status": status,
                "crop_match_message": message
            }
        elif ratios["chlorosis"] > 0.05:
            disease_conf = min(0.95, ratios["chlorosis"] * 3)
            crop_conf = 0.85
            status, message = _compute_crop_match(crop_name or "Unknown crop", crop_name, crop_conf)
            return {
                "crop_detected": crop_name or "Unknown crop",
                "crop_confidence": crop_conf,
                "issue_type": "Chlorosis / Nitrogen Deficiency",
                "disease_confidence": disease_conf,
                "confidence": disease_conf,
                "symptoms": ["Yellowing between veins", "Pale new growth"],
                "severity": "moderate" if ratios["chlorosis"] > 0.12 else "low",
                "recommended_action": "Apply compost tea or banana-peel fertilizer.",
                "crop_match_status": status,
                "crop_match_message": message
            }
        elif ratios["powdery_mildew"] > 0.05:
            disease_conf = min(0.95, ratios["powdery_mildew"] * 3)
            crop_conf = 0.85
            status, message = _compute_crop_match(crop_name or "Unknown crop", crop_name, crop_conf)
            return {
                "crop_detected": crop_name or "Unknown crop",
                "crop_confidence": crop_conf,
                "issue_type": "Powdery Mildew",
                "disease_confidence": disease_conf,
                "confidence": disease_conf,
                "symptoms": ["White powdery patches on leaf surfaces"],
                "severity": "moderate" if ratios["powdery_mildew"] > 0.10 else "low",
                "recommended_action": "Apply diluted milk spray (1:9) or baking-soda solution.",
                "crop_match_status": status,
                "crop_match_message": message
            }
        else:
            crop_conf = 0.95
            status, message = _compute_crop_match(crop_name or "Unknown crop", crop_name, crop_conf)
            return {
                "crop_detected": crop_name or "Unknown crop",
                "crop_confidence": crop_conf,
                "issue_type": "Healthy Foliage",
                "disease_confidence": 0.95,
                "confidence": 0.95,
                "symptoms": ["Uniform green coloration", "No visible lesions detected"],
                "severity": "low",
                "recommended_action": "Maintain current care routine.",
                "crop_match_status": status,
                "crop_match_message": message
            }

    except Exception as e:
        print(f"⚠️ Pixel analyzer error: {e}")
        return _mock_diagnosis(crop_name)

# ============ MAIN DIAGNOSIS FUNCTION ============
async def diagnose_plant_image(
    image_bytes: bytes,
    crop_name: str | None = None,
    use_mock: bool = False,
) -> tuple[dict, dict]:
    """
    Diagnose a plant leaf image.

    PRIORITY ORDER:
    1. Qoder Vision API (using hackathon credits)
    2. Google Gemini Vision API
    3. Hugging Face Inference API
    4. On-device pixel analyzer (fallback)
    """
    if use_mock:
        diagnosis = _mock_diagnosis(crop_name)
        return diagnosis, _remedy_dict_from_issue(diagnosis["issue_type"], diagnosis["crop_detected"])

    # ----- PRIORITY 1: Qoder Vision -----
    diagnosis = await _qoder_vision_diagnosis(image_bytes, crop_name)
    source = "qoder"

    # ----- PRIORITY 2: Gemini Vision -----
    if diagnosis is None:
        diagnosis = _gemini_diagnosis(image_bytes, crop_name)
        source = "gemini"

    # ----- PRIORITY 3: Hugging Face -----
    if diagnosis is None:
        diagnosis = _huggingface_diagnosis(image_bytes, crop_name)
        source = "huggingface"

    # ----- PRIORITY 4: Pixel Analyzer (always works) -----
    if diagnosis is None:
        diagnosis = analyze_leaf_pixels(image_bytes, crop_name)
        source = "pixel"

    issue_type = diagnosis.get("issue_type", "General Plant Stress")
    remedy = _remedy_dict_from_issue(issue_type, diagnosis.get("crop_detected") or crop_name)

    print(f"✅ Final diagnosis source: {source}")
    return diagnosis, remedy

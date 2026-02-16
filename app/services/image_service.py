"""Image service — download WhatsApp media & extract receipt info via vision model."""

from __future__ import annotations

import base64
import json
import logging
import re

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_nebius import ChatNebius

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Lazy-initialised vision LLM ─────────────────────────────────
_vision_llm: ChatNebius | None = None
_llm: ChatNebius | None = None


def _get_vision_llm() -> ChatNebius:
    """Return (and cache) the vision-capable ChatNebius instance."""
    global _vision_llm
    if _vision_llm is None:
        settings = get_settings()
        _vision_llm = ChatNebius(
            api_key=settings.nebius_api_key,
            model=settings.nebius_vision_model,
            temperature=0.1,  # very low temp for strict JSON
            max_tokens=2048,
        )
    return _vision_llm

def _get_llm() -> ChatNebius:
    """Return (and cache) the ChatNebius instance."""
    global _llm
    if _llm is None:
        settings = get_settings()
        _llm = ChatNebius(
            api_key=settings.nebius_api_key,
            model=settings.nebius_model,
            temperature=0.1,
            max_tokens=2048,
        )
    return _llm


# ── Receipt extraction prompt ───────────────────────────────────
RECEIPT_SYSTEM_PROMPT = """You are an expert data-extraction assistant for a printing & stationery shop.
Extract receipt or order data from images or user text into STRICT JSON.

JSON Schema (use these EXACT key names):
{
  "no_nota": "Receipt/transaction ID, or 'Tidak Diketahui' if unknown",
  "customer_name": "Customer name or null",
  "transaction_date": "Date in YYYY-MM-DD format or null",
  "payment_method": "Cash / Transfer / QR or null",
  "down_payment": "Down Payment amount as NUMERIC STRING (e.g. '100000'). Default '0'.",
  "items": [
    {
      "item_name": "Item name ONLY — no size, no material (e.g. 'Spanduk', 'Banner', 'Pulpen')",
      "size": "Size/dimension as separate field (e.g. '2x3m', 'A3+', 'A4', '5x1m') or null",
      "material": "Material/media as separate field (e.g. 'Flexi', 'Vinyl', 'HVS', 'Korea') or null",
      "quantity": "Quantity as string with unit (e.g. '2 pcs', '1 lbr', '3 rim'). Default '1'.",
      "price_per_item": "UNIT price for ONE piece as numeric string (e.g. '50000')",
      "total_price": "Line total = quantity × price_per_item (e.g. '300000')",
      "notes": "Extra remarks ONLY (e.g. 'laminasi', 'mata ayam'). NOT the item name. null if none."
    }
  ],
  "total": "Grand total (sum of all total_price) as numeric string or '0'"
}

Rules:
1. Output valid JSON ONLY. No markdown, no commentary, no ```json blocks.
2. Every item MUST have: item_name, quantity, price_per_item, total_price.
3. SEPARATE FIELDS: "size" and "material" MUST be their own fields. NEVER put them in "item_name".
   "Spanduk 2x3m Flexi" → item_name="Spanduk", size="2x3m", material="Flexi"
   "Banner 5x1 Korea"   → item_name="Banner", size="5x1m", material="Korea"
4. UNIT PRICE: "price_per_item" is ALWAYS the price for ONE piece. Do NOT divide by quantity.
   "Spanduk 2pcs 150000" → price_per_item="150000", total_price="300000" (2×150000)
   "Pulpen 5pcs 10000"   → price_per_item="10000", total_price="50000" (5×10000)
5. "notes" is ONLY for finishing/processing remarks, NOT the item name.
6. If quantity is unclear, default to "1".
7. If unit price is unclear but total is known, set price_per_item = total_price.
8. If a text field is missing, use "Tidak Diketahui". If a numeric field is missing, use "0".

DOWN PAYMENT RULES (CRITICAL):
9. "down_payment" is a TOP-LEVEL field, NOT inside items.
10. Look for keywords: "DP", "dp", "Uang Muka", "Down Payment", "Bayar Dulu".
11. Extract the numeric amount and place it in the top-level "down_payment" field.
12. If NO down payment is mentioned, set "down_payment": "0".
    Examples:
    - "DP 100000"       → "down_payment": "100000"
    - "DP: Rp 50.000"   → "down_payment": "50000"
    - "Uang muka 200rb"  → "down_payment": "200000"
    - No DP mentioned    → "down_payment": "0"

FULL EXAMPLE:
Input: "Spanduk 2x3m Flexi 2pcs 150000, Pulpen 5pcs 10000, DP 100000"
Output:
{
  "no_nota": "Tidak Diketahui",
  "customer_name": null,
  "transaction_date": null,
  "payment_method": null,
  "down_payment": "100000",
  "items": [
    {
      "item_name": "Spanduk",
      "size": "2x3m",
      "material": "Flexi",
      "quantity": "2 pcs",
      "price_per_item": "150000",
      "total_price": "300000",
      "notes": null
    },
    {
      "item_name": "Pulpen",
      "size": null,
      "material": null,
      "quantity": "5 pcs",
      "price_per_item": "10000",
      "total_price": "50000",
      "notes": null
    }
  ],
  "total": "350000"
}

"""

GENERAL_IMAGE_PROMPT = """You are a helpful AI assistant that can analyze images.
Describe what you see in the image and extract any relevant information.
If the image contains text, read and transcribe it.
Respond in the same language as any text found, or in Indonesian by default."""


async def download_wa_media(media_id: str) -> bytes:
    """Download media from WhatsApp Cloud API."""
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Get media URL
        meta_url = f"https://graph.facebook.com/v22.0/{media_id}"
        resp = await client.get(meta_url, headers=headers)
        resp.raise_for_status()
        media_url = resp.json()["url"]

        logger.info("📥 Downloading media from: %s", media_url[:80])

        # Step 2: Download actual file
        file_resp = await client.get(media_url, headers=headers)
        file_resp.raise_for_status()

        logger.info("📥 Downloaded %d bytes", len(file_resp.content))
        return file_resp.content


async def analyze_image(image_bytes: bytes, caption: str | None = None) -> str | dict:
    """Analyze an image using the Nebius vision model.
    
    Returns:
        str: General description (if not a receipt).
        dict: Structured data (if receipt extraction requested).
    """
    llm = _get_vision_llm()

    # Encode image to base64
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # Determine prompt based on caption
    is_receipt = False
    
    if caption and any(kw in caption.lower() for kw in ["struk", "receipt", "nota", "belanja", "kasir"]):
        is_receipt = True
        system_prompt = RECEIPT_SYSTEM_PROMPT
        user_text = f"Extract information from this receipt. User note: {caption}"
    elif caption:
        system_prompt = GENERAL_IMAGE_PROMPT
        user_text = caption
    else:
        # Default: try receipt extraction first
        is_receipt = True
        system_prompt = RECEIPT_SYSTEM_PROMPT
        user_text = "Extract information from this image if it is a receipt."

    # Build multimodal message
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(
            content=[
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{b64_image}"},
                },
            ]
        ),
    ]

    try:
        response = await llm.ainvoke(messages)
        content = response.content
        logger.info("Vision LLM output: %s", str(content)[:200]) # Log first 200 chars

        # Parse JSON if we expect a receipt
        if is_receipt:
            return _parse_json_response(str(content))
        
        return str(content)

    except Exception:
        logger.exception("Vision model call failed")
        return "Maaf, saya gagal menganalisa gambar ini. Coba kirim ulang dengan kualitas lebih baik. 🙏"


def _parse_json_response(content: str) -> dict | str:
    """Attempt to clean and parse JSON from LLM output."""
    try:
        # Remove markdown code blocks if present
        clean_content = content.replace("```json", "").replace("```", "").strip()
        # Basic cleanup for common json errors if needed (e.g. trailing commas)
        return json.loads(clean_content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON from Vision model: %s", content[:100])
        # Try to use regex to find JSON block
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
             try:
                return json.loads(match.group(0))
             except:
                pass
        
        # Return as raw text if parsing fails
        return content


async def parse_text_to_receipt(text: str) -> dict | str:
    """Parse free-form text into structured receipt JSON using the LLM.

    Args:
        text: User-typed receipt description, e.g.
              "Nota 123 spanduk 2x3 50rb, pulpen 10rb total 60000"

    Returns:
        Parsed receipt dict, or an error string if parsing fails.
    """
    llm = _get_llm()

    messages = [
        SystemMessage(content=RECEIPT_SYSTEM_PROMPT),
        HumanMessage(content=(
            "Extract receipt data from this text description into JSON:\n\n"
            f"{text}"
        )),
    ]

    try:
        response = await llm.ainvoke(messages)
        content = str(response.content)
        logger.info("Text-to-receipt LLM output: %s", content[:500])
        return _parse_json_response(content)
    except Exception:
        logger.exception("Text-to-receipt LLM call failed")
        return "Maaf, gagal memproses teks struk. Coba lagi. 🙏"


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
RECEIPT_SYSTEM_PROMPT = """You are an expert OCR assistant.
Your task is to extract data from shopping receipts into STRICT JSON format.

JSON Schema:
{
  "no_nota": "Receipt/transaction ID or 'Tidak Diketahui'",
  "customer_name": "Customer name or null",
  "transaction_date": "Date in YYYY-MM-DD format or null",
  "payment_method": "Cash/Transfer/QR or null",
  "items": [
    {
      "nama_barang": "Item name ONLY, without size or material (e.g. 'Spanduk', 'Stiker', 'Pulpen', 'Banner')",
      "ukuran": "Size/dimension MUST be extracted separately (e.g. '2x3m', 'A3+', 'A4', '5x1m'). NEVER put this in nama_barang.",
      "bahan": "Material/media MUST be extracted separately (e.g. 'Flexi', 'Vinyl', 'HVS', 'Albatros', 'Korea'). NEVER put this in nama_barang.",
      "jumlah": "Quantity as string (e.g. '2 pcs', '1 lbr', '3 rim')",
      "harga": "UNIT price PER ITEM as numeric string (e.g. '50000'). This is ALWAYS the price for ONE piece.",
      "total": "Line total = jumlah x harga (e.g. if jumlah=2 and harga=150000, then total='300000')",
      "keterangan": "Extra notes/remarks ONLY (e.g. 'finishing laminasi', 'mata ayam', 'cutting bulat') or null. Do NOT put the item name here."
    }
  ],
  "total": "Grand total amount as numeric string or '0'"
  "down_payment": "Down Payment (DP) amount as numeric string or '0'"
}

Rules:
1. Output MUST be valid JSON only. Do not add markdown blocks like ```json.
2. Each item must have nama_barang, jumlah, harga, and total.
3. "keterangan" is ONLY for extra finishing/processing notes, NOT the item name.
4. If quantity is unclear, default to "1".
5. If unit price is unclear but total is known, set harga = total.
6. If a field is missing, use "Tidak Diketahui" for text or "0" for amounts.
7. Do not include any conversational text.
8. CRITICAL: "ukuran" and "bahan" MUST be extracted as SEPARATE fields. Do NOT combine them into "nama_barang".
   Example: "Spanduk 2x3m Flexi" should become: nama_barang="Spanduk", ukuran="2x3m", bahan="Flexi".
   Example: "Banner 5x1 Korea" should become: nama_barang="Banner", ukuran="5x1m", bahan="Korea".
9. CRITICAL: "harga" is ALWAYS the price PER SINGLE ITEM. The number the user writes next to the item is the UNIT PRICE, never the line total.
   Do NOT divide the price by quantity. The total is calculated as jumlah x harga.
   Example: "Spanduk 2pcs 150000" means harga="150000" (per piece), total="300000" (2 x 150000).
   Example: "Pulpen 5pcs 10000" means harga="10000" (per piece), total="50000" (5 x 10000).
10. Extract "DP" or "Uang Muka" if mentioned. If not mentioned, set "dp": "0".

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
        logger.info("Text-to-receipt LLM output: %s", content[:200])
        return _parse_json_response(content)
    except Exception:
        logger.exception("Text-to-receipt LLM call failed")
        return "Maaf, gagal memproses teks struk. Coba lagi. 🙏"


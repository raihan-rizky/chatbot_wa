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


# ── Receipt extraction prompt ───────────────────────────────────
RECEIPT_SYSTEM_PROMPT = """You are an expert OCR assistant.
Your task is to extract data from shopping receipts into STRICT JSON format and categorize items.

Categories:
1. Spanduk: Banners, baliho, large format printing, outdoor ads.
2. Percetakan: General printing (invitations, flyers, brochures, stickers, business cards), offset.
3. ATK: Stationery, office supplies, pens, paper, books, etc.

JSON Schema:
{
  "no_nota": "ID of transaction or '0'",
  "spanduk_items": ["Item Name (qty)", "Item Name (qty)"],
  "percetakan_items": ["Item Name (qty)", "Item Name (qty)"],
  "atk_items": ["Item Name (qty)", "Item Name (qty)"],
  "total": "Total amount (numeric string) or '0'"
}

Rules:
1. Output MUST be valid JSON only. Do not add markdown blocks like ```json.
2. If a category has no items, return an empty list [].
3. If a field is missing, use "Tidak Diketahui" (or "0" for amount).
4. Classify each item into the most appropriate category based on its name.
5. Do not include any conversational text.
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

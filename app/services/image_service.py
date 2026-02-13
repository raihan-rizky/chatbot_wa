"""Image service — download WhatsApp media & extract receipt info via vision model."""

from __future__ import annotations

import base64
import logging

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
            temperature=0.3,  # lower temp for structured extraction
            max_tokens=2048,
        )
    return _vision_llm


# ── Receipt extraction prompt ───────────────────────────────────
RECEIPT_SYSTEM_PROMPT = """You are an expert OCR assistant specializing in reading shopping receipts (struk belanja).
When given an image of a receipt, extract ALL information and present it in this structured format:

🏪 **Nama Toko:** [store name]
📍 **Alamat:** [address if visible]
📅 **Tanggal:** [date if visible]

🛒 **Daftar Belanja:**
1. [item name] — Rp [price] — [Item Quantity]
2. [item name] — Rp [price] — [Item Quantity]
...

💰 **Subtotal:** Rp [subtotal]
🏷️ **Diskon:** Rp [discount if any]
💵 **Total:** Rp [total]
💳 **Pembayaran:** [payment method if visible]
💰 **Kembalian:** Rp [change if visible]

If any field is not visible or unclear, write "Tidak terlihat".
Always use Indonesian Rupiah (Rp) format for prices.
Be precise with numbers — double-check amounts from the image."""

GENERAL_IMAGE_PROMPT = """You are a helpful AI assistant that can analyze images.
Describe what you see in the image and extract any relevant information.
If the image contains text, read and transcribe it.
Respond in the same language as any text found, or in Indonesian by default."""


async def download_wa_media(media_id: str) -> bytes:
    """Download media from WhatsApp Cloud API.

    Steps:
        1. GET media URL using the media_id
        2. Download the actual file from the URL

    Args:
        media_id: The WhatsApp media ID from the webhook payload.

    Returns:
        The raw image bytes.
    """
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


async def analyze_image(image_bytes: bytes, caption: str | None = None) -> str:
    """Analyze an image using the Nebius vision model.

    Args:
        image_bytes: Raw image data.
        caption: Optional caption/context from the user.

    Returns:
        The AI-generated analysis as a string.
    """
    llm = _get_vision_llm()

    # Encode image to base64
    b64_image = base64.b64encode(image_bytes).decode("utf-8")

    # Determine prompt based on caption
    if caption and any(kw in caption.lower() for kw in ["struk", "receipt", "nota", "belanja", "kasir"]):
        system_prompt = RECEIPT_SYSTEM_PROMPT
        user_text = f"Tolong ekstrak informasi dari struk belanja ini. Catatan user: {caption}"
    elif caption:
        system_prompt = GENERAL_IMAGE_PROMPT
        user_text = caption
    else:
        # Default: try receipt extraction first, fall back to general
        system_prompt = RECEIPT_SYSTEM_PROMPT
        user_text = "Tolong analisa gambar ini. Jika ini struk belanja, ekstrak semua informasinya. Jika bukan struk, jelaskan isi gambar."

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
        return response.content  # type: ignore[return-value]
    except Exception:
        logger.exception("Vision model call failed")
        return "Maaf, saya gagal menganalisa gambar ini. Coba kirim ulang dengan kualitas lebih baik. 🙏"

"""WhatsApp webhook routes — verification & incoming messages."""

from __future__ import annotations

import logging
import traceback

from fastapi import APIRouter, Query, Request, Response

from app.config import get_settings
from app.services.llm_service import get_ai_response
from app.services.image_service import analyze_image, download_wa_media
from app.services.whatsapp import send_message

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Webhook verification (called once by Meta) ──────────────────
@router.get("/webhook")
async def verify_webhook(
    response: Response,
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    """Handle the WhatsApp webhook verification handshake."""
    settings = get_settings()

    if hub_mode == "subscribe" and hub_verify_token == settings.whatsapp_verify_token:
        logger.info("Webhook verified successfully ✅")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed — token mismatch")
    response.status_code = 403
    return {"error": "Verification failed"}


# ── Incoming messages ────────────────────────────────────────────
@router.post("/webhook")
async def receive_message(request: Request):
    """Receive incoming WhatsApp messages and process replies."""
    body = await request.json()

    logger.info("📨 Webhook payload received")

    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                if "messages" not in value:
                    logger.info("No messages in payload, skipping")
                    continue

                for message in value["messages"]:
                    sender = message["from"]
                    msg_type = message.get("type")

                    if msg_type == "text":
                        await _handle_text(sender, message)
                    elif msg_type == "image":
                        await _handle_image(sender, message)
                    else:
                        logger.info("Skipping message type=%s from %s", msg_type, sender)

    except Exception:
        logger.error("❌ Error processing webhook:\n%s", traceback.format_exc())

    return {"status": "ok"}


async def _handle_text(phone: str, message: dict) -> None:
    """Handle a text message — generate AI reply."""
    text = message["text"]["body"]
    logger.info("📩 Text from %s: %s", phone, text[:80])

    try:
        reply = await get_ai_response(phone, text)
        logger.info("🤖 AI reply for %s: %s", phone, reply[:80])
        await send_message(phone, reply)
        logger.info("✅ Reply sent to %s", phone)
    except Exception:
        logger.error("❌ Failed to reply to %s:\n%s", phone, traceback.format_exc())


async def _handle_image(phone: str, message: dict) -> None:
    """Handle an image message — download, OCR, extract receipt info."""
    media_id = message["image"]["id"]
    caption = message.get("image", {}).get("caption")

    logger.info("🖼️ Image from %s (media_id=%s, caption=%s)", phone, media_id, caption)

    try:
        # Download image from WhatsApp
        image_bytes = await download_wa_media(media_id)
        logger.info("📥 Downloaded image: %d bytes", len(image_bytes))

        # Analyze with vision model
        result = await analyze_image(image_bytes, caption)
        logger.info("🔍 Analysis done for %s: %s", phone, result[:80])

        # Send result back
        await send_message(phone, result)
        logger.info("✅ Image analysis sent to %s", phone)

    except Exception:
        logger.error("❌ Failed to process image from %s:\n%s", phone, traceback.format_exc())
        await send_message(phone, "Maaf, gagal memproses gambar. Coba kirim ulang dengan kualitas lebih baik. 🙏")

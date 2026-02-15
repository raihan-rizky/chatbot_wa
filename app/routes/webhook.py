"""WhatsApp webhook routes — verification & incoming messages."""

from __future__ import annotations

import asyncio
import logging
import traceback

from fastapi import APIRouter, Query, Request, Response

from app.config import get_settings
from app.services.llm_service import get_ai_response
from app.services.chat_history import save_message
from app.services.image_service import analyze_image, download_wa_media
from app.services.whatsapp import send_message
from app.services.sheets import append_log, append_receipt_data, clear_sheet
from app.services.chat_history import clear_history

logger = logging.getLogger(__name__)

router = APIRouter()

# Track processed message IDs to avoid duplicates
_processed_ids: set[str] = set()


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
        logger.info("Webhook verified successfully")
        return Response(content=hub_challenge, media_type="text/plain")

    logger.warning("Webhook verification failed")
    response.status_code = 403
    return {"error": "Verification failed"}


# ── Incoming messages ────────────────────────────────────────────
@router.post("/webhook")
async def receive_message(request: Request):
    """Receive incoming WhatsApp messages and process replies."""
    body = await request.json()

    logger.info("Webhook payload received")

    try:
        for entry in body.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                if "messages" not in value:
                    continue

                # Collect images per sender for batch processing
                sender_images: dict[str, list[dict]] = {}

                for message in value["messages"]:
                    msg_id = message.get("id", "")
                    sender = message["from"]
                    msg_type = message.get("type")

                    # Deduplicate
                    if msg_id in _processed_ids:
                        logger.info("Skipping duplicate message %s", msg_id)
                        continue
                    _processed_ids.add(msg_id)
                    if len(_processed_ids) > 1000:
                        _processed_ids.clear()

                    logger.info("Message from %s type=%s id=%s", sender, msg_type, msg_id)

                    if msg_type == "text":
                        await _handle_text(sender, message)
                    elif msg_type == "image":
                        sender_images.setdefault(sender, []).append(message)
                    else:
                        logger.info("Skipping unsupported type=%s", msg_type)

                # Process batched images per sender
                for sender, images in sender_images.items():
                    await _handle_images(sender, images)

    except Exception:
        logger.error("Error processing webhook:\n%s", traceback.format_exc())

    return {"status": "ok"}


# ── Commands that users can type ─────────────────────────────────
_RESET_COMMANDS = {"/hapus", "/reset", "/clear"}


async def _handle_text(phone: str, message: dict) -> None:
    """Handle a text message — generate AI reply and save to Supabase."""
    text = message["text"]["body"]
    logger.info("Text from %s: %s", phone, text[:80])

    # ── Check for reset command ──────────────────────────────────
    if text.strip().lower() in _RESET_COMMANDS:
        await _handle_reset(phone)
        return

    try:
        reply = await get_ai_response(phone, text)
        logger.info("AI reply ready, sending to %s", phone)
        await send_message(phone, reply)
        logger.info("Reply sent to %s", phone)
    except Exception:
        logger.error("Failed to reply to %s:\n%s", phone, traceback.format_exc())
        try:
            await send_message(phone, "Maaf, terjadi kesalahan. Coba kirim ulang pesan kamu. 🙏")
        except Exception:
            pass


async def _handle_reset(phone: str) -> None:
    """Delete all chat history (Supabase) and spreadsheet data (Google Sheets)."""
    logger.info("Reset requested by %s", phone)

    try:
        # 1. Clear Supabase chat history
        await clear_history(phone)
        chat_ok = True
    except Exception:
        logger.error("Failed to clear chat history for %s", phone, exc_info=True)
        chat_ok = False

    # 2. Clear Google Sheets data
    sheet_ok = clear_sheet()

    # 3. Send confirmation
    if chat_ok and sheet_ok:
        reply = (
            "✅ *Semua data berhasil dihapus!*\n\n"
            "💬 Riwayat chat — dihapus\n"
            "📊 Data spreadsheet — dihapus\n\n"
            "Silakan mulai percakapan baru. 👋"
        )
    elif chat_ok:
        reply = (
            "⚠️ *Sebagian data dihapus*\n\n"
            "💬 Riwayat chat — dihapus\n"
            "❌ Data spreadsheet — gagal dihapus\n\n"
            "Coba kirim /hapus lagi nanti."
        )
    elif sheet_ok:
        reply = (
            "⚠️ *Sebagian data dihapus*\n\n"
            "❌ Riwayat chat — gagal dihapus\n"
            "📊 Data spreadsheet — dihapus\n\n"
            "Coba kirim /hapus lagi nanti."
        )
    else:
        reply = (
            "❌ *Gagal menghapus data*\n\n"
            "Terjadi kesalahan saat menghapus. Coba lagi nanti. 🙏"
        )

    await send_message(phone, reply)


async def _handle_images(phone: str, messages: list[dict]) -> None:
    """Handle one or more image messages from the same sender.

    Features:
        - Rate limiting: enforces max_images_per_request
        - Concurrent downloads: fetches all media in parallel
        - Individual analysis: each image analyzed separately (receipts need own JSON)
        - Each receipt logged to Google Sheets individually
        - Consolidated reply: one WhatsApp message combining all results
    """
    settings = get_settings()
    max_images = settings.max_images_per_request

    # ── Rate limiting ────────────────────────────────────────────
    if len(messages) > max_images:
        logger.warning(
            "User %s sent %d images, capping to %d", phone, len(messages), max_images
        )
        await send_message(
            phone,
            f"⚠️ Maksimal {max_images} gambar sekaligus ya. "
            f"Hanya {max_images} gambar pertama yang akan diproses.",
        )
        messages = messages[:max_images]

    # ── Single image fast path ───────────────────────────────────
    if len(messages) == 1:
        await _handle_single_image(phone, messages[0])
        return

    # ── Multi-image processing ───────────────────────────────────
    media_ids = [msg["image"]["id"] for msg in messages]
    captions = [msg.get("image", {}).get("caption") for msg in messages]

    logger.info("Batch processing %d images from %s", len(messages), phone)

    try:
        # Save user messages to Supabase
        for i, media_id in enumerate(media_ids):
            user_content = captions[i] if captions[i] else f"[Gambar {i + 1} dikirim]"
            await save_message(phone, "user", user_content, image_url=f"wa_media:{media_id}")

        # Concurrent downloads
        download_tasks = [download_wa_media(mid) for mid in media_ids]
        download_results = await asyncio.gather(*download_tasks, return_exceptions=True)

        # Analyze each image individually and collect results
        reply_parts: list[str] = []
        for i, dl_result in enumerate(download_results):
            img_label = f"📷 *Gambar {i + 1}:*"

            if isinstance(dl_result, Exception):
                logger.error(
                    "Failed to download image %d (media_id=%s): %s",
                    i + 1, media_ids[i], dl_result,
                )
                reply_parts.append(f"{img_label}\n⚠️ Gagal mengunduh gambar ini.")
                continue

            # Analyze with vision model
            result = await analyze_image(dl_result, captions[i])

            # Log to Google Sheets & format reply
            if isinstance(result, dict):
                append_receipt_data(result)

                spanduk_count = len(result.get("spanduk_items", []))
                percetakan_count = len(result.get("percetakan_items", []))
                atk_count = len(result.get("atk_items", []))

                reply_parts.append(
                    f"{img_label}\n"
                    f"✅ Data Struk Berhasil Disimpan\n"
                    f"🧾 No. Nota: {result.get('no_nota')}\n"
                    f"💰 Total: {result.get('total')}\n"
                    f"📦 Rincian: Spanduk({spanduk_count}) "
                    f"Percetakan({percetakan_count}) ATK({atk_count})"
                )
            else:
                append_log(phone, "assistant", result)
                reply_parts.append(f"{img_label}\n{result}")

        # Build consolidated reply
        consolidated = "\n\n".join(reply_parts)

        # Add sheet link if any receipt was found
        has_receipt = any("Data Struk" in part for part in reply_parts)
        if has_receipt:
            consolidated += (
                "\n\n_Cek Google Sheet dibawah untuk detail lengkap._ \n"
                "https://bit.ly/ExcelTeladanAI"
            )

        # Save AI response & send one consolidated reply
        await save_message(phone, "assistant", consolidated)
        await send_message(phone, consolidated)
        logger.info("Batch analysis sent to %s (%d images)", phone, len(messages))

    except Exception:
        logger.error("Failed to batch-process images from %s:\n%s", phone, traceback.format_exc())
        try:
            await send_message(phone, "Maaf, gagal memproses gambar. Coba kirim ulang. 🙏")
        except Exception:
            pass


async def _handle_single_image(phone: str, message: dict) -> None:
    """Handle a single image message — download, OCR, extract info, save to Supabase."""
    media_id = message["image"]["id"]
    caption = message.get("image", {}).get("caption")

    logger.info("Image from %s (media_id=%s)", phone, media_id)

    try:
        # Save user image message to Supabase
        user_content = caption if caption else "[Gambar dikirim]"
        await save_message(phone, "user", user_content, image_url=f"wa_media:{media_id}")

        # Download image from WhatsApp
        image_bytes = await download_wa_media(media_id)
        logger.info("Downloaded image: %d bytes", len(image_bytes))

        # Analyze with vision model
        result = await analyze_image(image_bytes, caption)
        logger.info("Analysis done for %s", phone)

        # Save AI response to Supabase
        await save_message(phone, "assistant", result)

        # Log to Google Sheets
        if isinstance(result, dict):
            append_receipt_data(result)

            # Calculate counts
            spanduk_count = len(result.get('spanduk_items', []))
            percetakan_count = len(result.get('percetakan_items', []))
            atk_count = len(result.get('atk_items', []))

            # Format result for WhatsApp reply
            reply_text = (
                f"✅ *Data Struk Berhasil Disimpan*\n\n"
                f"🧾 No. Nota: {result.get('no_nota')}\n"
                f"💰 Total: {result.get('total')}\n\n"
                f"📦 *Rincian Item:*\n"
                f"- Spanduk: {spanduk_count}\n"
                f"- Percetakan: {percetakan_count}\n"
                f"- ATK: {atk_count}\n\n"
                "_Cek Google Sheet dibawah untuk detail lengkap._ \n"
                "https://bit.ly/ExcelTeladanAI"
            )
        else:
            append_log(phone, "assistant", result)
            reply_text = result

        # Send result back via WhatsApp
        await send_message(phone, reply_text)
        logger.info("Image analysis sent to %s", phone)

    except Exception:
        logger.error("Failed to process image from %s:\n%s", phone, traceback.format_exc())
        try:
            await send_message(phone, "Maaf, gagal memproses gambar. Coba kirim ulang. 🙏")
        except Exception:
            pass

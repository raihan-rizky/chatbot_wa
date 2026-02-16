"""WhatsApp webhook routes — verification & incoming messages."""

from __future__ import annotations

import asyncio
import logging
import traceback

from fastapi import APIRouter, Query, Request, Response

from app.config import get_settings
from app.services.llm_service import get_ai_response
from app.services.chat_history import save_message, get_history
from app.services.image_service import analyze_image, download_wa_media, parse_text_to_receipt
from app.services.pdf_service import generate_receipt_pdf
from app.services.whatsapp import send_document, send_message, upload_media
from app.services.sheets import append_log, append_receipt_data, clear_sheet, overwrite_receipt_data
from app.services.chat_history import clear_history
from app.services.receipt_service import save_receipt_items, get_todays_receipts, generate_next_transaction_id, get_product_by_code

import re

logger = logging.getLogger(__name__)

router = APIRouter()

# Track processed message IDs to avoid duplicates
_processed_ids: set[str] = set()


def _calc_grand_total(data: dict) -> str:
    """Auto-calculate grand total from items (qty × price) and format it."""
    total = 0
    for item in data.get("items", []):
        qty_raw = item.get("quantity") or item.get("jumlah") or "1"
        price_raw = item.get("price_per_item") or item.get("harga_satuan") or item.get("harga") or 0
        # Extract numeric values
        qty_clean = re.sub(r"[^\d.]", "", str(qty_raw).replace(",", ""))
        price_clean = str(price_raw).replace("Rp", "").replace(".", "").replace(",", "").strip()
        price_match = re.search(r"[\d]+", price_clean)
        try:
            qty_num = float(qty_clean) if qty_clean else 0
            price_num = float(price_match.group()) if price_match else 0
            total += int(qty_num * price_num)
        except (ValueError, TypeError):
            pass
    return f"{total:,}".replace(",", ".")


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
_SYNC_COMMANDS = {"/spreadsheet", "/sync", "/excel"}
_HELP_COMMANDS = {"/help", "/bantuan", "/guide", "/panduan"}

# ── Welcome guide for first-time users ────────────────────────────
_WELCOME_GUIDE = (
    "👋 *Halo! Selamat datang di Toko Teladan AI* 🤖\n\n"
    "Saya asisten digital yang siap membantu kamu mencatat transaksi toko.\n\n"
    "📋 *Perintah yang tersedia:*\n\n"
    "📝 */struk* — Buat struk digital\n"
    "Contoh:\n"
    "```\n"
    "/struk Pelanggan Budi Santoso\n"
    "1. SPF-280 3x1m 10pcs mata ayam per 50cm\n"
    "2. SPF-340 2x1m 5pcs polos\n"
    "3. SPF-510 4x2m 1pcs selongsong atas bawah\n"
    "DP 50000 QRIS\n"
    "```\n\n"
    "📊 */spreadsheet* — Sinkronisasi data ke Google Sheet\n"
    "🗑️ */hapus* — Hapus semua riwayat chat & data\n\n"
    "🖨️ *Kode Produk:*\n"
    "• SPF-280 = Flexi 280gr (China)\n"
    "• SPF-340 = Flexi 340gr (Korea)\n"
    "• SPF-510 = Flexi 510gr (Jerman)\n"
    "• SP-PVC = Cetak PVC Rigid \n"
    "• SP-LUS = Cetak Luster \n"
    "• ST-VIN = Cetak Stiker Vinyl \n"
    "• ST-ONE = Cetak Stiker One Way Vision \n\n"
    "📸 Kamu juga bisa kirim *foto struk* dan saya akan membacanya otomatis!\n\n"
    "Silakan kirim pesan atau perintah untuk memulai. 😊"
)

async def _handle_text(phone: str, message: dict) -> None:
    """Handle a text message — generate AI reply and save to Supabase."""
    text = message["text"]["body"]
    logger.info("Text from %s: %s", phone, text[:80])

    # ── Welcome guide for first-time users ────────────────────────
    try:
        history = await get_history(phone, limit=1)
        if not history:
            await send_message(phone, _WELCOME_GUIDE)
            logger.info("Sent welcome guide to new user %s", phone)
    except Exception:
        logger.warning("Failed to check history for welcome guide", exc_info=True)

    # ── Check for commands ────────────────────────────────────────
    stripped = text.strip()

    if stripped.lower() in _HELP_COMMANDS:
        await send_message(phone, _WELCOME_GUIDE)
        logger.info("Sent help guide to %s", phone)
        return

    if stripped.lower() in _RESET_COMMANDS:
        await _handle_reset(phone)
        return

    if stripped.lower() in _SYNC_COMMANDS:
        await _handle_sync(phone)
        return

    if stripped.lower().startswith("/struk"):
        receipt_text = stripped[6:].strip()  # text after "/struk"
        await _handle_struk(phone, receipt_text)
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
    await send_message(phone, "🔄 *Permintaan diterima.* Sedang menghapus data...")

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


async def _handle_sync(phone: str) -> None:
    """Fetch today's receipts from Supabase and overwrite Google Sheet."""
    logger.info("Sync to spreadsheet requested by %s", phone)
    await send_message(phone, "🔄 *Permintaan diterima.* Sedang sinkronisasi ke Google Sheet...")

    try:
        # 1. Fetch today's receipts from Supabase
        receipts = await get_todays_receipts()

        # 2. Overwrite Google Sheet with fresh data
        success = overwrite_receipt_data(receipts)

        if success:
            reply = (
                "✅ *Google Sheet berhasil diperbarui!*\n\n"
                f"📊 Total {len(receipts)} baris data hari ini.\n\n"
                "_Cek Google Sheet:_\n"
                "https://bit.ly/ExcelTeladanAI"
            )
        else:
            reply = (
                "❌ *Gagal memperbarui Google Sheet.*\n\n"
                "Pastikan koneksi Google Sheets sudah diatur dengan benar. 🙏"
            )
    except Exception:
        logger.error("Failed to sync spreadsheet for %s", phone, exc_info=True)
        reply = "❌ Terjadi kesalahan saat sinkronisasi. Coba lagi nanti. 🙏"

    await send_message(phone, reply)


async def _handle_struk(phone: str, text: str) -> None:
    """Convert user-typed text into a digital receipt PDF and send it back."""
    logger.info("Struk command from %s: %s", phone, text[:80])
    await send_message(phone, "🔄 *Permintaan diterima.* Struk digital sedang dibuat...")

    if not text:
        await send_message(
            phone,
            "📝 *Cara pakai /struk:*\n\n"
            "Ketik data struk setelah /struk, contoh:\n"
            "/struk Nota 123, Spanduk 2x3m 50000, Pulpen 2pcs 10000, total 60000",
        )
        return

    try:
        # Pre-process text: generic lookup for product codes
        # Split text by words and check if any word matches a product code
        words = text.split()
        refined_words = []
        
        for word in words:
            # clean punctuation
            clean_word = word.strip(",.-").upper()
            product = await get_product_by_code(clean_word)
            if product:
                # Replace code with full details: "Name (Rp Price)"
                # This guides the LLM to use the correct name and unit price
                refined_words.append(f'{product["name"]} (Rp {product["price"]})')
            else:
                refined_words.append(word)
        
        refined_text = " ".join(refined_words)
        logger.info("Refined text for LLM: %s", refined_text)

        # Parse text into receipt JSON via LLM (stateless, no chat history)
        result = await parse_text_to_receipt(refined_text)

        if isinstance(result, dict):
            # Auto-generate transaction ID if LLM couldn't extract one
            nota = result.get("no_nota", "Tidak Diketahui")
            if not nota or nota == "Tidak Diketahui":
                result["no_nota"] = await generate_next_transaction_id()

            # 1. Save to Supabase (Primary)
            await save_receipt_items(result)

            # NOTE: Auto-save to Google Sheets is DISABLED.
            # Use /spreadsheet command to sync.

            # Item count
            item_count = len(result.get("items", []))

            # Text summary
            reply_text = (
                f"✅ *Struk Digital Berhasil Dibuat*\n\n"
                f"🧾 No. Nota: {result.get('no_nota')}\n"
                f"📦 Jumlah Item: {item_count}\n"
                f"💰 Total: Rp {_calc_grand_total(result)}\n\n"
                "_Jika ingin update Google Sheet ketik \spreadsheet._ \n"
                "https://bit.ly/ExcelTeladanAI"
            )

            await send_message(phone, reply_text)

            # Generate and send PDF
            try:
                pdf_bytes = generate_receipt_pdf(result)
                nota_id = result.get("no_nota", "receipt")
                pdf_filename = f"Struk_{nota_id}.pdf"
                wa_media_id = await upload_media(pdf_bytes, "application/pdf", pdf_filename)
                await send_document(phone, wa_media_id, pdf_filename, caption="📄 Struk digital kamu")
                logger.info("Struk PDF sent to %s", phone)
            except Exception:
                logger.error("Failed to send struk PDF to %s", phone, exc_info=True)
        else:
            await send_message(phone, result)

    except Exception:
        logger.error("Failed /struk for %s:\n%s", phone, traceback.format_exc())
        try:
            await send_message(phone, "Maaf, gagal membuat struk digital. Coba lagi. 🙏")
        except Exception:
            pass


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
                # Auto-generate transaction ID if LLM couldn't extract one
                nota = result.get("no_nota", "Tidak Diketahui")
                if not nota or nota == "Tidak Diketahui":
                    result["no_nota"] = await generate_next_transaction_id()

                # 1. Save to Supabase (Primary)
                await save_receipt_items(result)

                # NOTE: Auto-save to Sheets DISABLED. Use /spreadsheet to sync.

                item_count = len(result.get("items", []))

                reply_parts.append(
                    f"{img_label}\n"
                    f"✅ Data Struk Berhasil Disimpan\n"
                    f"🧾 No. Nota: {result.get('no_nota')}\n"
                    f"📦 Item: {item_count}\n"
                    f"💰 Total: Rp {_calc_grand_total(result)}"
                )

                # Generate and send PDF receipt
                try:
                    pdf_bytes = generate_receipt_pdf(result)
                    nota_id = result.get('no_nota', 'receipt')
                    pdf_filename = f"Struk_{nota_id}.pdf"
                    wa_media_id = await upload_media(pdf_bytes, "application/pdf", pdf_filename)
                    await send_document(phone, wa_media_id, pdf_filename, caption="📄 Struk digital kamu")
                except Exception:
                    logger.error("Failed to send PDF for image %d", i + 1, exc_info=True)
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
            # Auto-generate transaction ID if LLM couldn't extract one
            nota = result.get("no_nota", "Tidak Diketahui")
            if not nota or nota == "Tidak Diketahui":
                result["no_nota"] = await generate_next_transaction_id()

            # 1. Save to Supabase (Primary)
            await save_receipt_items(result)

            # NOTE: Auto-save to Sheets DISABLED. Use /spreadsheet to sync.

            # Item count
            item_count = len(result.get('items', []))

            # Format result for WhatsApp reply
            reply_text = (
                f"✅ *Data Struk Berhasil Disimpan*\n\n"
                f"🧾 No. Nota: {result.get('no_nota')}\n"
                f"📦 Jumlah Item: {item_count}\n"
                f"💰 Total: Rp {_calc_grand_total(result)}\n\n"
                "_Cek Google Sheet dibawah untuk detail lengkap._ \n"
                "https://bit.ly/ExcelTeladanAI"
            )
        else:
            append_log(phone, "assistant", result)
            reply_text = result

        # Send text reply
        await send_message(phone, reply_text)

        # Generate and send PDF if receipt was detected
        if isinstance(result, dict):
            try:
                pdf_bytes = generate_receipt_pdf(result)
                nota_id = result.get('no_nota', 'receipt')
                pdf_filename = f"Struk_{nota_id}.pdf"
                wa_media_id = await upload_media(pdf_bytes, "application/pdf", pdf_filename)
                await send_document(phone, wa_media_id, pdf_filename, caption="📄 Struk digital kamu")
                logger.info("PDF receipt sent to %s", phone)
            except Exception:
                logger.error("Failed to send PDF to %s", phone, exc_info=True)
        logger.info("Image analysis sent to %s", phone)

    except Exception:
        logger.error("Failed to process image from %s:\n%s", phone, traceback.format_exc())
        try:
            await send_message(phone, "Maaf, gagal memproses gambar. Coba kirim ulang. 🙏")
        except Exception:
            pass

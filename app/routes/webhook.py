"""WhatsApp webhook routes — incoming messages (WAHA format)."""

from __future__ import annotations

import asyncio
import logging
import traceback

from fastapi import APIRouter, Request

from app.config import get_settings
from app.services.llm_service import get_ai_response, generate_daily_report
from app.services.chat_history import save_message, get_history
from app.services.image_service import analyze_image, download_wa_media, parse_text_to_receipt
from app.services.pdf_service import generate_receipt_pdf
from app.services.whatsapp import send_document, send_message
from app.services.sheets import append_log, clear_sheet, overwrite_receipt_data
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


# ── Incoming messages ────────────────────────────────────────────
@router.post("/webhook")
async def receive_message(request: Request):
    """Receive incoming WhatsApp messages (WAHA format) and process replies."""
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    event = body.get("event")
    if event != "message":
        return {"status": "ok"}

    payload = body.get("payload", {})
    if not payload:
        return {"status": "ok"}

    msg_id = payload.get("id", "")
    sender_jid = payload.get("from", "")

    # Ignore group messages and status broadcasts
    if "@g.us" in sender_jid or "status@broadcast" in sender_jid:
        return {"status": "ok"}

    # Ignore messages sent by the bot itself
    if payload.get("fromMe", False):
        return {"status": "ok"}

    sender = sender_jid.replace("@c.us", "")

    # Deduplicate
    if msg_id in _processed_ids:
        logger.info("Skipping duplicate message %s", msg_id)
        return {"status": "ok"}
    _processed_ids.add(msg_id)
    if len(_processed_ids) > 1000:
        _processed_ids.clear()

    msg_type = payload.get("type", "chat")
    has_media = payload.get("hasMedia", False)

    logger.info("Webhook from %s type=%s has_media=%s id=%s", sender, msg_type, has_media, msg_id)

    try:
        if has_media or msg_type == "image":
            await _handle_single_image(sender, payload)
        elif msg_type == "chat":
            text = payload.get("body", "")
            if text:
                await _handle_text(sender, text)
        else:
            logger.info("Skipping unsupported message type: %s", msg_type)
    except Exception:
        logger.error("Error processing webhook:\n%s", traceback.format_exc())

    return {"status": "ok"}


# ── Commands that users can type ─────────────────────────────────
_RESET_COMMANDS = {"/hapus", "/reset", "/clear"}
_SYNC_COMMANDS = {"/spreadsheet", "/sync", "/excel"}
_REPORT_COMMANDS = {"/dailyreport", "/report", "/laporan", "/rekap"}
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
    "📈 */dailyreport* — Laporan penjualan hari ini\n"
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

async def _handle_text(phone: str, text: str) -> None:
    """Handle a text message — generate AI reply and save to Supabase."""
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

    if stripped.lower() in _REPORT_COMMANDS:
        await _handle_daily_report(phone)
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
        words = text.split()
        refined_words = []
        
        for word in words:
            clean_word = word.strip(",.-").upper()
            product = await get_product_by_code(clean_word)
            if product:
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

            # Item count
            item_count = len(result.get("items", []))

            # Text summary
            reply_text = (
                f"✅ *Struk Digital Berhasil Dibuat*\n\n"
                f"🧾 No. Nota: {result.get('no_nota')}\n"
                f"📦 Jumlah Item: {item_count}\n"
                f"💰 Total: Rp {_calc_grand_total(result)}\n\n"
                "_Jika ingin update Google Sheet ketik /spreadsheet._ \n"
                "https://bit.ly/ExcelTeladanAI"
            )

            await send_message(phone, reply_text)

            # Generate and send PDF
            try:
                pdf_bytes = generate_receipt_pdf(result)
                nota_id = result.get("no_nota", "receipt")
                pdf_filename = f"Struk_{nota_id}.pdf"
                await send_document(phone, pdf_bytes, "application/pdf", pdf_filename, caption="📄 Struk digital kamu")
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


async def _handle_single_image(phone: str, payload: dict) -> None:
    """Handle a single image message (WAHA) — download, OCR, extract info, save to Supabase."""
    msg_id = payload.get("id")
    # In WAHA, caption is often stored in 'body' for media messages.
    caption = payload.get("body", "")

    logger.info("Media from %s (msg_id=%s)", phone, msg_id)

    try:
        # Save user image message to Supabase
        user_content = caption if caption else "[Gambar dikirim]"
        await save_message(phone, "user", user_content, image_url=f"wa_media:{msg_id}")

        # Download image from WAHA API
        image_bytes = await download_wa_media(msg_id)
        if not image_bytes:
            logger.error("Failed to download image %s", msg_id)
            await send_message(phone, "Maaf, gagal mengunduh gambar ini. Coba kirim ulang.")
            return

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
                await send_document(phone, pdf_bytes, "application/pdf", pdf_filename, caption="📄 Struk digital kamu")
                logger.info("PDF receipt sent to %s", phone)
            except Exception:
                logger.error("Failed to send PDF to %s", phone, exc_info=True)
        logger.info("Image analysis sent to %s", phone)

    except Exception:
        logger.error("Failed to process media from %s:\n%s", phone, traceback.format_exc())
        try:
            await send_message(phone, "Maaf, gagal memproses gambar. Coba kirim ulang. 🙏")
        except Exception:
            pass


async def _handle_daily_report(phone: str) -> None:
    """Generate and send today's sales report."""
    logger.info("Daily report requested by %s", phone)
    await send_message(phone, "🔄 *Sedang membuat laporan hari ini...* Mohon tunggu sebentar.")

    try:
        # 1. Fetch today's receipts
        receipts = await get_todays_receipts()
        
        if not receipts:
            await send_message(phone, "📅 *Laporan Hari Ini*\n\nBelum ada transaksi yang tercatat hari ini.")
            return

        # 2. Generate analysis with LLM
        report = await generate_daily_report(receipts)
        
        # 3. Send report
        await send_message(phone, report)
        logger.info("Daily report sent to %s", phone)

    except Exception:
        logger.error("Failed to generate daily report for %s", phone, exc_info=True)
        try:
            await send_message(phone, "❌ Maaf, gagal membuat laporan. Coba lagi nanti. 🙏")
        except Exception:
            pass

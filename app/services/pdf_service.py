"""PDF service — generate digital receipt PDFs from structured data."""

from __future__ import annotations

import io
import logging
from datetime import datetime

from fpdf import FPDF
import httpx

logger = logging.getLogger(__name__)

"""PDF service — generate digital receipt PDFs from structured data."""

# ── Column widths (Total = 190, A5 landscape minus 20mm margins) ──
# No(8) + Item(40) + Size(15) + Mat(25) + Qty(12) + Price(25) + Total(25) + Notes(40) = 190

COL_NO = 8
COL_ITEM = 40
COL_SIZE = 15
COL_MATERIAL = 25
COL_QTY = 12
COL_PRICE = 25
COL_TOTAL = 25
COL_NOTES = 40
ROW_H = 10  # Row height for readability

class ReceiptPDF(FPDF):
    """Custom PDF layout matching the Media Stationery receipt template."""

    def header(self):
        # Header starts below logo area, but let's keep text here
        pass

        # Store name (Manual positioning)
        #self.set_xy(10, 7)
        #self.set_font("Helvetica", "B", 14)
        #self.cell(80, 6, "Toko Teladan", align="L")

        # Address line below store name
        self.set_xy(10, 16)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(0, 0, 0)
        self.cell(150, 7, "Jl. Temu Putih No.30 Cilegon | Telp: 0254 393022 | tokoteladancv@gmail.com", align="L")

        # Move below header block
        self.set_y(25)

        # Divider
        self.set_draw_color(180, 0, 0)
        self.set_line_width(0.8)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_line_width(0.2)
        self.ln(2)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, "Dokumen ini dibuat secara otomatis oleh Teladan AI", align="C")
        


def _parse_num(value: str | int | float) -> float:
    """Extract a numeric value from a string like '2 pcs', 'Rp 50.000', '3 rim', etc."""
    import re
    try:
        clean = str(value).replace("Rp", "").replace(".", "").replace(",", "").strip()
        # Extract the first number (int or float) from the string
        match = re.search(r'[\d]+(?:\.\d+)?', clean)
        return float(match.group()) if match else 0.0
    except (ValueError, TypeError):
        return 0.0


# ── Logo URL & cache ─────────────────────────────────────────────
LOGO_URL = "https://gcdnb.pbrd.co/images/gzifkw96PMAF.png"
_logo_cache: bytes | None = None


def _download_logo() -> bytes | None:
    """Download logo from URL and cache it in memory."""
    global _logo_cache
    if _logo_cache is not None:
        return _logo_cache
    try:
        resp = httpx.get(LOGO_URL, timeout=10.0, follow_redirects=True)
        resp.raise_for_status()
        _logo_cache = resp.content
        logger.info("Logo downloaded: %d bytes", len(_logo_cache))
        return _logo_cache
    except Exception:
        logger.warning("Failed to download logo from %s", LOGO_URL, exc_info=True)
        return None


def _fmt_number(value: str | int | float) -> str:
    """Format a numeric string with thousand separators, e.g. '150000' -> '150.000'."""
    try:
        # Handle "Rp 50.000" or similar inputs
        clean_val = str(value).replace("Rp", "").replace(".", "").replace(",", "").strip()
        num = int(float(clean_val))
        return f"{num:,}".replace(",", ".")
    except (ValueError, TypeError):
        return str(value)


def generate_receipt_pdf(data: dict) -> bytes:
    """Generate a receipt PDF matching the new schema fields."""
    logger.info("=== PDF Generation Start ===")
    logger.info("Input data keys: %s", list(data.keys()))
    logger.info("Raw items count: %d", len(data.get("items", [])))

    # ── Extract top-level down_payment BEFORE item loop ────────
    raw_dp = data.get("down_payment") or data.get("dp") or 0
    logger.info("Raw down_payment from data: %r", raw_dp)
    dp_num = _parse_num(raw_dp)
    logger.info("Parsed down_payment numeric: %s", dp_num)

    pdf = ReceiptPDF(orientation="L", unit="mm", format="A5")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Logo (downloaded from URL) ────────────────────────────────
    try:
        logo_bytes = _download_logo()
        if logo_bytes:
            logo_stream = io.BytesIO(logo_bytes)
            pdf.image(logo_stream, 10, 6, 60, 12)
    except Exception:
        logger.warning("Failed to add logo image", exc_info=True)

    # ── Receipt info ─────────────────────────────────────────────
    # Mappings from new schema or legacy schema
    trx_id = data.get("transaction_id") or data.get("no_nota") or "UNKNOWN"
    trx_date = data.get("transaction_date") or datetime.now().strftime("%Y-%m-%d")
    customer = data.get("customer_name") or data.get("nama_pelanggan") or "Pelanggan Umum"
    
    # Header Info (Left Aligned)
    pdf.set_font("Helvetica", "", 10)
    
    # Row 1: Transaction ID & Date
    pdf.cell(30, 6, "Transaction ID")
    pdf.cell(5, 6, ":")
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(200, 0, 0)  # Red
    pdf.cell(60, 6, str(trx_id))
    pdf.set_text_color(0, 0, 0)    # Reset
    
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(25, 6, "Date")
    pdf.cell(5, 6, ":")
    pdf.cell(0, 6, str(trx_date), new_x="LMARGIN", new_y="NEXT")

    # Row 2: Customer & Payment Method
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(30, 6, "Customer")
    pdf.cell(5, 6, ":")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(60, 6, str(customer))
    pdf.set_text_color(0, 0, 0)

    payment = data.get("payment_method") or "Cash"
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(25, 6, "Payment")
    pdf.cell(5, 6, ":")
    pdf.cell(0, 6, str(payment), new_x="LMARGIN", new_y="NEXT")
    
    pdf.ln(5)

    # ── Table header ─────────────────────────────────────────────
    # Columns: No, Item, Size, Material, Qty, Price, Total, Notes
    pdf.set_font("Helvetica", "B", 8) 
    pdf.set_fill_color(0, 0, 128)  # Navy
    pdf.set_text_color(255, 255, 255)
    
    header_h = 8
    
    pdf.cell(COL_NO, header_h, "No", border=1, fill=True, align="C")
    pdf.cell(COL_ITEM, header_h, "Item Name", border=1, fill=True, align="C")
    pdf.cell(COL_SIZE, header_h, "Size", border=1, fill=True, align="C")
    pdf.cell(COL_MATERIAL, header_h, "Material", border=1, fill=True, align="C")
    pdf.cell(COL_QTY, header_h, "Qty", border=1, fill=True, align="C")
    pdf.cell(COL_PRICE, header_h, "Price", border=1, fill=True, align="C")
    pdf.cell(COL_TOTAL, header_h, "Total", border=1, fill=True, align="C")
    pdf.cell(COL_NOTES, header_h, "Notes", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")
    
    pdf.set_text_color(0, 0, 0)

    # ── Table rows (fixed 4 rows per page) ───────────────────────
    FIXED_ROWS = 4
    items = data.get("items", [])
    pdf.set_font("Helvetica", "", 8)
    computed_grand_total = 0

    for idx in range(1, FIXED_ROWS + 1):
        # Alternate row colors
        if idx % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
            fill = True
        else:
            fill = False

        if idx <= len(items):
            item = items[idx - 1]

            # Extract Fields (support both new english keys, old indo keys, AND legacy LLM keys)
            name = str(item.get("item_name") or item.get("nama_barang") or item.get("keterangan") or "")
            size = str(item.get("size") or item.get("ukuran") or "-")
            material = str(item.get("material") or item.get("bahan") or "-")
            qty_raw = item.get("quantity") or item.get("jumlah") or "1"
            price_raw = item.get("price_per_item") or item.get("harga_satuan") or item.get("harga") or 0
            notes = str(item.get("notes") or item.get("keterangan") or "")

            # Auto-calculate: item total = qty × price
            qty_num = _parse_num(qty_raw)
            price_num = _parse_num(price_raw)
            item_total = int(qty_num * price_num) if qty_num and price_num else 0
            computed_grand_total += item_total

            # Truncate long text to fit column widths
            name = (name[:20] + '..') if len(name) > 20 else name
            size = (size[:8] + '..') if len(size) > 8 else size
            material = (material[:14] + '..') if len(material) > 14 else material
            notes = (notes[:24] + '..') if len(notes) > 24 else notes

            pdf.cell(COL_NO, ROW_H, str(idx), border=1, fill=fill, align="C")
            pdf.cell(COL_ITEM, ROW_H, name, border=1, fill=fill)
            pdf.cell(COL_SIZE, ROW_H, size, border=1, fill=fill, align="C")
            pdf.cell(COL_MATERIAL, ROW_H, material, border=1, fill=fill, align="C")
            pdf.cell(COL_QTY, ROW_H, str(qty_raw), border=1, fill=fill, align="C")
            pdf.cell(COL_PRICE, ROW_H, _fmt_number(price_raw), border=1, fill=fill, align="R")
            pdf.cell(COL_TOTAL, ROW_H, _fmt_number(item_total), border=1, fill=fill, align="R")
            pdf.cell(COL_NOTES, ROW_H, notes, border=1, fill=fill, align="L", new_x="LMARGIN", new_y="NEXT")
        else:
            # Empty row to fill up to FIXED_ROWS
            pdf.cell(COL_NO, ROW_H, "", border=1, fill=fill, align="C")
            pdf.cell(COL_ITEM, ROW_H, "", border=1, fill=fill)
            pdf.cell(COL_SIZE, ROW_H, "", border=1, fill=fill, align="C")
            pdf.cell(COL_MATERIAL, ROW_H, "", border=1, fill=fill, align="C")
            pdf.cell(COL_QTY, ROW_H, "", border=1, fill=fill, align="C")
            pdf.cell(COL_PRICE, ROW_H, "", border=1, fill=fill, align="R")
            pdf.cell(COL_TOTAL, ROW_H, "", border=1, fill=fill, align="R")
            pdf.cell(COL_NOTES, ROW_H, "", border=1, fill=fill, align="L", new_x="LMARGIN", new_y="NEXT")

    # ── Grand total row (auto-calculated) ─────────────────────────
    logger.info("Computed grand_total: %s", computed_grand_total)

    # ── DP Validation: cap at grand total ─────────────────────────
    if dp_num > computed_grand_total:
        logger.warning("DP (%s) exceeds grand total (%s), resetting DP to 0", dp_num, computed_grand_total)
        dp_num = 0

    dp_val = int(dp_num)
    sisa_val = computed_grand_total - dp_val
    logger.info("Final DP: %s, Sisa: %s, Grand Total: %s", dp_val, sisa_val, computed_grand_total)

    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    
    offset = COL_NO + COL_ITEM + COL_SIZE + COL_MATERIAL + COL_QTY + COL_PRICE
    pdf.cell(offset, 10, "GRAND TOTAL", align="R")
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(COL_TOTAL + COL_NOTES, 10, f"Rp {_fmt_number(computed_grand_total)}", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    # Row DP
    pdf.cell(offset, 10, "DP", align="R")
    pdf.set_fill_color(255, 255, 255)  # White bg for DP
    pdf.cell(COL_TOTAL + COL_NOTES, 10, f"Rp {_fmt_number(dp_val)}", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    # Row SISA
    pdf.cell(offset, 10, "SISA", align="R")
    pdf.set_fill_color(255, 255, 255)
    pdf.cell(COL_TOTAL + COL_NOTES, 10, f"Rp {_fmt_number(sisa_val)}", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    # ── Output as bytes ──────────────────────────────────────────
    buffer = io.BytesIO()
    pdf.output(buffer)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info("Generated detailed PDF (%d bytes) for %s", len(pdf_bytes), trx_id)
    return pdf_bytes

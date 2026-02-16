"""PDF service — generate digital receipt PDFs from structured data."""

from __future__ import annotations

import io
import logging
from datetime import datetime

from fpdf import FPDF

logger = logging.getLogger(__name__)

"""PDF service — generate digital receipt PDFs from structured data."""

# ── Column widths (Total ~190) ───────────────────────────────────
# New layout: 
# No (8), Item+Ket (50), Size (20), Material (20), 
# Qty (15), Price (25), Total (27), Notes (25) -> Too wide for portrait
# Let's optimize:
# No(8), Item(45), Size(18), Mat(18), Qty(10), Price(22), Total(25), Notes(24) = 170 + 20 margin = 190. Fits!

COL_NO = 8
COL_ITEM = 45
COL_SIZE = 18
COL_MATERIAL = 18
COL_QTY = 12
COL_PRICE = 28
COL_TOTAL = 28
COL_NOTES = 33
ROW_H = 10  # Increased row height for better readability

class ReceiptPDF(FPDF):
    """Custom PDF layout matching the Media Stationery receipt template."""

    def header(self):
        # Logo + Store info side by side
        try:
            self.image("app/public/images/toko_teladan-logo.png", 10, 6, 18)
        except Exception:
            pass

        # Store info next to logo
        self.set_xy(30, 6)
        self.set_font("Helvetica", "B", 14)
        self.cell(0, 5, "Toko Teladan", new_x="LMARGIN", new_y="NEXT", align="L")
        self.set_x(30)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(0, 0, 0)
        self.cell(0, 3, "Jl. Temu Putih No.30 Cilegon | Telp: 0254 393022 | tokoteladancv@gmail.com", new_x="LMARGIN", new_y="NEXT", align="L")

        self.ln(1)

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
    pdf = ReceiptPDF(orientation="L", unit="mm", format="A5")
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

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

    # Row 2: Customer
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(30, 6, "Customer")
    pdf.cell(5, 6, ":")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, str(customer), new_x="LMARGIN", new_y="NEXT")
    
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

    # ── Table rows ───────────────────────────────────────────────
    items = data.get("items", [])
    pdf.set_font("Helvetica", "", 8)
    computed_grand_total = 0

    for idx, item in enumerate(items, 1):
        # Alternate row colors
        if idx % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
            fill = True
        else:
            fill = False

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

        # Truncate long text to fit
        name = (name[:22] + '..') if len(name) > 22 else name
        notes = (notes[:15] + '..') if len(notes) > 15 else notes

        pdf.cell(COL_NO, ROW_H, str(idx), border=1, fill=fill, align="C")
        pdf.cell(COL_ITEM, ROW_H, name, border=1, fill=fill)
        pdf.cell(COL_SIZE, ROW_H, size, border=1, fill=fill, align="C")
        pdf.cell(COL_MATERIAL, ROW_H, material, border=1, fill=fill, align="C")
        pdf.cell(COL_QTY, ROW_H, str(qty_raw), border=1, fill=fill, align="C")
        pdf.cell(COL_PRICE, ROW_H, _fmt_number(price_raw), border=1, fill=fill, align="R")
        pdf.cell(COL_TOTAL, ROW_H, _fmt_number(item_total), border=1, fill=fill, align="R")
        pdf.cell(COL_NOTES, ROW_H, notes, border=1, fill=fill, align="L", new_x="LMARGIN", new_y="NEXT")

    # ── Grand total row (auto-calculated) ─────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    
    offset = COL_NO + COL_ITEM + COL_SIZE + COL_MATERIAL + COL_QTY + COL_PRICE
    pdf.cell(offset, 10, "GRAND TOTAL", align="R")
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(COL_TOTAL + COL_NOTES, 10, f"Rp {_fmt_number(computed_grand_total)}", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    # ── Output as bytes ──────────────────────────────────────────
    buffer = io.BytesIO()
    pdf.output(buffer)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info("Generated detailed PDF (%d bytes) for %s", len(pdf_bytes), trx_id)
    return pdf_bytes

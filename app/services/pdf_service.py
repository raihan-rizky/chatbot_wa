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
        # Logo
        # Try-except block to handle missing logo gracefully
        try:
            # 25mm width, starting at x=10, y=8
            self.image("app/public/images/toko_teladan-logo.png", 10, 8, 25)
        except Exception:
            # Fallback if logo missing: just print text or leave blank space
            pass

        # Address / Header Text (Below Logo)
        # Logo ends at y=33 (8+25). Start text at y=35.
        self.set_y(35)
        
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 10, "Toko Teladan", new_x="LMARGIN", new_y="NEXT", align="L")
        
        self.set_font("Helvetica", "", 8)
        self.set_text_color(0, 0, 0)
        self.cell(0, 4, "Jl. Temu Putih No.30 Cilegon", new_x="LMARGIN", new_y="NEXT", align="L")
        self.cell(0, 4, "Telp: 0254 393022 Fax: 389079", new_x="LMARGIN", new_y="NEXT", align="L")
        self.cell(0, 4, "Email : tokoteladancv@gmail.com", new_x="LMARGIN", new_y="NEXT", align="L")
        
        self.ln(2)

        # Divider
        self.set_draw_color(180, 0, 0)
        self.set_line_width(0.8)
        self.line(10, self.get_y(), 200, self.get_y())
        self.set_line_width(0.2)
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 10, "Dokumen ini dibuat secara otomatis oleh Teladan AI", align="C")


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

    for idx, item in enumerate(items, 1):
        # Alternate row colors
        if idx % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
            fill = True
        else:
            fill = False

        # Extract Fields (support both new english keys, old indo keys, AND legacy LLM keys)
        # Priority: item_name > nama_barang > keterangan (LLM uses this as item desc)
        name = str(item.get("item_name") or item.get("nama_barang") or item.get("keterangan") or "")
        size = str(item.get("size") or item.get("ukuran") or "-")
        material = str(item.get("material") or item.get("bahan") or "-")
        qty = str(item.get("quantity") or item.get("jumlah") or "1")
        # Price: price_per_item > harga_satuan > harga (legacy LLM key)
        price = _fmt_number(item.get("price_per_item") or item.get("harga_satuan") or item.get("harga") or 0)
        # Total: total_price > total_harga > total (legacy LLM key)
        total = _fmt_number(item.get("total_price") or item.get("total_harga") or item.get("total") or 0)
        # Notes: use "notes" or "keterangan" (now properly contains finishing/remarks only)
        notes = str(item.get("notes") or item.get("keterangan") or "")

        # Truncate long text to fit
        name = (name[:22] + '..') if len(name) > 22 else name
        notes = (notes[:15] + '..') if len(notes) > 15 else notes

        pdf.cell(COL_NO, ROW_H, str(idx), border=1, fill=fill, align="C")
        pdf.cell(COL_ITEM, ROW_H, name, border=1, fill=fill)
        pdf.cell(COL_SIZE, ROW_H, size, border=1, fill=fill, align="C")
        pdf.cell(COL_MATERIAL, ROW_H, material, border=1, fill=fill, align="C")
        pdf.cell(COL_QTY, ROW_H, qty, border=1, fill=fill, align="C")
        pdf.cell(COL_PRICE, ROW_H, price, border=1, fill=fill, align="R")
        pdf.cell(COL_TOTAL, ROW_H, total, border=1, fill=fill, align="R")
        pdf.cell(COL_NOTES, ROW_H, notes, border=1, fill=fill, align="L", new_x="LMARGIN", new_y="NEXT")

    # ── Grand total row ──────────────────────────────────────────
    grand_total = data.get("total_price") or data.get("total") or "0"
    
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    
    # Align Right for total
    # Calculate offset
    offset = COL_NO + COL_ITEM + COL_SIZE + COL_MATERIAL + COL_QTY + COL_PRICE
    pdf.cell(offset, 10, "GRAND TOTAL", align="R")
    pdf.set_fill_color(200, 200, 200)
    pdf.cell(COL_TOTAL + COL_NOTES, 10, f"Rp {_fmt_number(grand_total)}", border=1, fill=True, align="C", new_x="LMARGIN", new_y="NEXT")

    # ── Output as bytes ──────────────────────────────────────────
    buffer = io.BytesIO()
    pdf.output(buffer)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info("Generated detailed PDF (%d bytes) for %s", len(pdf_bytes), trx_id)
    return pdf_bytes

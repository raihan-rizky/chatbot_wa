"""PDF service — generate digital receipt PDFs from structured data."""

from __future__ import annotations

import io
import logging
from datetime import datetime

from fpdf import FPDF

logger = logging.getLogger(__name__)

# ── Column widths (must add up to 190 = page width - margins) ────
COL_NO = 12
COL_KETERANGAN = 88
COL_JUMLAH = 25
COL_HARGA = 32
COL_TOTAL = 33
ROW_H = 7


class ReceiptPDF(FPDF):
    """Custom PDF layout matching the Media Stationery receipt template."""

    def header(self):
        # Store name
        self.set_font("Helvetica", "B", 18)
        self.cell(0, 10, "MEDIA STATIONERY", align="C", new_x="LMARGIN", new_y="NEXT")

        # Subtitle
        self.set_font("Helvetica", "", 9)
        self.set_text_color(100, 100, 100)
        self.cell(
            0, 5,
            "Atk, Offset, Supplier, Computer, Digital Printing",
            align="C", new_x="LMARGIN", new_y="NEXT",
        )
        self.set_text_color(0, 0, 0)
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


def _fmt_number(value: str) -> str:
    """Format a numeric string with thousand separators, e.g. '150000' -> '150.000'."""
    try:
        num = int(str(value).replace(".", "").replace(",", ""))
        return f"{num:,}".replace(",", ".")
    except (ValueError, TypeError):
        return str(value)


def generate_receipt_pdf(data: dict) -> bytes:
    """Generate a receipt PDF matching the Media Stationery template.

    Args:
        data: Receipt dict with keys: no_nota, items (list of
              {keterangan, jumlah, harga, total}), total.

    Returns:
        Raw PDF bytes ready to upload.
    """
    pdf = ReceiptPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=20)

    # ── Receipt info ─────────────────────────────────────────────
    no_nota = data.get("no_nota", "Tidak Diketahui")
    grand_total = data.get("total", "0")
    timestamp = datetime.now().strftime("%d-%m-%Y")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(30, 6, "No. Nota")
    pdf.cell(5, 6, ":")
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 6, str(no_nota), new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 10)
    pdf.cell(30, 6, "Tanggal")
    pdf.cell(5, 6, ":")
    pdf.cell(0, 6, timestamp, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # ── Table header ─────────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 9)
    pdf.set_fill_color(180, 0, 0)
    pdf.set_text_color(255, 255, 255)
    pdf.cell(COL_NO, ROW_H, "No", border=1, fill=True, align="C")
    pdf.cell(COL_KETERANGAN, ROW_H, "Keterangan", border=1, fill=True, align="C")
    pdf.cell(COL_JUMLAH, ROW_H, "Jumlah", border=1, fill=True, align="C")
    pdf.cell(COL_HARGA, ROW_H, "Harga", border=1, fill=True, align="C")
    pdf.cell(COL_TOTAL, ROW_H, "Total", border=1, fill=True, align="C",
             new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    # ── Table rows ───────────────────────────────────────────────
    items = data.get("items", [])
    pdf.set_font("Helvetica", "", 9)

    for idx, item in enumerate(items, 1):
        # Alternate row colors
        if idx % 2 == 0:
            pdf.set_fill_color(245, 245, 245)
            fill = True
        else:
            fill = False

        keterangan = str(item.get("keterangan", ""))
        jumlah = str(item.get("jumlah", ""))
        harga = _fmt_number(item.get("harga", "0"))
        item_total = _fmt_number(item.get("total", "0"))

        pdf.cell(COL_NO, ROW_H, str(idx), border=1, fill=fill, align="C")
        pdf.cell(COL_KETERANGAN, ROW_H, keterangan, border=1, fill=fill)
        pdf.cell(COL_JUMLAH, ROW_H, jumlah, border=1, fill=fill, align="C")
        pdf.cell(COL_HARGA, ROW_H, harga, border=1, fill=fill, align="R")
        pdf.cell(COL_TOTAL, ROW_H, item_total, border=1, fill=fill, align="R",
                 new_x="LMARGIN", new_y="NEXT")

    # Fill empty rows to make it look like the template (min 10 rows)
    remaining = max(0, 10 - len(items))
    for _ in range(remaining):
        pdf.cell(COL_NO, ROW_H, "", border=1)
        pdf.cell(COL_KETERANGAN, ROW_H, "", border=1)
        pdf.cell(COL_JUMLAH, ROW_H, "", border=1)
        pdf.cell(COL_HARGA, ROW_H, "", border=1)
        pdf.cell(COL_TOTAL, ROW_H, "", border=1, new_x="LMARGIN", new_y="NEXT")

    # ── Grand total row ──────────────────────────────────────────
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(180, 0, 0)
    pdf.set_text_color(255, 255, 255)
    total_label_w = COL_NO + COL_KETERANGAN + COL_JUMLAH + COL_HARGA
    pdf.cell(total_label_w, ROW_H + 1, "TOTAL", border=1, fill=True, align="R")
    pdf.cell(COL_TOTAL, ROW_H + 1, _fmt_number(grand_total), border=1, fill=True,
             align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)

    # ── Output as bytes ──────────────────────────────────────────
    buffer = io.BytesIO()
    pdf.output(buffer)
    pdf_bytes = buffer.getvalue()
    buffer.close()

    logger.info("Generated receipt PDF (%d bytes) for nota %s", len(pdf_bytes), no_nota)
    return pdf_bytes

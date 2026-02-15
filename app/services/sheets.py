"""Google Sheets service — log AI analysis results."""

from __future__ import annotations

import logging
from datetime import datetime

import gspread

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Singleton Client ─────────────────────────────────────────────
_gc: gspread.Client | None = None
_sheet: gspread.Worksheet | None = None


def _get_sheet() -> gspread.Worksheet | None:
    """Initialize Google Sheets client (only once) and return the worksheet."""
    global _gc, _sheet
    
    # Return cached sheet if already initialized
    if _sheet is not None:
        return _sheet

    settings = get_settings()
    
    if not settings.google_sheet_id:
        logger.warning("Google Sheet ID not set. Skipping sheet initialization.")
        return None

    try:
        # Priority: Try loading from JSON content (env var) first
        if settings.google_creds_json:
            import json
            from google.oauth2.service_account import Credentials

            logger.info("🔑 Loading Google options from GOOGLE_CREDS_JSON env var")
            creds_dict = json.loads(settings.google_creds_json)
            creds = Credentials.from_service_account_info(
                creds_dict,
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
            _gc = gspread.authorize(creds)
        
        # Fallback: Load from file
        else:
            logger.info("🔑 Loading Google options from file: %s", settings.google_creds_path)
            _gc = gspread.service_account(filename=settings.google_creds_path)
        
        # Open the specific spreadsheet
        sh = _gc.open_by_key(settings.google_sheet_id)
        
        # Select the first worksheet
        _sheet = sh.sheet1
        
        logger.info("✅ Connected to Google Sheet: %s", sh.title)
        return _sheet
        
    except Exception:
        logger.error("❌ Failed to connect to Google Sheets", exc_info=True)
        return None


def append_log(phone: str, role: str, content: str) -> None:
    """Append a row to the Google Sheet.
    
    Columns: [Timestamp, Phone, Role, Content]
    """
    sheet = _get_sheet()
    if sheet is None:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = [timestamp, phone, role, content]

    try:
        sheet.append_row(row)
        logger.info("📝 Logged to Google Sheet")
    except Exception:
        logger.error("Failed to append to Google Sheet", exc_info=True)


def append_receipt_data(data: dict) -> None:
    """Append structured receipt data to Google Sheet.
    
    Columns:
    - Col A: Timestamp
    - Col B: No Nota
    - Col C: Spanduk Items
    - Col D: Percetakan Items
    - Col E: ATK Items
    - Col F: Total Amount
    """
    sheet = _get_sheet()
    if sheet is None:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Extract fields
    no_nota = data.get("no_nota", "0")
    total = data.get("total", "0")
    
    # Helper to format list
    def format_list(items):
        if not items:
            return "-"
        if isinstance(items, list):
             return "\n".join([f"{i+1}. {item}" for i, item in enumerate(items)])
        return str(items)

    spanduk_str = format_list(data.get("spanduk_items", []))
    percetakan_str = format_list(data.get("percetakan_items", []))
    atk_str = format_list(data.get("atk_items", []))

    # Check if we need to add headers (only if the sheet is likely empty)
    try:
        if not sheet.acell('A1').value:
            headers = ["Timestamp", "No Nota", "Spanduk", "Percetakan", "ATK", "Total"]
            sheet.append_row(headers)
            logger.info("📝 Added headers to Google Sheet")
    except Exception:
        pass

    row = [timestamp, no_nota, spanduk_str, percetakan_str, atk_str, total]

    try:
        sheet.append_row(row)
        logger.info("📝 Logged categorized receipt data to Google Sheet")
    except Exception:
        logger.error("Failed to append receipt data", exc_info=True)


def clear_sheet() -> bool:
    """Delete all data rows from the Google Sheet (keeps header row).

    Returns:
        True if cleared successfully, False otherwise.
    """
    sheet = _get_sheet()
    if sheet is None:
        return False

    try:
        row_count = sheet.row_count
        if row_count <= 1:
            logger.info("Sheet is already empty (only header row)")
            return True

        # Delete all rows except the header (row 1)
        sheet.delete_rows(2, row_count)
        logger.info("🗑️ Cleared all data rows from Google Sheet")
        return True
    except Exception:
        logger.error("Failed to clear Google Sheet", exc_info=True)
        return False



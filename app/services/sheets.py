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
    - Col C: Items (Summary)
    - Col D: Total Amount
    - Col E: Raw JSON
    """
    sheet = _get_sheet()
    if sheet is None:
        return

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Extract fields with defaults
    no_nota = data.get("no_nota", "0")
    total = data.get("total", "0")
    
    # Format items list
    items_list = data.get("items", [])
    if isinstance(items_list, list):
        items_str = ", ".join(items_list)
    else:
        items_str = str(items_list)
    
    # Optional: Price per item & Item Number (if you want to add columns for them)
    # price_per_item = data.get("price_per_item", "0")
    # item_number = data.get("item_number", "0")

    # Convert dict to JSON string for backup
    import json
    #raw_json = json.dumps(data, ensure_ascii=False)

    row = [timestamp, no_nota, items_str,total]

    try:
        sheet.append_row(row)
        logger.info("📝 Logged receipt data to Google Sheet")
    except Exception:
        logger.error("Failed to append receipt data", exc_info=True)


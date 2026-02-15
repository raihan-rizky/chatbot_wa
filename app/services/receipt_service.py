"""Service for managing receipt data in Supabase."""

from __future__ import annotations

import logging
from datetime import datetime

import httpx
from app.config import get_settings

logger = logging.getLogger(__name__)

TABLE = "receipts_teladan"


def _headers() -> dict[str, str]:
    """Build Supabase REST API headers using service_role key."""
    settings = get_settings()
    return {
        "apikey": settings.supabase_service_key,
        "Authorization": f"Bearer {settings.supabase_service_key}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal",
    }


def _base_url() -> str:
    settings = get_settings()
    return f"{settings.supabase_url}/rest/v1/{TABLE}"


async def save_receipt_items(data: dict) -> bool:
    """
    Save receipt items to Supabase `receipts_teladan` table.
    
    Args:
        data: Structured receipt data containing 'items' list and metadata.
              Expected format:
              {
                  "transaction_id": "...",
                  "transaction_date": "...",
                  "customer_name": "...",
                  "payment_method": "...",
                  "items": [
                      {
                          "item_name": "...",
                          "size": "...",
                          "material": "...",
                          "quantity": "...",
                          "price_per_item": 10000,
                          "total_price": 20000,
                          "notes": "..."
                      },
                      ...
                  ]
              }
              
    Returns:
        True if successful, False otherwise.
    """
    if not data.get("items"):
        logger.warning("No items to save for transaction %s", data.get("transaction_id"))
        return True

    rows = []
    
    # Common fields for all items in this transaction
    transaction_id = data.get("transaction_id") or data.get("no_nota") or "UNKNOWN"
    # Use current time if not provided or invalid
    transaction_date = data.get("transaction_date") or datetime.now().isoformat()
    customer_name = data.get("customer_name") or data.get("nama_pelanggan")
    payment_method = data.get("payment_method") or "Cash"

    for item in data["items"]:
        row = {
            "transaction_id": transaction_id,
            "transaction_date": transaction_date,
            "customer_name": customer_name,
            "item_name": item.get("item_name") or item.get("nama_barang") or "Unknown Item",
            "size": item.get("size") or item.get("ukuran"),
            "material": item.get("material") or item.get("bahan"),
            "quantity": str(item.get("quantity") or item.get("jumlah_barang") or "1"),
            "price_per_item": item.get("price_per_item") or item.get("harga_satuan") or 0,
            "total_price": item.get("total_price") or item.get("total_harga") or 0,
            "payment_method": payment_method,
            "notes": item.get("notes") or item.get("keterangan"),
        }
        rows.append(row)

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.post(
                _base_url(),
                headers=_headers(),
                json=rows,
            )
            
            if resp.status_code >= 400:
                logger.error(
                    "Supabase receipt save failed: %s %s", 
                    resp.status_code, 
                    resp.text
                )
                return False
                
            logger.info("Saved %d receipt items to Supabase for Trx ID: %s", len(rows), transaction_id)
            return True
            
        except Exception:
            logger.exception("Exception saving receipt to Supabase")
            return False


async def _get_last_transaction_id() -> str | None:
    """Fetch the most recent transaction_id from Supabase."""
    params = {
        "order": "created_at.desc",
        "limit": 1,
        "select": "transaction_id"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(_base_url(), headers=_headers(), params=params)
            resp.raise_for_status()
            data = resp.json()
            if data and len(data) > 0:
                return data[0].get("transaction_id")
            return None
        except Exception:
            logger.warning("Failed to fetch last transaction ID", exc_info=True)
            return None


async def generate_next_transaction_id() -> str:
    """Generate next ID format 'AB-XXX'. Defaults to 'AB-001' if no previous ID."""
    last_id = await _get_last_transaction_id()
    
    prefix = "AB-"
    next_num = 1
    
    if last_id and last_id.startswith(prefix):
        try:
            # Extract number part: AB-970 -> 970
            num_part = last_id.replace(prefix, "")
            next_num = int(num_part) + 1
        except ValueError:
            pass # Keep default 1 if parsing fails
            
    return f"{prefix}{next_num:03d}" # e.g. AB-001, AB-971


async def get_todays_receipts() -> list[dict]:
    """Fetch all receipt items for the current day (server time)."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    # Filter: created_at >= today's date (00:00:00)
    # Note: Supabase/Postgrest filter syntax
    params = {
        "transaction_date": f"gte.{today_str}T00:00:00",
        "order": "transaction_date.asc,id.asc",
        "select": "*"
    }
    
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(_base_url(), headers=_headers(), params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.exception("Failed to fetch today's receipts from Supabase")
            return []

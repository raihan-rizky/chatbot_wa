"""WhatsApp API — send messages via WAHA."""

from __future__ import annotations

import base64
import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def _get_headers(settings) -> dict[str, str]:
    """Helper to retrieve standard headers including authentication."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if settings.waha_api_key:
        headers["X-Api-Key"] = settings.waha_api_key
    return headers


async def send_message(to: str, body: str) -> None:
    """Send a text message to a WhatsApp user via WAHA.

    Args:
        to: Recipient phone number (e.g. ``"6281234567890"``).
        body: The text content to send.
    """
    settings = get_settings()
    url = f"{settings.waha_base_url}/api/sendText"

    payload = {
        "session": settings.waha_session,
        "chatId": f"{to}@c.us" if not to.endswith("@c.us") else to,
        "text": body,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=_get_headers(settings), json=payload)

        if response.status_code not in (200, 201):
            logger.error(
                "Failed to send WA message to %s — %s %s",
                to,
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        logger.info("Message sent to %s", to)


async def send_document(
    to: str,
    file_bytes: bytes,
    mime_type: str,
    filename: str,
    caption: str = "",
) -> None:
    """Send a document message to a WhatsApp user via WAHA.

    Args:
        to: Recipient phone number.
        file_bytes: Raw file content (PDF bytes).
        mime_type: MIME type of the file.
        filename: Display filename the recipient sees.
        caption: Optional caption text shown with the document.
    """
    settings = get_settings()
    url = f"{settings.waha_base_url}/api/sendFile"

    b64_data = base64.b64encode(file_bytes).decode('utf-8')

    payload = {
        "session": settings.waha_session,
        "chatId": f"{to}@c.us" if not to.endswith("@c.us") else to,
        "file": {
            "mimetype": mime_type,
            "filename": filename,
            "data": b64_data,
        },
    }
    if caption:
        payload["caption"] = caption

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=_get_headers(settings), json=payload)

        if response.status_code not in (200, 201):
            logger.error(
                "Failed to send document to %s — %s %s",
                to,
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        logger.info("Document '%s' sent to %s", filename, to)

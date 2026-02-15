"""WhatsApp Cloud API — send messages."""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

WA_API_BASE = "https://graph.facebook.com/v22.0"


async def send_message(to: str, body: str) -> None:
    """Send a text message to a WhatsApp user.

    Args:
        to: Recipient phone number (e.g. ``"6281234567890"``).
        body: The text content to send.
    """
    settings = get_settings()
    url = f"{WA_API_BASE}/{settings.whatsapp_phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": body},
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)

        if response.status_code != 200:
            logger.error(
                "Failed to send WA message to %s — %s %s",
                to,
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        logger.info("Message sent to %s", to)


async def upload_media(
    file_bytes: bytes,
    mime_type: str,
    filename: str,
) -> str:
    """Upload a file to the WhatsApp Media API.

    Args:
        file_bytes: Raw file content.
        mime_type: MIME type (e.g. ``"application/pdf"``).
        filename: Display filename (e.g. ``"receipt.pdf"``).

    Returns:
        The WhatsApp media ID for the uploaded file.
    """
    settings = get_settings()
    url = f"{WA_API_BASE}/{settings.whatsapp_phone_number_id}/media"

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
    }

    # WhatsApp Media API expects multipart/form-data
    files = {
        "file": (filename, file_bytes, mime_type),
    }
    data = {
        "messaging_product": "whatsapp",
        "type": mime_type,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(url, headers=headers, files=files, data=data)

        if response.status_code != 200:
            logger.error(
                "Failed to upload media — %s %s",
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        media_id = response.json()["id"]
        logger.info("Uploaded media: %s (%s, %d bytes)", media_id, filename, len(file_bytes))
        return media_id


async def send_document(
    to: str,
    media_id: str,
    filename: str,
    caption: str = "",
) -> None:
    """Send a document message to a WhatsApp user.

    Args:
        to: Recipient phone number.
        media_id: WhatsApp media ID from ``upload_media()``.
        filename: Display filename the recipient sees.
        caption: Optional caption text shown with the document.
    """
    settings = get_settings()
    url = f"{WA_API_BASE}/{settings.whatsapp_phone_number_id}/messages"

    headers = {
        "Authorization": f"Bearer {settings.whatsapp_access_token}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "document",
        "document": {
            "id": media_id,
            "filename": filename,
        },
    }
    if caption:
        payload["document"]["caption"] = caption

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, headers=headers, json=payload)

        if response.status_code != 200:
            logger.error(
                "Failed to send document to %s — %s %s",
                to,
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        logger.info("Document '%s' sent to %s", filename, to)


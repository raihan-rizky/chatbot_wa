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

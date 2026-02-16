"""Text service — parse free-form text into structured receipt JSON."""

from __future__ import annotations

import json
import logging
import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_nebius import ChatNebius

from app.config import get_settings
from app.services.prompts import RECEIPT_SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# ── Lazy-initialised fast LLM (for text only) ───────────────────
_fast_llm: ChatNebius | None = None


def _get_fast_llm() -> ChatNebius:
    """Return (and cache) the fast/lightweight ChatNebius instance."""
    global _fast_llm
    if _fast_llm is None:
        settings = get_settings()
        _fast_llm = ChatNebius(
            api_key=settings.nebius_api_key,
            model=settings.nebius_fast_model,
            temperature=0.1,
            max_tokens=2048,
        )
    return _fast_llm


def _parse_json_response(content: str) -> dict | str:
    """Attempt to clean and parse JSON from LLM output."""
    try:
        # Remove markdown code blocks if present
        clean_content = content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_content)
    except json.JSONDecodeError:
        logger.warning("Failed to parse JSON from Text model: %s", content[:100])
        # Try to use regex to find JSON block
        match = re.search(r'\{.*\}', content, re.DOTALL)
        if match:
             try:
                return json.loads(match.group(0))
             except:
                pass
        
        # Return as raw text if parsing fails
        return content


async def parse_text_to_receipt(text: str) -> dict | str:
    """Parse free-form text into structured receipt JSON using the Fast LLM.

    Args:
        text: User-typed receipt description, e.g.
              "Nota 123 spanduk 2x3 50rb, pulpen 10rb total 60000"

    Returns:
        Parsed receipt dict, or an error string if parsing fails.
    """
    llm = _get_fast_llm()

    messages = [
        SystemMessage(content=RECEIPT_SYSTEM_PROMPT),
        HumanMessage(content=(
            "Extract receipt data from this text description into JSON:\n\n"
            f"{text}"
        )),
    ]

    try:
        response = await llm.ainvoke(messages)
        content = str(response.content)
        logger.info("Text-to-receipt LLM output: %s", content[:200])
        return _parse_json_response(content)
    except Exception:
        logger.exception("Text-to-receipt LLM call failed")
        return "Maaf, gagal memproses teks struk. Coba lagi. 🙏"

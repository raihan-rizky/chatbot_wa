"""LLM service — LangChain + Nebius AI Studio."""

from __future__ import annotations

import logging
from collections import defaultdict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_nebius import ChatNebius

from app.config import get_settings

logger = logging.getLogger(__name__)

# ── Per-user conversation history (in-memory) ────────────────────
_history: dict[str, list[HumanMessage | AIMessage]] = defaultdict(list)

# ── System prompt ────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "You are a helpful, friendly AI assistant communicating through WhatsApp. "
    "Keep your answers concise and conversational — WhatsApp messages should "
    "be easy to read on a phone screen. Use emoji when appropriate. "
    "If you don't know something, say so honestly."
)

# ── Lazy-initialised LLM instance ───────────────────────────────
_llm: ChatNebius | None = None


def _get_llm() -> ChatNebius:
    """Return (and cache) the ChatNebius instance."""
    global _llm
    if _llm is None:
        settings = get_settings()
        _llm = ChatNebius(
            api_key=settings.nebius_api_key,
            model=settings.nebius_model,
            temperature=0.7,
            top_p=0.95,
        )
    return _llm


def _trim_history(phone: str) -> None:
    """Keep only the latest N message pairs for a user."""
    max_len = get_settings().max_history_length
    if len(_history[phone]) > max_len:
        _history[phone] = _history[phone][-max_len:]


async def get_ai_response(phone: str, user_message: str) -> str:
    """Generate an AI response for *user_message* within the user's conversation.

    Args:
        phone: The sender's phone number (used as conversation key).
        user_message: The text the user sent.

    Returns:
        The AI-generated reply as a plain string.
    """
    llm = _get_llm()

    # Append the new user message to history
    _history[phone].append(HumanMessage(content=user_message))
    _trim_history(phone)

    # Build the full message list: system + history
    messages = [SystemMessage(content=SYSTEM_PROMPT), *_history[phone]]

    try:
        response = await llm.ainvoke(messages)
        reply = response.content

        # Store the assistant reply in history
        _history[phone].append(AIMessage(content=reply))
        _trim_history(phone)

        return reply  # type: ignore[return-value]
    except Exception:
        logger.exception("Nebius LLM call failed for phone=%s", phone)
        # Remove the user message we just added so history stays clean
        _history[phone].pop()
        return "Sorry, I'm having trouble thinking right now. Please try again in a moment. 🙏"


def clear_history(phone: str) -> None:
    """Reset conversation history for a user."""
    _history.pop(phone, None)

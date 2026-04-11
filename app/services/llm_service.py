"""LLM service — LangChain + Nebius AI Studio with Supabase chat history."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_nebius import ChatNebius

from app.config import get_settings
from app.services.chat_history import save_message, get_history

logger = logging.getLogger(__name__)

# ── System prompt ────────────────────────────────────────────────
SYSTEM_PROMPT = (
    "Kamu adalah asisten AI untuk 'Toko Teladan Percetakan dan ATK'. "
    "Tugasmu adalah membantu mencatat pemasukan dan pengeluaran toko, serta menjawab pertanyaan pelanggan. "
    "Alamat toko: Jl. Temu Putih No.30, Jombang Wetan, Kec. Jombang, Kota Cilegon, Banten, 42411. "
    "Jam buka: 08:00 - 17:00. Nomor HP: 085959929700 (untuk pembelian). "
    "Jawablah dengan sopan, singkat, dan jelas layaknya chat WhatsApp. Gunakan emoji jika perlu. "
    "Jika kamu tidak tahu jawabannya, katakan sejujurnya."
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


async def get_ai_response(phone: str, user_message: str) -> str:
    """Generate an AI response using persistent Supabase history.

    Args:
        phone: The sender's phone number (conversation key).
        user_message: The text the user sent.

    Returns:
        The AI-generated reply as a plain string.
    """
    llm = _get_llm()
    settings = get_settings()

    # Save user message to Supabase
    await save_message(phone, "user", user_message)

    # Load recent history from Supabase
    history_rows = await get_history(phone, limit=settings.max_history_length)

    # Convert DB rows to LangChain messages
    messages = [SystemMessage(content=SYSTEM_PROMPT)]
    for row in history_rows:
        if row["role"] == "user":
            messages.append(HumanMessage(content=row["content"]))
        elif row["role"] == "assistant":
            messages.append(AIMessage(content=row["content"]))

    try:
        response = await llm.ainvoke(messages)
        reply = response.content

        # Save AI reply to Supabase
        await save_message(phone, "assistant", reply)

        return reply  # type: ignore[return-value]
    except Exception:
        logger.exception("Nebius LLM call failed for phone=%s", phone)
        return "Sorry, I'm having trouble thinking right now. Please try again in a moment. 🙏"


async def generate_daily_report(receipts: list[dict]) -> str:
    """Generate a daily sales report using the LLM.

    Args:
        receipts: List of receipt dictionaries.

    Returns:
        A summarized report string.
    """
    if not receipts:
        return "Belum ada transaksi hari ini."

    llm = _get_llm()

    # Convert receipts to a simplified string format for the LLM
    receipts_text = ""
    for r in receipts:
        receipts_text += (
            f"- ID: {r.get('transaction_id')}, "
            f"Item: {r.get('item_name')} ({r.get('quantity')}), "
            f"Total: {r.get('total_price')}, "
            f"Payment: {r.get('payment_method')}\n"
        )

    prompt = (
        "Analisis data penjualan hari ini dan buat laporan singkat untuk pemilik toko.\n\n"
        "Data Transaksi:\n"
        f"{receipts_text}\n\n"
        "Tolong buatkan rangkuman dengan poin-poin berikut:\n"
        "1. *Total Omzet Hari Ini* (Hitung total semua transaksi)\n"
        "2. *Rincian Pembayaran* (Total Cash vs Transfer vs QRIS)\n"
        "3. *Item Terlaris* (Barang apa yang paling banyak laku)\n"
        "4. *Insight/Saran Singkat* (Misal: 'Penjualan banner sedang ramai', atau 'Stok pulpen perlu dicek')\n\n"
        "Gunakan bahasa Indonesia yang profesional namun santai. Format output harus rapi dan enak dibaca di WhatsApp."
    )

    messages = [
        SystemMessage(content="Kamu adalah asisten manajer toko yang pintar menganalisis data penjualan."),
        HumanMessage(content=prompt),
    ]

    try:
        response = await llm.ainvoke(messages)
        return str(response.content)
    except Exception:
        logger.exception("Failed to generate daily report")
        return "Maaf, gagal membuat laporan harian. Coba lagi nanti. 🙏"

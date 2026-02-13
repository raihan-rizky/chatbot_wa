"""FastAPI application entry-point for the WhatsApp AI chatbot."""

from __future__ import annotations

import logging

from fastapi import FastAPI

from app.routes.webhook import router as webhook_router

# ── Logging ──────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-7s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)

# ── FastAPI app ──────────────────────────────────────────────────
app = FastAPI(
    title="WhatsApp AI Chatbot",
    description="AI-powered WhatsApp chatbot using LangChain & Nebius",
    version="1.0.0",
)

app.include_router(webhook_router)


@app.get("/")
async def health_check():
    """Simple health-check endpoint."""
    return {"status": "ok", "service": "whatsapp-ai-chatbot"}


# ── Run directly with `python -m app.main` ──────────────────────
if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)

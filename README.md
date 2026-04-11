# WhatsApp AI Chatbot

AI-powered WhatsApp chatbot built with **FastAPI**, **LangChain**, and **Nebius AI Studio**.

## Prerequisites

- Python 3.11+
- [Nebius AI Studio](https://studio.nebius.com/) API key
- [Meta Developer](https://developers.facebook.com/) account with a WhatsApp Business App
- [ngrok](https://ngrok.com/) (for local development)

## Quick Start

### 1. Clone & install

```bash
cd chatbot_wa
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux
pip install -r requirements.txt
```

### 2. Configure environment

```bash
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux
```

Edit `.env` and fill in your credentials:

| Variable                   | Where to get it                                              |
| -------------------------- | ------------------------------------------------------------ |
| `NEBIUS_API_KEY`           | [Nebius AI Studio](https://studio.nebius.com/) → API Keys    |
| `NEBIUS_MODEL`             | Default: `Qwen/Qwen3-14B` — see Nebius docs for alternatives |
| `WHATSAPP_ACCESS_TOKEN`    | Meta Developer Console → WhatsApp → API Setup                |
| `WHATSAPP_PHONE_NUMBER_ID` | Meta Developer Console → WhatsApp → API Setup                |
| `WHATSAPP_VERIFY_TOKEN`    | Any secret string you choose                                 |

### 3. Start the server

```bash
python -m app.main
```

The server runs at `http://localhost:8000`.

### 4. Expose with ngrok

```bash
ngrok http 8000
```

Copy the **HTTPS** forwarding URL (e.g. `https://xxxx.ngrok-free.app`).

### 5. Configure Meta webhook

1. Go to **Meta Developer Console** → your app → **WhatsApp** → **Configuration**
2. Set **Callback URL** to `https://xxxx.ngrok-free.app/webhook`
3. Set **Verify token** to the same value as `WHATSAPP_VERIFY_TOKEN` in your `.env`
4. Subscribe to the **messages** field
5. Send a message from WhatsApp to your sandbox number — the bot will reply! 🎉

## Chat Commands

| Command            | Description                                              |
| ------------------ | -------------------------------------------------------- |
| `/struk [details]` | Create a digital receipt (e.g. `/struk Spanduk 2x1 50k`) |
| `/dailyreport`     | Generate a daily sales report with AI analysis           |
| `/spreadsheet`     | Sync today's data to Google Sheets                       |
| `/hapus`           | Clear chat history & reset data                          |
| `/help`            | Show the welcome guide                                   |

## API Endpoints

| Method | Path       | Description                  |
| ------ | ---------- | ---------------------------- |
| `GET`  | `/`        | Health check                 |
| `GET`  | `/webhook` | Meta webhook verification    |
| `POST` | `/webhook` | Receive incoming WA messages |

## Project Structure

```
app/
├── main.py              # FastAPI entry point
├── config.py            # Settings from .env
├── services/
│   ├── llm_service.py   # LangChain + Nebius AI
│   └── whatsapp.py      # Send WA messages
└── routes/
    └── webhook.py       # Webhook endpoints
```

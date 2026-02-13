-- ============================================
-- Chat History Table — Teladan AI WhatsApp Bot
-- ============================================

CREATE TABLE IF NOT EXISTS chat_history (
    id            BIGSERIAL PRIMARY KEY,
    phone_number  VARCHAR(20) NOT NULL,          -- nomor WA user (e.g. 6281991029210)
    role          VARCHAR(10) NOT NULL            -- 'user' atau 'assistant'
                  CHECK (role IN ('user', 'assistant')),
    content       TEXT NOT NULL,                  -- isi pesan
    message_type  VARCHAR(10) DEFAULT 'text'      -- 'text' atau 'image'
                  CHECK (message_type IN ('text', 'image')),
    created_at    TIMESTAMPTZ DEFAULT NOW(),

    -- Index untuk query riwayat per user (cepat)
    CONSTRAINT idx_phone_created UNIQUE (phone_number, created_at, id)
);

-- Index untuk pencarian riwayat chat per nomor
CREATE INDEX IF NOT EXISTS idx_chat_history_phone 
    ON chat_history (phone_number, created_at DESC);

-- Opsional: RLS (Row Level Security) untuk Supabase
-- ALTER TABLE chat_history ENABLE ROW LEVEL SECURITY;

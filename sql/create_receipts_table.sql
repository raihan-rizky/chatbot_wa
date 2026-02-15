-- ============================================
-- Receipts Table — Teladan AI WhatsApp Bot
-- ============================================

CREATE TABLE IF NOT EXISTS receipts_teladan (
    id BIGSERIAL PRIMARY KEY,
    transaction_id VARCHAR(50) NOT NULL,    -- ID Transaksi (e.g. No Nota) - Unik per transaksi, tapi 1 transaksi bisa banyak item
    transaction_date TIMESTAMPTZ DEFAULT NOW(),
    customer_name VARCHAR(100),
    item_name VARCHAR(255) NOT NULL,
    size VARCHAR(50),
    material VARCHAR(50),
    quantity VARCHAR(50),                   -- Disimpan sebagai Text karena bisa "2 pcs", "3 rim", dll
    price_per_item NUMERIC(15, 2),          -- Harga per barang
    total_price NUMERIC(15, 2),             -- Total harga per item (qty * price)
    payment_method VARCHAR(50),
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Index untuk pencarian cepat berdasarkan ID Transaksi
CREATE INDEX IF NOT EXISTS idx_receipts_transaction_id 
    ON receipts_teladan (transaction_id);

-- Index untuk filter berdasarkan tanggal (untuk laporan)
CREATE INDEX IF NOT EXISTS idx_receipts_date 
    ON receipts_teladan (transaction_date DESC);

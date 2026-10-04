-- Automatic in-chat OxaPay USDT deposits
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS oxapay_track_id VARCHAR(120);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS oxapay_order_id VARCHAR(160);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS payment_network VARCHAR(80);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS payment_address TEXT;
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS expected_crypto_amount NUMERIC(30, 12);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS payment_tx_hash VARCHAR(255);
ALTER TABLE transactions ADD COLUMN IF NOT EXISTS payment_callback JSONB;

CREATE INDEX IF NOT EXISTS idx_transactions_oxapay_track_id ON transactions(oxapay_track_id);
CREATE INDEX IF NOT EXISTS idx_transactions_oxapay_order_id ON transactions(oxapay_order_id);

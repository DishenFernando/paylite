-- PayLite PostgreSQL schema. Applied automatically only to a fresh Docker volume.
-- Money is stored as integer cents to avoid floating-point errors.
CREATE TABLE users (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 name VARCHAR(80) NOT NULL,
 mobile VARCHAR(16) NOT NULL UNIQUE,
 password_hash TEXT NOT NULL,
 pin_hash TEXT NOT NULL,
 role VARCHAR(10) NOT NULL DEFAULT 'CUSTOMER' CHECK (role IN ('CUSTOMER','ADMIN')),
 login_failures INT NOT NULL DEFAULT 0,
 login_locked_until TIMESTAMPTZ,
 pin_failures INT NOT NULL DEFAULT 0,
 pin_locked_until TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE wallets (
 user_id BIGINT PRIMARY KEY REFERENCES users(id),
 balance_cents BIGINT NOT NULL DEFAULT 0 CHECK (balance_cents >= 0)
);
CREATE TABLE transfers (
 id VARCHAR(32) PRIMARY KEY,
 sender_id BIGINT NOT NULL REFERENCES users(id),
 recipient_id BIGINT NOT NULL REFERENCES users(id),
 amount_cents BIGINT NOT NULL CHECK (amount_cents > 0),
 idem_key VARCHAR(128) NOT NULL,
 status VARCHAR(12) NOT NULL DEFAULT 'COMPLETED' CHECK (status IN ('COMPLETED','REVERSED')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 reversed_at TIMESTAMPTZ,
 CONSTRAINT uq_sender_idem UNIQUE (sender_id, idem_key),
 CONSTRAINT distinct_transfer_parties CHECK (sender_id <> recipient_id)
);
CREATE TABLE ledger_entries (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 wallet_id BIGINT NOT NULL REFERENCES wallets(user_id),
 entry_type VARCHAR(12) NOT NULL CHECK (entry_type IN ('TOPUP','SENT','RECEIVED','REVERSAL')),
 delta_cents BIGINT NOT NULL CHECK (delta_cents <> 0),
 reference VARCHAR(80) NOT NULL,
 counterparty_id BIGINT REFERENCES users(id),
 balance_after_cents BIGINT NOT NULL CHECK (balance_after_cents >= 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_ledger_wallet_date ON ledger_entries(wallet_id, created_at DESC);
CREATE INDEX idx_transfers_sender_date ON transfers(sender_id, created_at DESC);
CREATE TABLE sessions (
 token_hash CHAR(64) PRIMARY KEY,
 user_id BIGINT NOT NULL REFERENCES users(id),
 expires_at TIMESTAMPTZ NOT NULL
);
-- Immutability enforced in the database, not just in the Python API.
CREATE FUNCTION refuse_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Ledger is append-only; UPDATE and DELETE are prohibited';
END;
$$;
CREATE TRIGGER ledger_no_update_delete BEFORE UPDATE OR DELETE ON ledger_entries
FOR EACH ROW EXECUTE FUNCTION refuse_ledger_mutation();

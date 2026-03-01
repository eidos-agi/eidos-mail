-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Emails table
CREATE TABLE IF NOT EXISTS emails (
    id SERIAL PRIMARY KEY,
    uid INTEGER UNIQUE NOT NULL,
    message_id TEXT,
    from_addr TEXT,
    to_addrs TEXT,
    cc_addrs TEXT,
    subject TEXT,
    date_sent TIMESTAMPTZ,
    body_text TEXT,
    folder TEXT DEFAULT 'INBOX',
    synced_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_emails_uid ON emails(uid);
CREATE INDEX IF NOT EXISTS idx_emails_date ON emails(date_sent DESC);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails(from_addr);
CREATE INDEX IF NOT EXISTS idx_emails_deleted ON emails(deleted_at) WHERE deleted_at IS NULL;

-- Email vectors table (384-dim for all-MiniLM-L6-v2)
CREATE TABLE IF NOT EXISTS email_vectors (
    email_id INTEGER PRIMARY KEY REFERENCES emails(id),
    embedding vector(384) NOT NULL
);

-- IVFFlat index for fast similarity search
-- Note: requires at least ~100 rows before creating; will be created after first sync

-- Multi-user email isolation: owner_email column + mail_accounts table

-- 1. Add owner column, backfill daniel's data
ALTER TABLE emails ADD COLUMN IF NOT EXISTS owner_email TEXT;
UPDATE emails SET owner_email = 'daniel@eidosagi.com' WHERE owner_email IS NULL;
ALTER TABLE emails ALTER COLUMN owner_email SET NOT NULL;

-- 2. Fix uid uniqueness: was global, now per-user+folder
ALTER TABLE emails DROP CONSTRAINT IF EXISTS emails_uid_key;
DROP INDEX IF EXISTS idx_emails_uid;
CREATE UNIQUE INDEX IF NOT EXISTS idx_emails_owner_uid_folder ON emails(owner_email, uid, folder);
CREATE INDEX IF NOT EXISTS idx_emails_owner ON emails(owner_email);

-- 3. Mail accounts table (metadata — passwords stay in vault)
CREATE TABLE IF NOT EXISTS mail_accounts (
    id SERIAL PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    display_name TEXT,
    imap_host TEXT DEFAULT 'imap.migadu.com',
    imap_port INTEGER DEFAULT 993,
    smtp_host TEXT DEFAULT 'smtp.migadu.com',
    smtp_port INTEGER DEFAULT 465,
    vault_secret_path TEXT,
    last_sync_at TIMESTAMPTZ,
    sync_status TEXT DEFAULT 'idle',
    sync_error TEXT,
    enabled BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    deleted_at TIMESTAMPTZ
);

-- 4. Seed daniel's account
INSERT INTO mail_accounts (email, display_name, vault_secret_path)
VALUES ('daniel@eidosagi.com', 'Daniel Shanklin', 'mail/daniel@eidosagi.com/password')
ON CONFLICT (email) DO NOTHING;

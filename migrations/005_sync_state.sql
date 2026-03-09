-- Per-folder sync state for smart incremental sync

CREATE TABLE IF NOT EXISTS sync_state (
    id SERIAL PRIMARY KEY,
    owner_email TEXT NOT NULL,
    folder TEXT NOT NULL,
    uidvalidity INTEGER,
    highest_uid INTEGER DEFAULT 0,
    last_full_sync TIMESTAMPTZ,
    last_incremental_sync TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(owner_email, folder)
);

-- Read tracking + Eisenhower Matrix scoring
ALTER TABLE emails ADD COLUMN IF NOT EXISTS is_read BOOLEAN DEFAULT FALSE;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS urgency REAL DEFAULT 0.5;
ALTER TABLE emails ADD COLUMN IF NOT EXISTS priority REAL DEFAULT 0.5;

CREATE INDEX IF NOT EXISTS idx_emails_is_read ON emails(is_read) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_emails_urgency_priority ON emails(urgency, priority) WHERE deleted_at IS NULL;

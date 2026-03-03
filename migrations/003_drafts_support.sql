-- Allow nullable uid for local drafts (not synced from IMAP)
ALTER TABLE emails ALTER COLUMN uid DROP NOT NULL;

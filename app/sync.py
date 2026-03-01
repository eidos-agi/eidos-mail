"""IMAP sync: fetch emails from Migadu, store in Postgres, generate embeddings."""

import imaplib
import email
import email.policy
from email.utils import parsedate_to_datetime
from datetime import datetime

from app.config import IMAP_HOST, IMAP_PORT, EMAIL_ADDRESS, EMAIL_PASSWORD
from app.database import get_pool
from app.embeddings import encode


def extract_body(msg) -> str:
    """Extract plain text body from email message."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                payload = part.get_payload(decode=True)
                if payload:
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        return payload.decode(charset, errors="replace")
                    except (LookupError, UnicodeDecodeError):
                        return payload.decode("utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            try:
                return payload.decode(charset, errors="replace")
            except (LookupError, UnicodeDecodeError):
                return payload.decode("utf-8", errors="replace")
    return ""


def parse_date(msg) -> datetime | None:
    """Parse date from email, return datetime."""
    date_str = msg.get("Date", "")
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str)
    except Exception:
        return None


def fetch_emails_imap(folder: str = "INBOX") -> list[tuple[int, bytes]]:
    """Connect to IMAP, fetch all emails, return list of (uid, raw_bytes)."""
    imap = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    imap.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
    imap.select(folder, readonly=True)

    _, data = imap.uid("search", None, "ALL")
    uids = data[0].split()

    results = []
    for uid_bytes in uids:
        uid = int(uid_bytes)
        _, msg_data = imap.uid("fetch", uid_bytes, "(RFC822)")
        if msg_data[0] is None:
            continue
        raw = msg_data[0][1]
        results.append((uid, raw))

    imap.logout()
    return results


async def sync_emails(folders: list[str] | None = None) -> dict:
    """Full sync: IMAP fetch -> Postgres insert -> vector embeddings."""
    if folders is None:
        folders = ["INBOX", "Sent"]

    pool = await get_pool()
    stats = {"folders": {}, "total_new": 0}

    # Get already-synced UIDs
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT uid FROM emails WHERE deleted_at IS NULL"
        )
        synced_uids = {r["uid"] for r in rows}

    new_email_ids = []

    for folder in folders:
        try:
            emails = fetch_emails_imap(folder)
        except Exception as e:
            stats["folders"][folder] = {"error": str(e)}
            continue

        new_count = 0
        for uid, raw in emails:
            if uid in synced_uids:
                continue

            msg = email.message_from_bytes(raw, policy=email.policy.default)
            body = extract_body(msg)
            date_sent = parse_date(msg)

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO emails
                        (uid, message_id, from_addr, to_addrs, cc_addrs,
                         subject, date_sent, body_text, folder)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (uid) DO NOTHING
                    RETURNING id""",
                    uid,
                    msg.get("Message-ID", ""),
                    msg.get("From", ""),
                    msg.get("To", ""),
                    msg.get("Cc", ""),
                    msg.get("Subject", ""),
                    date_sent,
                    body,
                    folder,
                )
                if row:
                    new_email_ids.append(row["id"])
                    new_count += 1
                    synced_uids.add(uid)

        stats["folders"][folder] = {"fetched": len(emails), "new": new_count}
        stats["total_new"] += new_count

    # Generate embeddings for new emails
    if new_email_ids:
        await embed_emails(new_email_ids)
        stats["embedded"] = len(new_email_ids)

    return stats


async def embed_emails(email_ids: list[int]):
    """Generate and store embeddings for given email IDs."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, subject, body_text FROM emails
            WHERE id = ANY($1)""",
            email_ids,
        )

    texts = []
    ids = []
    for row in rows:
        text = f"{row['subject'] or ''} {(row['body_text'] or '')[:500]}"
        texts.append(text)
        ids.append(row["id"])

    if not texts:
        return

    embeddings = encode(texts)

    async with pool.acquire() as conn:
        for email_id, emb in zip(ids, embeddings):
            vec_str = "[" + ",".join(str(x) for x in emb) + "]"
            await conn.execute(
                """INSERT INTO email_vectors (email_id, embedding)
                VALUES ($1, $2)
                ON CONFLICT (email_id) DO UPDATE SET embedding = $2""",
                email_id,
                vec_str,
            )

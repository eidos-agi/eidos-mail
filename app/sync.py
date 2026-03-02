"""IMAP sync: fetch emails per-user, store in Postgres, generate embeddings."""

import imaplib
import email
import email.policy
from email.utils import parsedate_to_datetime
from datetime import datetime

from app.database import get_pool
from app.embeddings import encode
from app.vault_client import get_mail_password, get_mail_account


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


def fetch_emails_imap(
    imap_host: str, imap_port: int, email_addr: str, password: str,
    folder: str = "INBOX",
) -> list[tuple[int, bytes]]:
    """Connect to IMAP, fetch all emails, return list of (uid, raw_bytes)."""
    imap = imaplib.IMAP4_SSL(imap_host, imap_port)
    imap.login(email_addr, password)
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


async def sync_emails_for_user(
    user_email: str, folders: list[str] | None = None,
) -> dict:
    """Per-user sync: look up credentials, IMAP fetch, insert with owner_email."""
    if folders is None:
        folders = ["INBOX", "Sent"]

    # Look up account config and password
    account = await get_mail_account(user_email)
    if not account:
        return {"error": f"No mail account configured for {user_email}"}

    password = await get_mail_password(user_email)
    if not password:
        return {"error": f"Could not fetch password from vault for {user_email}"}

    pool = await get_pool()

    # Mark sync in progress
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE mail_accounts SET sync_status = 'syncing' "
            "WHERE email = $1 AND deleted_at IS NULL",
            user_email,
        )

    stats = {"folders": {}, "total_new": 0}

    try:
        # Get already-synced UIDs for this user
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT uid, folder FROM emails "
                "WHERE owner_email = $1 AND deleted_at IS NULL",
                user_email,
            )
            synced = {(r["uid"], r["folder"]) for r in rows}

        new_email_ids = []

        for folder in folders:
            try:
                emails_raw = fetch_emails_imap(
                    account["imap_host"], account["imap_port"],
                    user_email, password, folder,
                )
            except Exception as e:
                stats["folders"][folder] = {"error": str(e)}
                continue

            new_count = 0
            for uid, raw in emails_raw:
                if (uid, folder) in synced:
                    continue

                msg = email.message_from_bytes(raw, policy=email.policy.default)
                body = extract_body(msg)
                date_sent = parse_date(msg)

                async with pool.acquire() as conn:
                    row = await conn.fetchrow(
                        """INSERT INTO emails
                            (uid, message_id, from_addr, to_addrs, cc_addrs,
                             subject, date_sent, body_text, folder, owner_email)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        ON CONFLICT (owner_email, uid, folder) DO NOTHING
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
                        user_email,
                    )
                    if row:
                        new_email_ids.append(row["id"])
                        new_count += 1
                        synced.add((uid, folder))

            stats["folders"][folder] = {"fetched": len(emails_raw), "new": new_count}
            stats["total_new"] += new_count

        # Generate embeddings for new emails
        if new_email_ids:
            await embed_emails(new_email_ids)
            stats["embedded"] = len(new_email_ids)

        # Mark sync complete
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE mail_accounts SET sync_status = 'idle', "
                "last_sync_at = NOW(), sync_error = NULL "
                "WHERE email = $1 AND deleted_at IS NULL",
                user_email,
            )

    except Exception as e:
        # Mark sync error
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE mail_accounts SET sync_status = 'error', "
                "sync_error = $2 "
                "WHERE email = $1 AND deleted_at IS NULL",
                user_email, str(e),
            )
        raise

    return stats


async def embed_emails(email_ids: list[int]):
    """Generate and store embeddings for given email IDs."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, subject, body_text FROM emails
            WHERE id = ANY($1) AND deleted_at IS NULL""",
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

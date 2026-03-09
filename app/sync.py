"""Smart IMAP sync: incremental by default, full when needed.

Strategy:
1. Track UIDVALIDITY per folder — if it changes, server rebuilt UIDs, full resync required
2. Track highest_uid per folder — only fetch UIDs > highest_uid for incremental
3. Sync flags (read/unread) for recent emails without re-downloading bodies
4. Detect server-side deletions — soft-delete emails removed from server
5. Full sync runs on first sync or when UIDVALIDITY changes
6. Single IMAP connection per sync (not per-folder)
"""

import imaplib
import email
import email.policy
from email.utils import parsedate_to_datetime
from datetime import datetime

from app.database import get_pool
from app.embeddings import encode
from app.vault_client import get_mail_password, get_mail_account
from app.scoring import score_email

SYNC_FOLDERS = ["INBOX", "Sent"]


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


# ---------------------------------------------------------------------------
# IMAP helpers
# ---------------------------------------------------------------------------

def _imap_connect(host: str, port: int, email_addr: str, password: str) -> imaplib.IMAP4_SSL:
    """Open and authenticate a single IMAP connection."""
    imap = imaplib.IMAP4_SSL(host, port)
    imap.login(email_addr, password)
    return imap


def _select_folder(imap: imaplib.IMAP4_SSL, folder: str) -> tuple[int, int]:
    """Select folder, return (message_count, uidvalidity)."""
    _, data = imap.select(folder, readonly=True)
    msg_count = int(data[0]) if data[0] else 0
    # Get UIDVALIDITY
    _, resp = imap.response("UIDVALIDITY")
    uidvalidity = int(resp[0]) if resp and resp[0] else 0
    return msg_count, uidvalidity


def _fetch_uids(imap: imaplib.IMAP4_SSL, search_criteria: str = "ALL") -> list[int]:
    """Search and return list of UIDs."""
    _, data = imap.uid("search", None, search_criteria)
    if not data[0]:
        return []
    return [int(u) for u in data[0].split()]


def _fetch_flags(imap: imaplib.IMAP4_SSL, uids: list[int]) -> dict[int, set]:
    """Fetch flags for given UIDs. Returns {uid: {flag_set}}."""
    if not uids:
        return {}
    uid_str = ",".join(str(u) for u in uids)
    _, data = imap.uid("fetch", uid_str, "(FLAGS)")
    flags = {}
    for item in data:
        if isinstance(item, tuple):
            item = item[0]
        if not isinstance(item, bytes):
            continue
        text = item.decode(errors="replace")
        # Parse "N (UID X FLAGS (\Seen \Flagged))"
        import re
        uid_match = re.search(r"UID (\d+)", text)
        flags_match = re.search(r"FLAGS \(([^)]*)\)", text)
        if uid_match:
            uid = int(uid_match.group(1))
            flag_set = set()
            if flags_match:
                flag_set = set(flags_match.group(1).split())
            flags[uid] = flag_set
    return flags


def _fetch_messages(imap: imaplib.IMAP4_SSL, uids: list[int]) -> list[tuple[int, bytes]]:
    """Fetch full RFC822 messages for given UIDs."""
    results = []
    for uid in uids:
        _, msg_data = imap.uid("fetch", str(uid).encode(), "(RFC822)")
        if msg_data[0] is None:
            continue
        raw = msg_data[0][1]
        results.append((uid, raw))
    return results


# ---------------------------------------------------------------------------
# Smart sync
# ---------------------------------------------------------------------------

async def sync_emails_for_user(
    user_email: str, folders: list[str] | None = None,
) -> dict:
    """Smart per-user sync with incremental fetching.

    Flow per folder:
    1. Check UIDVALIDITY — if changed, nuke sync state & full resync
    2. If we have highest_uid, only fetch UIDs > highest_uid (incremental)
    3. If no sync state, fetch all (first sync)
    4. Sync flags for recent emails (last 200) to catch read/unread changes
    5. Detect server-side deletions for this folder
    """
    if folders is None:
        folders = list(SYNC_FOLDERS)

    account = await get_mail_account(user_email)
    if not account:
        return {"error": f"No mail account configured for {user_email}"}

    password = await get_mail_password(user_email)
    if not password:
        return {"error": f"Could not fetch password from vault for {user_email}"}

    pool = await get_pool()

    # Mark syncing
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE mail_accounts SET sync_status = 'syncing' "
            "WHERE email = $1 AND deleted_at IS NULL",
            user_email,
        )

    stats = {"folders": {}, "total_new": 0, "total_flag_updates": 0, "total_deletions": 0}

    try:
        # Single IMAP connection for all folders
        imap = _imap_connect(
            account["imap_host"], account["imap_port"],
            user_email, password,
        )

        new_email_ids = []

        for folder in folders:
            folder_stats = await _sync_folder(
                imap, pool, user_email, folder, new_email_ids,
            )
            stats["folders"][folder] = folder_stats
            stats["total_new"] += folder_stats.get("new", 0)
            stats["total_flag_updates"] += folder_stats.get("flag_updates", 0)
            stats["total_deletions"] += folder_stats.get("deletions", 0)

        imap.logout()

        # Batch embed new emails
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
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE mail_accounts SET sync_status = 'error', "
                "sync_error = $2 "
                "WHERE email = $1 AND deleted_at IS NULL",
                user_email, str(e),
            )
        raise

    return stats


async def _sync_folder(
    imap: imaplib.IMAP4_SSL,
    pool,
    user_email: str,
    folder: str,
    new_email_ids: list[int],
) -> dict:
    """Sync a single folder with smart incremental logic."""
    fstats = {"mode": "incremental", "new": 0, "flag_updates": 0, "deletions": 0}

    try:
        msg_count, uidvalidity = _select_folder(imap, folder)
    except Exception as e:
        return {"error": str(e)}

    # Get sync state for this folder
    async with pool.acquire() as conn:
        state = await conn.fetchrow(
            "SELECT uidvalidity, highest_uid FROM sync_state "
            "WHERE owner_email = $1 AND folder = $2",
            user_email, folder,
        )

    need_full = False
    highest_uid = 0

    if state is None:
        # First sync ever for this folder
        need_full = True
        fstats["mode"] = "full (first sync)"
    elif state["uidvalidity"] != uidvalidity and state["uidvalidity"] is not None:
        # UIDVALIDITY changed — server rebuilt UIDs, all our stored UIDs are invalid
        need_full = True
        fstats["mode"] = "full (UIDVALIDITY changed)"
        # Soft-delete all emails for this folder since UIDs are now meaningless
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE emails SET deleted_at = NOW() "
                "WHERE owner_email = $1 AND folder = $2 AND deleted_at IS NULL",
                user_email, folder,
            )
    else:
        highest_uid = state["highest_uid"] or 0

    # --- Fetch new messages ---
    if need_full:
        server_uids = _fetch_uids(imap, "ALL")
    elif highest_uid > 0:
        # Incremental: only UIDs above our watermark
        server_uids = _fetch_uids(imap, f"UID {highest_uid + 1}:*")
        # IMAP quirk: UID X:* always returns at least UID X even if nothing new
        server_uids = [u for u in server_uids if u > highest_uid]
    else:
        server_uids = _fetch_uids(imap, "ALL")

    fstats["server_count"] = msg_count
    fstats["to_fetch"] = len(server_uids)

    # Get already-synced UIDs for dedup
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT uid FROM emails WHERE owner_email = $1 AND folder = $2 AND deleted_at IS NULL",
            user_email, folder,
        )
        synced_uids = {r["uid"] for r in rows}

    # Filter out already-synced
    uids_to_fetch = [u for u in server_uids if u not in synced_uids]

    if uids_to_fetch:
        messages = _fetch_messages(imap, uids_to_fetch)
        for uid, raw in messages:
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            body = extract_body(msg)
            date_sent = parse_date(msg)
            urgency, priority = score_email(
                msg.get("Subject", ""), body,
                msg.get("From", ""), date_sent,
            )

            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """INSERT INTO emails
                        (uid, message_id, from_addr, to_addrs, cc_addrs,
                         subject, date_sent, body_text, folder, owner_email,
                         urgency, priority)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
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
                    urgency,
                    priority,
                )
                if row:
                    new_email_ids.append(row["id"])
                    fstats["new"] += 1

    # --- Flag sync: check read/unread for recent emails ---
    all_server_uids = _fetch_uids(imap, "ALL") if not need_full else server_uids
    if all_server_uids:
        # Only sync flags for last 200 to keep it fast
        recent_uids = all_server_uids[-200:]
        server_flags = _fetch_flags(imap, recent_uids)

        if server_flags:
            async with pool.acquire() as conn:
                db_rows = await conn.fetch(
                    "SELECT uid, is_read FROM emails "
                    "WHERE owner_email = $1 AND folder = $2 AND uid = ANY($3::int[]) "
                    "AND deleted_at IS NULL",
                    user_email, folder, list(server_flags.keys()),
                )
                for r in db_rows:
                    server_read = "\\Seen" in server_flags.get(r["uid"], set())
                    if server_read != r["is_read"]:
                        await conn.execute(
                            "UPDATE emails SET is_read = $1 WHERE owner_email = $2 "
                            "AND folder = $3 AND uid = $4 AND deleted_at IS NULL",
                            server_read, user_email, folder, r["uid"],
                        )
                        fstats["flag_updates"] += 1

    # --- Detect server-side deletions ---
    all_server_uid_set = set(all_server_uids) if all_server_uids else set()
    if synced_uids and all_server_uid_set:
        deleted_uids = synced_uids - all_server_uid_set
        if deleted_uids:
            async with pool.acquire() as conn:
                result = await conn.execute(
                    "UPDATE emails SET deleted_at = NOW() "
                    "WHERE owner_email = $1 AND folder = $2 "
                    "AND uid = ANY($3::int[]) AND deleted_at IS NULL",
                    user_email, folder, list(deleted_uids),
                )
                fstats["deletions"] = int(result.split()[-1])

    # --- Update sync state ---
    new_highest = max(all_server_uids) if all_server_uids else highest_uid
    now_col = "last_full_sync" if need_full else "last_incremental_sync"

    async with pool.acquire() as conn:
        await conn.execute(
            f"""INSERT INTO sync_state (owner_email, folder, uidvalidity, highest_uid, {now_col})
            VALUES ($1, $2, $3, $4, NOW())
            ON CONFLICT (owner_email, folder) DO UPDATE SET
                uidvalidity = $3, highest_uid = $4, {now_col} = NOW()""",
            user_email, folder, uidvalidity, new_highest,
        )

    return fstats


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

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

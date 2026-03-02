"""Per-user credential fetching.

Supports two backends:
1. Environment variables: MAIL_PASSWORD__<user> (works today, no extra infra)
2. eidos-vault API: requires VAULT_SERVICE_TOKEN (future, once service auth is set up)

Env var names are derived from email: daniel@eidosagi.com → MAIL_PASSWORD__daniel
(prefix before @, lowercased).
"""

import os
import httpx
from app.config import VAULT_URL, VAULT_SERVICE_TOKEN
from app.database import get_pool


def _env_key(email: str) -> str:
    """Convert email to env var name: daniel@eidosagi.com → MAIL_PASSWORD__daniel"""
    local_part = email.split("@")[0].lower()
    return f"MAIL_PASSWORD__{local_part}"


async def get_mail_password(user_email: str) -> str | None:
    """Fetch mail password for the given user.

    Priority:
    1. Environment variable (MAIL_PASSWORD__<user>)
    2. eidos-vault API (if VAULT_SERVICE_TOKEN is configured)
    """
    # 1. Try env var (immediate, no network)
    env_password = os.environ.get(_env_key(user_email))
    if env_password:
        return env_password

    # 2. Try vault API (requires service token)
    if not VAULT_SERVICE_TOKEN:
        return None

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT vault_secret_path FROM mail_accounts "
            "WHERE email = $1 AND enabled = TRUE AND deleted_at IS NULL",
            user_email,
        )
    if not row or not row["vault_secret_path"]:
        return None

    path = row["vault_secret_path"]
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{VAULT_URL}/api/secrets/{path}",
            headers={"Authorization": f"Bearer {VAULT_SERVICE_TOKEN}"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        return data.get("value")


async def get_mail_account(user_email: str) -> dict | None:
    """Fetch mail account config from database."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT email, display_name, imap_host, imap_port, "
            "smtp_host, smtp_port, vault_secret_path "
            "FROM mail_accounts "
            "WHERE email = $1 AND enabled = TRUE AND deleted_at IS NULL",
            user_email,
        )
    if not row:
        return None
    return dict(row)

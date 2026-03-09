"""Client for calling the worker service.

When MONOLITH_MODE is set, imports functions directly (local dev).
Otherwise, makes HTTP calls to the worker service.
"""

import os

import httpx

from app.config import WORKER_URL, WORKER_SECRET

MONOLITH_MODE = os.environ.get("MONOLITH_MODE", "").lower() in ("1", "true", "yes")


async def embed_query(query: str) -> str:
    """Get pgvector string for a search query."""
    if MONOLITH_MODE:
        from app.embeddings import encode_query
        return encode_query(query)

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            f"{WORKER_URL}/embed-query",
            json={"query": query},
            headers={"X-Worker-Secret": WORKER_SECRET},
        )
        resp.raise_for_status()
        return resp.json()["vector"]


async def trigger_sync(user_email: str) -> dict:
    """Trigger IMAP sync for a user."""
    if MONOLITH_MODE:
        from app.sync import sync_emails_for_user
        return await sync_emails_for_user(user_email)

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(
            f"{WORKER_URL}/sync",
            json={"email": user_email},
            headers={"X-Worker-Secret": WORKER_SECRET},
        )
        resp.raise_for_status()
        return resp.json()["stats"]

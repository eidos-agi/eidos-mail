"""Worker service: embedding generation and IMAP sync.

Runs as a separate FastAPI app behind Railway private networking.
No public URL — only reachable from eidos-mail-web.
"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.database import init_pool, close_pool
from app.embeddings import encode_query, get_model
from app.sync import sync_emails_for_user

WORKER_SECRET = os.environ.get("WORKER_SECRET", "")


def _check_secret(request: Request) -> bool:
    if not WORKER_SECRET:
        return True  # no secret configured (local dev)
    return request.headers.get("X-Worker-Secret") == WORKER_SECRET


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool(run_migrations=False)
    # Pre-load embedding model so first request isn't slow
    get_model()
    yield
    await close_pool()


app = FastAPI(title="eidos-mail-worker", lifespan=lifespan)


@app.get("/health")
async def health():
    from app.database import get_pool
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.fetchval("SELECT 1")
    return {"status": "ok", "service": "worker"}


@app.post("/embed-query")
async def embed_query_endpoint(request: Request):
    if not _check_secret(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    query = data.get("query", "").strip()
    if not query:
        return JSONResponse({"error": "query is required"}, status_code=400)

    vec_str = encode_query(query)
    return {"vector": vec_str}


@app.post("/sync")
async def sync_endpoint(request: Request):
    if not _check_secret(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    data = await request.json()
    user_email = data.get("email", "").strip()
    if not user_email:
        return JSONResponse({"error": "email is required"}, status_code=400)

    stats = await sync_emails_for_user(user_email)
    return {"stats": stats}

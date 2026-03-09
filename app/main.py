"""FastAPI app: HTMX web UI + REST API for email operations."""

import hmac
import imaplib
import os
import secrets
import smtplib
from email.mime.text import MIMEText
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from markupsafe import escape
from pathlib import Path

from app.config import SESSION_SECRET
from app.database import init_pool, close_pool, get_pool
from app.worker_client import embed_query, trigger_sync
from app.auth import router as auth_router, AuthRequired, require_web_auth, require_api_auth
from app.vault_client import get_mail_password, get_mail_account


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(
    title="eidos-mail",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET or "dev-secret-change-me", https_only=False)
app.include_router(auth_router)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
_env = templates.env


# ---------------------------------------------------------------------------
# CSRF helpers
# ---------------------------------------------------------------------------

def _csrf_token(request: Request) -> str:
    """Get or create a CSRF token for the session."""
    token = request.session.get("_csrf")
    if not token:
        token = secrets.token_hex(32)
        request.session["_csrf"] = token
    return token


def _csrf_validate(request: Request, form_token: str | None):
    """Validate CSRF token from form submission."""
    session_token = request.session.get("_csrf", "")
    if not form_token or not hmac.compare_digest(session_token, form_token):
        raise ValueError("CSRF validation failed")


# ---------------------------------------------------------------------------
# Template rendering helper
# ---------------------------------------------------------------------------

def _render(template_name: str, **ctx) -> str:
    return _env.get_template(template_name).render(**ctx)


@app.exception_handler(AuthRequired)
async def auth_required_handler(request: Request, exc: AuthRequired):
    return RedirectResponse(url="/auth/login", status_code=302)


def snippet(text: str | None, max_len: int = 80) -> str:
    if not text:
        return ""
    text = " ".join(text.split())
    return text[:max_len] + "..." if len(text) > max_len else text


# ---------------------------------------------------------------------------
# Eisenhower Matrix helpers
# ---------------------------------------------------------------------------

IKE_THRESHOLD = 0.5  # urgency/priority midpoint


def _ike_quadrant(urgency: float, priority: float) -> str:
    """Classify into Eisenhower quadrant."""
    if urgency >= IKE_THRESHOLD and priority >= IKE_THRESHOLD:
        return "do"
    elif urgency < IKE_THRESHOLD and priority >= IKE_THRESHOLD:
        return "schedule"
    elif urgency >= IKE_THRESHOLD and priority < IKE_THRESHOLD:
        return "delegate"
    else:
        return "eliminate"


def _ike_filter_sql(quadrant: str) -> str:
    """Return SQL WHERE clause fragment for a quadrant."""
    if quadrant == "do":
        return f"AND urgency >= {IKE_THRESHOLD} AND priority >= {IKE_THRESHOLD}"
    elif quadrant == "schedule":
        return f"AND urgency < {IKE_THRESHOLD} AND priority >= {IKE_THRESHOLD}"
    elif quadrant == "delegate":
        return f"AND urgency >= {IKE_THRESHOLD} AND priority < {IKE_THRESHOLD}"
    elif quadrant == "eliminate":
        return f"AND urgency < {IKE_THRESHOLD} AND priority < {IKE_THRESHOLD}"
    return ""


async def _ike_counts(user_email: str, folder: str) -> dict:
    """Count unread emails in each Eisenhower quadrant."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT urgency, priority FROM emails
            WHERE owner_email = $1 AND folder = $2
            AND is_read = FALSE AND deleted_at IS NULL""",
            user_email, folder,
        )
    counts = {"do": 0, "schedule": 0, "delegate": 0, "eliminate": 0}
    for r in rows:
        q = _ike_quadrant(r["urgency"] or 0.5, r["priority"] or 0.5)
        counts[q] += 1
    return counts


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    pool = await get_pool()
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM emails WHERE deleted_at IS NULL"
        )
    return {"status": "ok", "emails": count}


# ---------------------------------------------------------------------------
# HTMX Web UI
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request, email: str = Depends(require_web_auth)):
    inbox_html = await _inbox_html(email)
    return templates.TemplateResponse("layout.html", {
        "request": request, "content": inbox_html,
        "user_email": email, "active_tab": "inbox",
    })


@app.get("/inbox", response_class=HTMLResponse)
async def inbox(
    page: int = Query(1, ge=1),
    folder: str = Query("INBOX"),
    ike: str | None = Query(None),
    email: str = Depends(require_web_auth),
):
    return HTMLResponse(await _inbox_html(email, page=page, folder=folder, ike_param=ike))


PER_PAGE = 50


async def _inbox_html(
    user_email: str, page: int = 1, folder: str = "INBOX", ike_param: str | None = None,
) -> str:
    pool = await get_pool()
    offset = (page - 1) * PER_PAGE

    ike_on = ike_param is not None
    ike_quadrant = ike_param if ike_param and ike_param != "all" else None

    # Build query with optional Ike filter
    where_extra = ""
    if ike_quadrant:
        where_extra = _ike_filter_sql(ike_quadrant)

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            f"SELECT COUNT(*) FROM emails "
            f"WHERE owner_email = $1 AND folder = $2 AND deleted_at IS NULL {where_extra}",
            user_email, folder,
        )
        rows = await conn.fetch(
            f"""SELECT id, from_addr, subject, date_sent, body_text, is_read
            FROM emails
            WHERE owner_email = $1 AND folder = $2 AND deleted_at IS NULL {where_extra}
            ORDER BY date_sent DESC LIMIT $3 OFFSET $4""",
            user_email, folder, PER_PAGE, offset,
        )
        unread_count = await conn.fetchval(
            "SELECT COUNT(*) FROM emails "
            "WHERE owner_email = $1 AND folder = $2 AND is_read = FALSE AND deleted_at IS NULL",
            user_email, folder,
        )

        # Folder counts for nav
        folder_counts = await conn.fetch(
            "SELECT folder, COUNT(*) as cnt FROM emails "
            "WHERE owner_email = $1 AND deleted_at IS NULL "
            "GROUP BY folder ORDER BY folder",
            user_email,
        )

    emails_list = [{
        "id": r["id"], "from_addr": r["from_addr"],
        "subject": r["subject"] or "(no subject)",
        "date_sent": str(r["date_sent"])[:16] if r["date_sent"] else "",
        "snippet": snippet(r["body_text"]),
        "is_read": r["is_read"],
    } for r in rows]

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    # Build folder nav data
    folder_map = {r["folder"]: r["cnt"] for r in folder_counts}
    folders = [
        {"name": "INBOX", "label": "inbox", "count": folder_map.get("INBOX", 0)},
        {"name": "Sent", "label": "sent", "count": folder_map.get("Sent", 0)},
        {"name": "Drafts", "label": "drafts", "count": folder_map.get("Drafts", 0)},
    ]

    # Ike counts (only if ike is on)
    ike_counts = await _ike_counts(user_email, folder) if ike_on else {}

    return _render(
        "partials/inbox.html",
        emails=emails_list, total=total, page=page,
        per_page=PER_PAGE, total_pages=total_pages,
        active_folder=folder, folders=folders,
        unread_count=unread_count,
        ike_on=ike_on, ike_quadrant=ike_quadrant, counts=ike_counts,
    )


@app.get("/email/{email_id}", response_class=HTMLResponse)
async def email_detail(email_id: int, email: str = Depends(require_web_auth)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM emails WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
            email_id, email,
        )
        # Mark as read
        if row and not row["is_read"]:
            await conn.execute(
                "UPDATE emails SET is_read = TRUE WHERE id = $1", email_id,
            )
    if not row:
        return HTMLResponse('<div style="color:var(--muted)">Not found</div>')
    return HTMLResponse(_render("partials/email_detail.html", email=dict(row)))


@app.get("/search", response_class=HTMLResponse)
async def search_page(_email: str = Depends(require_web_auth)):
    return HTMLResponse(_render("partials/search.html"))


@app.get("/search/results", response_class=HTMLResponse)
async def search_results(
    q: str = Query(""),
    email: str = Depends(require_web_auth),
):
    q = q.strip()
    if not q or len(q) < 2:
        return HTMLResponse("")

    try:
        vec_str = await embed_query(q)
    except Exception:
        return HTMLResponse('<div style="color:var(--muted)">Search unavailable — worker is down.</div>')

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.id, e.from_addr, e.subject, e.date_sent, e.body_text, e.is_read
            FROM email_vectors v
            JOIN emails e ON e.id = v.email_id
            WHERE e.owner_email = $1 AND e.deleted_at IS NULL
            ORDER BY v.embedding <-> $2::vector
            LIMIT 20""",
            email, vec_str,
        )

    emails_list = [{
        "id": r["id"], "from_addr": r["from_addr"],
        "subject": r["subject"] or "(no subject)",
        "date_sent": str(r["date_sent"])[:16] if r["date_sent"] else "",
        "snippet": snippet(r["body_text"]),
        "is_read": r["is_read"],
    } for r in rows]

    return HTMLResponse(_render("partials/search_results.html", emails=emails_list))


@app.get("/compose", response_class=HTMLResponse)
async def compose_page(
    request: Request,
    draft_id: int | None = Query(None),
    reply_to: int | None = Query(None),
    forward: int | None = Query(None),
    _email: str = Depends(require_web_auth),
):
    draft_to = draft_subject = draft_body = ""

    pool = await get_pool()

    if reply_to:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT from_addr, subject, body_text FROM emails "
                "WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
                reply_to, _email,
            )
        if row:
            draft_to = row["from_addr"] or ""
            subj = row["subject"] or ""
            draft_subject = subj if subj.lower().startswith("re:") else f"Re: {subj}"
            original = (row["body_text"] or "")[:500]
            draft_body = f"\n\n---\n> {original}"

    elif forward:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT from_addr, subject, body_text, date_sent FROM emails "
                "WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
                forward, _email,
            )
        if row:
            subj = row["subject"] or ""
            draft_subject = subj if subj.lower().startswith("fwd:") else f"Fwd: {subj}"
            date_str = str(row["date_sent"])[:16] if row["date_sent"] else ""
            original = row["body_text"] or ""
            draft_body = f"\n\n------- Forwarded message -------\nFrom: {row['from_addr']}\nDate: {date_str}\nSubject: {row['subject']}\n\n{original}"

    elif draft_id:
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT to_addrs, subject, body_text FROM emails "
                "WHERE id = $1 AND owner_email = $2 AND folder = 'Drafts' AND deleted_at IS NULL",
                draft_id, _email,
            )
        if row:
            draft_to = row["to_addrs"] or ""
            draft_subject = row["subject"] or ""
            draft_body = row["body_text"] or ""

    return HTMLResponse(_render(
        "partials/compose.html",
        csrf_token=_csrf_token(request),
        draft_id=draft_id, draft_to=draft_to,
        draft_subject=draft_subject, draft_body=draft_body,
    ))


@app.post("/sync", response_class=HTMLResponse)
async def web_sync(email: str = Depends(require_web_auth)):
    """Trigger per-user IMAP sync from web UI."""
    try:
        stats = await trigger_sync(email)
    except Exception:
        return HTMLResponse('<span style="color:#ef5350">Sync unavailable — worker is down.</span>')
    new = stats.get("total_new", 0)
    if "error" in stats:
        return HTMLResponse(f'<span style="color:#ef5350">{escape(stats["error"])}</span>')
    return HTMLResponse(f'<span style="color:#4caf50">synced ({new} new)</span>')


@app.post("/mark-all-read", response_class=HTMLResponse)
async def mark_all_read(
    folder: str = Query("INBOX"),
    email: str = Depends(require_web_auth),
):
    """Mark all emails in a folder as read."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE emails SET is_read = TRUE "
            "WHERE owner_email = $1 AND folder = $2 AND is_read = FALSE AND deleted_at IS NULL",
            email, folder,
        )
    return HTMLResponse(await _inbox_html(email, folder=folder))


@app.post("/mark-read", response_class=HTMLResponse)
async def mark_read(request: Request, email: str = Depends(require_web_auth)):
    """Mark specific emails as read (from swipe/quick actions)."""
    data = await request.json()
    ids = data.get("ids", [])
    if not ids:
        return HTMLResponse("")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE emails SET is_read = $1 WHERE id = ANY($2::int[]) AND owner_email = $3 AND deleted_at IS NULL",
            data.get("read", True), ids, email,
        )
    return HTMLResponse("ok")


@app.post("/delete/{email_id}", response_class=HTMLResponse)
async def delete_email_web(email_id: int, email: str = Depends(require_web_auth)):
    """Soft-delete from web UI (swipe/quick actions)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE emails SET deleted_at = NOW() WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
            email_id, email,
        )
    return HTMLResponse("ok")


@app.post("/draft", response_class=HTMLResponse)
async def save_draft(request: Request, email: str = Depends(require_web_auth)):
    """Save or update a draft email."""
    form = await request.form()

    try:
        _csrf_validate(request, str(form.get("_csrf", "")))
    except ValueError:
        return HTMLResponse('<div class="flash flash-err">Invalid request.</div>')

    to = str(form.get("to", "")).strip()
    subject = str(form.get("subject", "")).strip()
    body = str(form.get("body", "")).strip()
    draft_id = form.get("draft_id")

    pool = await get_pool()
    async with pool.acquire() as conn:
        if draft_id:
            await conn.execute(
                "UPDATE emails SET to_addrs = $1, subject = $2, body_text = $3 "
                "WHERE id = $4 AND owner_email = $5 AND folder = 'Drafts' AND deleted_at IS NULL",
                to, subject, body, int(str(draft_id)), email,
            )
        else:
            await conn.execute(
                """INSERT INTO emails (from_addr, to_addrs, subject, body_text, folder, owner_email, is_read)
                VALUES ($1, $2, $3, $4, 'Drafts', $5, TRUE)""",
                email, to, subject, body, email,
            )

    return HTMLResponse('<div class="flash flash-ok">Draft saved.</div>')


@app.post("/send", response_class=HTMLResponse)
async def send_email_htmx(request: Request, email: str = Depends(require_web_auth)):
    form = await request.form()

    # CSRF check
    try:
        _csrf_validate(request, str(form.get("_csrf", "")))
    except ValueError:
        return HTMLResponse('<div class="flash flash-err">Invalid request. Please reload and try again.</div>')

    to = str(form.get("to", "")).strip()
    subject = str(form.get("subject", "")).strip()
    body = str(form.get("body", "")).strip()
    draft_id = form.get("draft_id")

    if not to:
        return HTMLResponse('<div class="flash flash-err">To address required.</div>')

    try:
        account = await get_mail_account(email)
        if not account:
            return HTMLResponse(f'<div class="flash flash-err">No mail account configured for {escape(email)}</div>')

        password = await get_mail_password(email)
        if not password:
            return HTMLResponse(f'<div class="flash flash-err">Could not fetch credentials for {escape(email)}</div>')

        msg = MIMEText(body)
        msg["From"] = email
        msg["To"] = to
        msg["Subject"] = subject

        with smtplib.SMTP_SSL(account["smtp_host"], account["smtp_port"]) as smtp:
            smtp.login(email, password)
            smtp.send_message(msg)

        # Soft-delete the draft if sending from one
        if draft_id:
            pool = await get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE emails SET deleted_at = NOW() WHERE id = $1 AND owner_email = $2 AND folder = 'Drafts'",
                    int(str(draft_id)), email,
                )

        return HTMLResponse(f'<div class="flash flash-ok">Sent to {escape(to)}</div>')
    except Exception as e:
        return HTMLResponse(f'<div class="flash flash-err">Error: {escape(str(e))}</div>')


# ---------------------------------------------------------------------------
# IMAP Diagnostics
# ---------------------------------------------------------------------------

@app.get("/imap", response_class=HTMLResponse)
async def imap_diagnostics(email: str = Depends(require_web_auth)):
    """IMAP connection diagnostics."""
    account = await get_mail_account(email)
    if not account:
        return HTMLResponse(_render(
            "partials/imap.html",
            connected=False, error="No mail account configured",
            account=None, sync_info=None, imap_folders=[],
            host="", port=0,
        ))

    # Sync status from DB
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT sync_status, last_sync_at, sync_error FROM mail_accounts "
            "WHERE email = $1 AND deleted_at IS NULL",
            email,
        )
    sync_info = None
    if row:
        sync_info = {
            "status": row["sync_status"],
            "last_sync": str(row["last_sync_at"])[:19] if row["last_sync_at"] else None,
            "error": row["sync_error"],
        }

    # Try IMAP connection test
    host = account["imap_host"]
    port = account["imap_port"]
    connected = False
    imap_folders = []
    error = None

    try:
        password = await get_mail_password(email)
        if not password:
            error = "Could not fetch credentials"
        else:
            imap = imaplib.IMAP4_SSL(host, port)
            imap.login(email, password)
            connected = True

            # List folders with message counts
            _, folder_data = imap.list()
            for item in (folder_data or []):
                if isinstance(item, bytes):
                    parts = item.decode().split('"')
                    if len(parts) >= 3:
                        folder_name = parts[-2].strip() if parts[-2].strip() else parts[-1].strip()
                    else:
                        folder_name = item.decode().split()[-1]

                    try:
                        status, data = imap.select(folder_name, readonly=True)
                        if status == "OK" and data[0]:
                            count = int(data[0])
                            imap_folders.append({"name": folder_name, "count": count})
                    except Exception:
                        imap_folders.append({"name": folder_name, "count": "?"})

            imap.logout()
    except Exception as e:
        error = str(e)

    return HTMLResponse(_render(
        "partials/imap.html",
        connected=connected, error=error,
        account=account, sync_info=sync_info,
        imap_folders=imap_folders,
        host=host, port=port,
    ))


# ---------------------------------------------------------------------------
# Infrastructure Dashboard
# ---------------------------------------------------------------------------

@app.get("/infra", response_class=HTMLResponse)
async def infra_dashboard(email: str = Depends(require_web_auth)):
    """Service infrastructure dashboard."""
    pool = await get_pool()

    async with pool.acquire() as conn:
        # Email stats
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM emails WHERE deleted_at IS NULL"
        )
        folder_stats = await conn.fetch(
            "SELECT folder, COUNT(*) as cnt FROM emails "
            "WHERE deleted_at IS NULL GROUP BY folder ORDER BY folder"
        )
        vector_count = await conn.fetchval(
            "SELECT COUNT(*) FROM email_vectors"
        )

        # Mail accounts
        accounts = await conn.fetch(
            "SELECT email, sync_status, last_sync_at, sync_error "
            "FROM mail_accounts WHERE deleted_at IS NULL ORDER BY email"
        )

    # Pool stats
    db_info = {
        "pool_size": pool.get_size(),
        "pool_min": pool.get_min_size(),
        "pool_max": pool.get_max_size(),
        "pool_free": pool.get_idle_size(),
    }

    stats = {
        "total": total,
        "folders": [{"folder": r["folder"], "count": r["cnt"]} for r in folder_stats],
        "vectors": vector_count,
        "vector_pct": round(vector_count / total * 100, 1) if total > 0 else 0,
    }

    accounts_list = [{
        "email": r["email"],
        "sync_status": r["sync_status"],
        "last_sync": str(r["last_sync_at"])[:19] if r["last_sync_at"] else None,
        "sync_error": r["sync_error"],
    } for r in accounts]

    # Railway env info
    railway = None
    env_name = os.environ.get("RAILWAY_ENVIRONMENT_NAME")
    svc_name = os.environ.get("RAILWAY_SERVICE_NAME")
    if env_name or svc_name:
        railway = {"environment": env_name, "service": svc_name}

    return HTMLResponse(_render(
        "partials/infra.html",
        db=db_info, stats=stats, accounts=accounts_list, railway=railway,
    ))


# ---------------------------------------------------------------------------
# REST API (for agents)
# ---------------------------------------------------------------------------

@app.post("/api/sync")
async def api_sync(email: str = Depends(require_api_auth)):
    """Trigger per-user IMAP sync."""
    try:
        stats = await trigger_sync(email)
    except Exception as e:
        return JSONResponse({"error": f"Worker error: {e}"}, status_code=503)
    return JSONResponse({"status": "ok", "stats": stats})


@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=2),
    email: str = Depends(require_api_auth),
):
    try:
        vec_str = await embed_query(q)
    except Exception as e:
        return JSONResponse({"error": f"Search unavailable: {e}"}, status_code=503)

    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.id, e.from_addr, e.to_addrs, e.subject,
                      e.date_sent, e.body_text, e.folder
            FROM email_vectors v
            JOIN emails e ON e.id = v.email_id
            WHERE e.owner_email = $1 AND e.deleted_at IS NULL
            ORDER BY v.embedding <-> $2::vector
            LIMIT 20""",
            email, vec_str,
        )
    return [_row_to_dict(r) for r in rows]


@app.get("/api/emails")
async def api_emails(
    recent: int = Query(20, ge=1, le=100),
    email: str = Depends(require_api_auth),
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, from_addr, to_addrs, subject, date_sent, body_text, folder
            FROM emails
            WHERE owner_email = $1 AND deleted_at IS NULL
            ORDER BY date_sent DESC LIMIT $2""",
            email, recent,
        )
    return [_row_to_dict(r) for r in rows]


@app.get("/api/emails/{email_id}")
async def api_email(email_id: int, email: str = Depends(require_api_auth)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM emails WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
            email_id, email,
        )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _row_to_dict(row)


@app.post("/api/emails/mark-read")
async def api_mark_read(request: Request, email: str = Depends(require_api_auth)):
    """Mark one or more emails as read or unread."""
    data = await request.json()
    ids = data.get("ids", [])
    read = data.get("read", True)

    if not ids or not isinstance(ids, list):
        return JSONResponse({"error": "ids (list of ints) required"}, status_code=400)

    pool = await get_pool()
    async with pool.acquire() as conn:
        updated = await conn.execute(
            "UPDATE emails SET is_read = $1 "
            "WHERE id = ANY($2::int[]) AND owner_email = $3 AND deleted_at IS NULL",
            read, ids, email,
        )
    count = int(updated.split()[-1])
    return {"status": "ok", "updated": count}


@app.post("/api/emails/{email_id}/delete")
async def api_delete_email(email_id: int, email: str = Depends(require_api_auth)):
    """Soft-delete an email."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "UPDATE emails SET deleted_at = NOW() "
            "WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
            email_id, email,
        )
    if result == "UPDATE 0":
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": email_id}


@app.post("/api/emails/{email_id}/reply")
async def api_reply(email_id: int, request: Request, email: str = Depends(require_api_auth)):
    """Reply to an email with proper threading headers."""
    data = await request.json()
    body = data.get("body", "").strip()
    if not body:
        return JSONResponse({"error": "body is required"}, status_code=400)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT from_addr, subject, message_id, body_text FROM emails "
            "WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
            email_id, email,
        )
    if not row:
        return JSONResponse({"error": "email not found"}, status_code=404)

    to = row["from_addr"]
    subject = row["subject"] or ""
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    # Build quoted reply
    original_snippet = (row["body_text"] or "")[:500]
    full_body = f"{body}\n\n---\n> {original_snippet}"

    try:
        account = await get_mail_account(email)
        if not account:
            return JSONResponse({"error": f"No mail account for {email}"}, status_code=400)
        password = await get_mail_password(email)
        if not password:
            return JSONResponse({"error": "Could not fetch credentials"}, status_code=500)

        msg = MIMEText(full_body)
        msg["From"] = email
        msg["To"] = to
        msg["Subject"] = subject
        if row["message_id"]:
            msg["In-Reply-To"] = row["message_id"]
            msg["References"] = row["message_id"]

        with smtplib.SMTP_SSL(account["smtp_host"], account["smtp_port"]) as smtp:
            smtp.login(email, password)
            smtp.send_message(msg)

        return {"status": "sent", "to": to, "subject": subject}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/emails/{email_id}/forward")
async def api_forward(email_id: int, request: Request, email: str = Depends(require_api_auth)):
    """Forward an email to another recipient."""
    data = await request.json()
    to = data.get("to", "").strip()
    note = data.get("body", "").strip()

    if not to:
        return JSONResponse({"error": "to is required"}, status_code=400)

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT from_addr, subject, body_text, date_sent FROM emails "
            "WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
            email_id, email,
        )
    if not row:
        return JSONResponse({"error": "email not found"}, status_code=404)

    subject = row["subject"] or ""
    if not subject.lower().startswith("fwd:"):
        subject = f"Fwd: {subject}"

    original = row["body_text"] or ""
    date_str = str(row["date_sent"])[:16] if row["date_sent"] else ""
    body = f"{note}\n\n------- Forwarded message -------\nFrom: {row['from_addr']}\nDate: {date_str}\nSubject: {row['subject']}\n\n{original}"

    try:
        account = await get_mail_account(email)
        if not account:
            return JSONResponse({"error": f"No mail account for {email}"}, status_code=400)
        password = await get_mail_password(email)
        if not password:
            return JSONResponse({"error": "Could not fetch credentials"}, status_code=500)

        msg = MIMEText(body)
        msg["From"] = email
        msg["To"] = to
        msg["Subject"] = subject

        with smtplib.SMTP_SSL(account["smtp_host"], account["smtp_port"]) as smtp:
            smtp.login(email, password)
            smtp.send_message(msg)

        return {"status": "forwarded", "to": to, "subject": subject}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/send")
async def api_send(request: Request, email: str = Depends(require_api_auth)):
    data = await request.json()
    to = data.get("to", "").strip()
    subject = data.get("subject", "").strip()
    body = data.get("body", "").strip()

    if not to:
        return JSONResponse({"error": "to is required"}, status_code=400)

    try:
        account = await get_mail_account(email)
        if not account:
            return JSONResponse({"error": f"No mail account for {email}"}, status_code=400)

        password = await get_mail_password(email)
        if not password:
            return JSONResponse({"error": "Could not fetch credentials"}, status_code=500)

        msg = MIMEText(body)
        msg["From"] = email
        msg["To"] = to
        msg["Subject"] = subject

        with smtplib.SMTP_SSL(account["smtp_host"], account["smtp_port"]) as smtp:
            smtp.login(email, password)
            smtp.send_message(msg)

        return {"status": "sent", "to": to}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_dict(row) -> dict:
    """Convert asyncpg Record to JSON-safe dict."""
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d

"""FastAPI app: HTMX web UI + REST API for email operations."""

import hmac
import secrets
import smtplib
from email.mime.text import MIMEText
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from markupsafe import escape
from pathlib import Path

from app.config import SESSION_SECRET
from app.database import init_pool, close_pool, get_pool
from app.sync import sync_emails_for_user
from app.embeddings import encode_query
from app.auth import router as auth_router, AuthRequired, require_web_auth, require_api_auth
from app.vault_client import get_mail_password, get_mail_account


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="eidos-mail", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET or "dev-secret-change-me", https_only=False)
app.include_router(auth_router)
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
    email: str = Depends(require_web_auth),
):
    return HTMLResponse(await _inbox_html(email, page=page))


PER_PAGE = 50


async def _inbox_html(user_email: str, page: int = 1) -> str:
    pool = await get_pool()
    offset = (page - 1) * PER_PAGE

    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM emails "
            "WHERE owner_email = $1 AND deleted_at IS NULL",
            user_email,
        )
        rows = await conn.fetch(
            """SELECT id, from_addr, subject, date_sent, body_text
            FROM emails
            WHERE owner_email = $1 AND deleted_at IS NULL
            ORDER BY date_sent DESC LIMIT $2 OFFSET $3""",
            user_email, PER_PAGE, offset,
        )

    emails_list = [{
        "id": r["id"], "from_addr": r["from_addr"],
        "subject": r["subject"] or "(no subject)",
        "date_sent": str(r["date_sent"])[:16] if r["date_sent"] else "",
        "snippet": snippet(r["body_text"]),
    } for r in rows]

    total_pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)

    return _render(
        "partials/inbox.html",
        emails=emails_list, total=total, page=page,
        per_page=PER_PAGE, total_pages=total_pages,
    )


@app.get("/email/{email_id}", response_class=HTMLResponse)
async def email_detail(email_id: int, email: str = Depends(require_web_auth)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM emails WHERE id = $1 AND owner_email = $2 AND deleted_at IS NULL",
            email_id, email,
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

    vec_str = encode_query(q)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.id, e.from_addr, e.subject, e.date_sent, e.body_text
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
    } for r in rows]

    return HTMLResponse(_render("partials/search_results.html", emails=emails_list))


@app.get("/compose", response_class=HTMLResponse)
async def compose_page(request: Request, _email: str = Depends(require_web_auth)):
    return HTMLResponse(_render("partials/compose.html", csrf_token=_csrf_token(request)))


@app.post("/sync", response_class=HTMLResponse)
async def web_sync(email: str = Depends(require_web_auth)):
    """Trigger per-user IMAP sync from web UI."""
    stats = await sync_emails_for_user(email)
    new = stats.get("total_new", 0)
    if "error" in stats:
        return HTMLResponse(f'<span style="color:#ef5350">{escape(stats["error"])}</span>')
    return HTMLResponse(f'<span style="color:#4caf50">synced ({new} new)</span>')


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

        return HTMLResponse(f'<div class="flash flash-ok">Sent to {escape(to)}</div>')
    except Exception as e:
        return HTMLResponse(f'<div class="flash flash-err">Error: {escape(str(e))}</div>')


# ---------------------------------------------------------------------------
# REST API (for agents)
# ---------------------------------------------------------------------------

@app.post("/api/sync")
async def api_sync(email: str = Depends(require_api_auth)):
    """Trigger per-user IMAP sync."""
    stats = await sync_emails_for_user(email)
    return JSONResponse({"status": "ok", "stats": stats})


@app.get("/api/search")
async def api_search(
    q: str = Query(..., min_length=2),
    email: str = Depends(require_api_auth),
):
    vec_str = encode_query(q)
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

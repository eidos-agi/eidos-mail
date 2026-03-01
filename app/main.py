"""FastAPI app: HTMX web UI + REST API for email operations."""

import smtplib
from email.mime.text import MIMEText
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path

from app.config import EMAIL_ADDRESS, EMAIL_PASSWORD, SMTP_HOST, SMTP_PORT
from app.database import init_pool, close_pool, get_pool
from app.sync import sync_emails
from app.embeddings import encode_query


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_pool()
    yield
    await close_pool()


app = FastAPI(title="eidos-mail", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


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
async def index(request: Request):
    inbox_html = await _inbox_html()
    return templates.TemplateResponse("layout.html", {
        "request": request, "content": inbox_html
    })


@app.get("/inbox", response_class=HTMLResponse)
async def inbox():
    return HTMLResponse(await _inbox_html())


async def _inbox_html() -> str:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, from_addr, subject, date_sent, body_text
            FROM emails WHERE deleted_at IS NULL
            ORDER BY date_sent DESC LIMIT 50"""
        )
    emails_list = [{
        "id": r["id"], "from_addr": r["from_addr"],
        "subject": r["subject"] or "(no subject)",
        "date_sent": str(r["date_sent"])[:16] if r["date_sent"] else "",
        "snippet": snippet(r["body_text"]),
    } for r in rows]

    count = len(emails_list)
    html = f'<div class="stats">{count} emails'
    html += ' <button class="sync-btn" hx-post="/api/sync" hx-target="#sync-status" hx-swap="innerHTML">sync</button>'
    html += ' <span id="sync-status" class="htmx-indicator">syncing...</span>'
    html += "</div>"
    for e in emails_list:
        html += f'''<div class="email-row" hx-get="/email/{e["id"]}" hx-target="#detail-{e["id"]}" hx-swap="innerHTML">
  <div class="email-from">{_esc(e["from_addr"])}</div>
  <div class="email-subject">{_esc(e["subject"])}</div>
  <div style="display:flex;justify-content:space-between;">
    <span class="email-snippet">{_esc(e["snippet"])}</span>
    <span class="email-date">{e["date_sent"]}</span>
  </div>
  <div id="detail-{e["id"]}"></div>
</div>'''
    if not emails_list:
        html += '<div style="color:var(--muted);padding:1rem;">No emails. Click sync to fetch.</div>'
    return html


@app.get("/email/{email_id}", response_class=HTMLResponse)
async def email_detail(email_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM emails WHERE id = $1", email_id
        )
    if not row:
        return HTMLResponse('<div style="color:var(--muted)">Not found</div>')
    return HTMLResponse(f'''<div class="email-detail">
  <div class="meta"><strong>From:</strong> {_esc(row["from_addr"])}</div>
  <div class="meta"><strong>To:</strong> {_esc(row["to_addrs"])}</div>
  {"" if not row["cc_addrs"] else f'<div class="meta"><strong>Cc:</strong> {_esc(row["cc_addrs"])}</div>'}
  <div class="meta"><strong>Date:</strong> {row["date_sent"]}</div>
  <div class="meta"><strong>Subject:</strong> {_esc(row["subject"])}</div>
  <pre>{_esc(row["body_text"])}</pre>
</div>''')


@app.get("/search", response_class=HTMLResponse)
async def search_page():
    return HTMLResponse('''
<div class="search-bar">
  <input type="text" name="q" id="search-input" placeholder="semantic search..."
    hx-get="/search/results" hx-target="#search-results" hx-trigger="keyup changed delay:400ms"
    hx-include="this" />
  <span class="htmx-indicator">searching...</span>
</div>
<div id="search-results"></div>
''')


@app.get("/search/results", response_class=HTMLResponse)
async def search_results(q: str = Query("")):
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
            WHERE e.deleted_at IS NULL
            ORDER BY v.embedding <-> $1::vector
            LIMIT 20""",
            vec_str,
        )

    html = ""
    for r in rows:
        e = {
            "id": r["id"], "from_addr": r["from_addr"],
            "subject": r["subject"] or "(no subject)",
            "date_sent": str(r["date_sent"])[:16] if r["date_sent"] else "",
            "snippet": snippet(r["body_text"]),
        }
        html += f'''<div class="email-row" hx-get="/email/{e["id"]}" hx-target="#detail-{e["id"]}" hx-swap="innerHTML">
  <div class="email-from">{_esc(e["from_addr"])}</div>
  <div class="email-subject">{_esc(e["subject"])}</div>
  <div style="display:flex;justify-content:space-between;">
    <span class="email-snippet">{_esc(e["snippet"])}</span>
    <span class="email-date">{e["date_sent"]}</span>
  </div>
  <div id="detail-{e["id"]}"></div>
</div>'''
    if not rows:
        html = '<div style="color:var(--muted);padding:1rem;">No results.</div>'
    return HTMLResponse(html)


@app.get("/compose", response_class=HTMLResponse)
async def compose_page():
    return HTMLResponse('''
<form hx-post="/send" hx-target="#send-result" hx-swap="innerHTML">
  <div class="form-group"><label>To</label><input type="text" name="to" required></div>
  <div class="form-group"><label>Subject</label><input type="text" name="subject"></div>
  <div class="form-group"><label>Body</label><textarea name="body" rows="8"></textarea></div>
  <button type="submit">send</button>
  <span class="htmx-indicator">sending...</span>
</form>
<div id="send-result"></div>
''')


@app.post("/send", response_class=HTMLResponse)
async def send_email_htmx(request: Request):
    form = await request.form()
    to = str(form.get("to", "")).strip()
    subject = str(form.get("subject", "")).strip()
    body = str(form.get("body", "")).strip()

    if not to:
        return HTMLResponse('<div class="flash flash-err">To address required.</div>')

    try:
        msg = MIMEText(body)
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to
        msg["Subject"] = subject

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        return HTMLResponse(f'<div class="flash flash-ok">Sent to {_esc(to)}</div>')
    except Exception as e:
        return HTMLResponse(f'<div class="flash flash-err">Error: {_esc(str(e))}</div>')


# ---------------------------------------------------------------------------
# REST API (for agents)
# ---------------------------------------------------------------------------

@app.post("/api/sync")
async def api_sync():
    """Trigger IMAP sync."""
    stats = await sync_emails()
    return JSONResponse({"status": "ok", "stats": stats})


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=2)):
    vec_str = encode_query(q)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT e.id, e.from_addr, e.to_addrs, e.subject,
                      e.date_sent, e.body_text, e.folder
            FROM email_vectors v
            JOIN emails e ON e.id = v.email_id
            WHERE e.deleted_at IS NULL
            ORDER BY v.embedding <-> $1::vector
            LIMIT 20""",
            vec_str,
        )
    return [_row_to_dict(r) for r in rows]


@app.get("/api/emails")
async def api_emails(recent: int = Query(20, ge=1, le=100)):
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT id, from_addr, to_addrs, subject, date_sent, body_text, folder
            FROM emails WHERE deleted_at IS NULL
            ORDER BY date_sent DESC LIMIT $1""",
            recent,
        )
    return [_row_to_dict(r) for r in rows]


@app.get("/api/emails/{email_id}")
async def api_email(email_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM emails WHERE id = $1 AND deleted_at IS NULL",
            email_id,
        )
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _row_to_dict(row)


@app.post("/api/send")
async def api_send(request: Request):
    data = await request.json()
    to = data.get("to", "").strip()
    subject = data.get("subject", "").strip()
    body = data.get("body", "").strip()

    if not to:
        return JSONResponse({"error": "to is required"}, status_code=400)

    try:
        msg = MIMEText(body)
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to
        msg["Subject"] = subject

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            smtp.send_message(msg)

        return {"status": "sent", "to": to}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: str | None) -> str:
    """HTML-escape a string."""
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _row_to_dict(row) -> dict:
    """Convert asyncpg Record to JSON-safe dict."""
    d = dict(row)
    for k, v in d.items():
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
    return d

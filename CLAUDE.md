# CLAUDE.md — eidos-mail

> Email service for Eidos AGI. IMAP sync, vector search, HTMX web UI, REST API.

## Architecture

```
Migadu IMAP/SMTP <-> FastAPI (eidos-mail) <-> Postgres + pgvector
                         |
                    HTMX web UI (humans)
                    REST API (agents)
```

## Stack

- Python 3.12, FastAPI, uvicorn, asyncpg, pgvector
- sentence-transformers (all-MiniLM-L6-v2, 384 dims)
- HTMX 2.0.4 for web UI
- Railway: Dockerfile + railway.toml + Postgres addon

## Project Structure

```
eidos-mail/
├── CLAUDE.md
├── Dockerfile
├── railway.toml
├── requirements.txt
├── app/
│   ├── main.py           <- FastAPI app + HTMX routes + API routes
│   ├── config.py          <- Settings from env
│   ├── database.py        <- asyncpg pool + migrations
│   ├── sync.py            <- IMAP sync
│   ├── embeddings.py      <- Vector embedding generation
│   └── templates/
│       └── layout.html    <- HTMX UI
└── migrations/
    └── 001_init.sql       <- emails + email_vectors tables
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `DATABASE_URL` | Postgres connection string (Railway provides this) |
| `IMAP_HOST` | IMAP server (imap.migadu.com) |
| `IMAP_PORT` | IMAP port (993) |
| `SMTP_HOST` | SMTP server (smtp.migadu.com) |
| `SMTP_PORT` | SMTP port (465) |
| `EMAIL_ADDRESS` | Email address (daniel@eidosagi.com) |
| `EMAIL_PASSWORD` | Email password |

## Endpoints

### Web UI (HTMX)
- `GET /` — inbox
- `GET /search` — semantic search
- `GET /compose` — compose form

### API (agents)
- `POST /api/sync` — trigger IMAP sync
- `GET /api/search?q=...` — vector search (JSON)
- `GET /api/emails?recent=N` — recent emails (JSON)
- `GET /api/emails/{id}` — single email (JSON)
- `POST /api/send` — send email (JSON)
- `POST /api/emails/mark-read` — mark emails read/unread (JSON: {ids, read})
- `POST /api/emails/{id}/delete` — soft-delete an email
- `POST /api/emails/{id}/reply` — reply with threading (JSON: {body})
- `POST /api/emails/{id}/forward` — forward email (JSON: {to, body})
- `POST /api/emails/{id}/undelete` — restore a soft-deleted email from trash
- `GET /health` — health check

## Development

```bash
# Install deps
pip install -r requirements.txt

# Run locally
DATABASE_URL=postgresql://... python -m uvicorn app.main:app --reload --port 8000
```

## Rules

- No raw secrets in code — use environment variables
- Soft deletes only (deleted_at column)
- All queries filter WHERE deleted_at IS NULL

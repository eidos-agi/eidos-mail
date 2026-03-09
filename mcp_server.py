"""MCP server for eidos-mail — exposes email operations as tools for Claude Code."""

import json
import time
from pathlib import Path

import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("eidos-mail")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MAIL_API = "https://mail.eidosagi.com"
DEFAULT_EMAIL = "daniel@eidosagi.com"
EIDOS_TOKEN_FILE = Path.home() / ".eidos" / "token.json"


def _get_token() -> str:
    """Load JWT from eidos CLI token cache, refreshing if expired."""
    if not EIDOS_TOKEN_FILE.exists():
        raise RuntimeError(
            "Not authenticated. Run `eidos login` first."
        )
    data = json.loads(EIDOS_TOKEN_FILE.read_text())
    token = data.get("token")
    expires_at = data.get("expires_at", 0)

    if time.time() >= expires_at:
        # Try to refresh using stored API key
        api_key = data.get("api_key")
        if not api_key:
            raise RuntimeError("Token expired and no API key stored. Run `eidos login`.")
        resp = httpx.post(
            "https://vault.eidosagi.com/api/auth/token",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Token refresh failed ({resp.status_code}). Run `eidos login`.")
        result = resp.json()
        token = result["token"]
        # Update cache
        data["token"] = token
        data["expires_at"] = time.time() + result["expires_in"]
        EIDOS_TOKEN_FILE.write_text(json.dumps(data))
        EIDOS_TOKEN_FILE.chmod(0o600)

    return token


def _headers() -> dict:
    return {"Authorization": f"Bearer {_get_token()}"}


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def inbox(recent: int = 10, email: str = DEFAULT_EMAIL) -> list[dict]:
    """Fetch recent emails from the inbox.

    Args:
        recent: Number of recent emails to fetch (1-100, default 10)
        email: Email account to check (default daniel@eidosagi.com)
    """
    recent = max(1, min(100, recent))
    resp = httpx.get(
        f"{MAIL_API}/api/emails",
        params={"recent": recent, "email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    emails = resp.json()
    # Return compact summaries to save tokens
    return [
        {
            "id": e["id"],
            "from": e.get("from_addr", ""),
            "to": e.get("to_addrs", ""),
            "subject": e.get("subject", ""),
            "date": str(e.get("date_sent", ""))[:16],
            "preview": (e.get("body_text") or "")[:200],
            "folder": e.get("folder", ""),
        }
        for e in emails
    ]


@mcp.tool()
def read_email(email_id: int, email: str = DEFAULT_EMAIL) -> dict:
    """Read a single email by ID. Returns full body text.

    Args:
        email_id: The email ID (from inbox results)
        email: Email account (default daniel@eidosagi.com)
    """
    resp = httpx.get(
        f"{MAIL_API}/api/emails/{email_id}",
        params={"email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def search_email(query: str, email: str = DEFAULT_EMAIL) -> list[dict]:
    """Semantic search across emails using vector similarity.

    Args:
        query: Natural language search query (e.g. "cloudflare DNS setup")
        email: Email account to search (default daniel@eidosagi.com)
    """
    resp = httpx.get(
        f"{MAIL_API}/api/search",
        params={"q": query, "email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    results = resp.json()
    return [
        {
            "id": e["id"],
            "from": e.get("from_addr", ""),
            "subject": e.get("subject", ""),
            "date": str(e.get("date_sent", ""))[:16],
            "preview": (e.get("body_text") or "")[:200],
        }
        for e in results
    ]


@mcp.tool()
def send_email(to: str, subject: str, body: str, email: str = DEFAULT_EMAIL) -> dict:
    """Send an email.

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Email body text
        email: Sender email account (default daniel@eidosagi.com)
    """
    resp = httpx.post(
        f"{MAIL_API}/api/send",
        json={"to": to, "subject": subject, "body": body},
        params={"email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def mark_read(
    ids: list[int], read: bool = True, email: str = DEFAULT_EMAIL,
) -> dict:
    """Mark one or more emails as read (or unread).

    Args:
        ids: List of email IDs to mark
        read: True to mark read, False to mark unread (default True)
        email: Email account (default daniel@eidosagi.com)
    """
    resp = httpx.post(
        f"{MAIL_API}/api/emails/mark-read",
        json={"ids": ids, "read": read},
        params={"email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def delete_email(email_id: int, email: str = DEFAULT_EMAIL) -> dict:
    """Soft-delete an email.

    Args:
        email_id: The email ID to delete
        email: Email account (default daniel@eidosagi.com)
    """
    resp = httpx.post(
        f"{MAIL_API}/api/emails/{email_id}/delete",
        params={"email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def reply_email(
    email_id: int, body: str, email: str = DEFAULT_EMAIL,
) -> dict:
    """Reply to an email. Adds proper threading headers and quotes the original.

    Args:
        email_id: The email ID to reply to
        body: Your reply text
        email: Email account (default daniel@eidosagi.com)
    """
    resp = httpx.post(
        f"{MAIL_API}/api/emails/{email_id}/reply",
        json={"body": body},
        params={"email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def forward_email(
    email_id: int, to: str, body: str = "", email: str = DEFAULT_EMAIL,
) -> dict:
    """Forward an email to another recipient.

    Args:
        email_id: The email ID to forward
        to: Recipient email address
        body: Optional note to include above the forwarded message
        email: Email account (default daniel@eidosagi.com)
    """
    resp = httpx.post(
        f"{MAIL_API}/api/emails/{email_id}/forward",
        json={"to": to, "body": body},
        params={"email": email},
        headers=_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool()
def sync_inbox(email: str = DEFAULT_EMAIL) -> dict:
    """Trigger IMAP sync to fetch new emails from the mail server.

    Args:
        email: Email account to sync (default daniel@eidosagi.com)
    """
    resp = httpx.post(
        f"{MAIL_API}/api/sync",
        params={"email": email},
        headers=_headers(),
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    mcp.run()


if __name__ == "__main__":
    main()

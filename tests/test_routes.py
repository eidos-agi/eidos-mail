"""Tests for HTMX web UI and API routes."""

import pytest
from unittest.mock import patch, AsyncMock
from tests.conftest import make_record, MockPool


@pytest.fixture
def pool_with_emails():
    """Pool that returns sample email data."""
    pool = MockPool()
    pool.conn.fetchval_returns = [
        3,   # total count
        1,   # unread count
    ]
    pool.conn.fetch_returns = [
        # emails query
        [
            make_record(id=1, from_addr="alice@example.com", subject="Hello",
                        date_sent=None, body_text="Hi there", is_read=False),
            make_record(id=2, from_addr="bob@example.com", subject="Meeting",
                        date_sent=None, body_text="Let's meet", is_read=True),
            make_record(id=3, from_addr="carol@example.com", subject="Update",
                        date_sent=None, body_text="Status update", is_read=True),
        ],
        # folder counts query
        [
            make_record(folder="INBOX", cnt=3),
            make_record(folder="Sent", cnt=1),
        ],
    ]
    return pool


@pytest.fixture
async def app_client(pool_with_emails):
    """Client with pre-loaded email data."""
    from httpx import ASGITransport, AsyncClient

    with patch("app.database.init_pool", new_callable=AsyncMock), \
         patch("app.database.close_pool", new_callable=AsyncMock):

        from app.main import app
        from app.auth import require_web_auth, require_api_auth

        async def fake_web_auth(request=None):
            return "test@eidosagi.com"

        async def fake_api_auth(request=None):
            return "test@eidosagi.com"

        app.dependency_overrides[require_web_auth] = fake_web_auth
        app.dependency_overrides[require_api_auth] = fake_api_auth

        with patch("app.main.get_pool", return_value=pool_with_emails):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac

        app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(app_client, pool_with_emails):
    pool_with_emails.conn.fetchval_returns = [42]
    resp = await app_client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["emails"] == 42


# ---------------------------------------------------------------------------
# Index / Inbox
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_index_requires_auth():
    """Index should redirect to login without auth."""
    from httpx import ASGITransport, AsyncClient

    with patch("app.database.init_pool", new_callable=AsyncMock), \
         patch("app.database.close_pool", new_callable=AsyncMock):

        from app.main import app
        # Clear any overrides
        app.dependency_overrides.clear()

        pool = MockPool()
        with patch("app.main.get_pool", return_value=pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test", follow_redirects=False) as ac:
                resp = await ac.get("/")
                assert resp.status_code == 302
                assert "/auth/login" in resp.headers["location"]


@pytest.mark.asyncio
async def test_index_returns_html(app_client):
    resp = await app_client.get("/")
    assert resp.status_code == 200
    assert "eidos-mail" in resp.text
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_inbox_with_folder(app_client, pool_with_emails):
    """Inbox should accept folder param."""
    pool_with_emails.conn.fetchval_returns = [0, 0]
    pool_with_emails.conn.fetch_returns = [[], []]
    resp = await app_client.get("/inbox?folder=Sent")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_inbox_with_ike(app_client, pool_with_emails):
    """Inbox with ike=all should show Eisenhower Matrix."""
    pool_with_emails.conn.fetchval_returns = [3, 1]
    pool_with_emails.conn.fetch_returns = [
        [
            make_record(id=1, from_addr="a@b.com", subject="Hi",
                        date_sent=None, body_text="body", is_read=False),
        ],
        [make_record(folder="INBOX", cnt=3)],
        # ike_counts query
        [
            make_record(urgency=0.8, priority=0.9),
            make_record(urgency=0.2, priority=0.8),
        ],
    ]
    resp = await app_client.get("/inbox?ike=all")
    assert resp.status_code == 200
    assert "eisenhower" in resp.text.lower() or "ike" in resp.text.lower()


@pytest.mark.asyncio
async def test_inbox_ike_quadrant_filter(app_client, pool_with_emails):
    """Inbox with ike=do should filter to that quadrant."""
    pool_with_emails.conn.fetchval_returns = [1, 0]
    pool_with_emails.conn.fetch_returns = [
        [make_record(id=1, from_addr="a@b.com", subject="Urgent",
                     date_sent=None, body_text="do it", is_read=False)],
        [make_record(folder="INBOX", cnt=1)],
        [make_record(urgency=0.8, priority=0.9)],
    ]
    resp = await app_client.get("/inbox?ike=do")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Email detail + mark as read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_email_detail(app_client, pool_with_emails):
    pool_with_emails.conn.fetchrow_returns = [
        make_record(
            id=1, from_addr="alice@example.com", to_addrs="test@eidosagi.com",
            cc_addrs="", subject="Hello", date_sent=None, body_text="Hi there",
            is_read=False,
        )
    ]
    resp = await app_client.get("/email/1")
    assert resp.status_code == 200
    assert "alice@example.com" in resp.text
    # Should have triggered mark-as-read UPDATE
    assert any("is_read = TRUE" in q for q, _ in pool_with_emails.conn.executed)


@pytest.mark.asyncio
async def test_email_detail_not_found(app_client, pool_with_emails):
    pool_with_emails.conn.fetchrow_returns = [None]
    resp = await app_client.get("/email/999")
    assert resp.status_code == 200
    assert "Not found" in resp.text


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_page(app_client):
    resp = await app_client.get("/search")
    assert resp.status_code == 200
    assert "search" in resp.text.lower()


@pytest.mark.asyncio
async def test_search_results_short_query(app_client):
    resp = await app_client.get("/search/results?q=a")
    assert resp.status_code == 200
    assert resp.text == ""  # too short


@pytest.mark.asyncio
async def test_search_results(app_client, pool_with_emails):
    pool_with_emails.conn.fetch_returns = [
        [make_record(id=1, from_addr="a@b.com", subject="Found it",
                     date_sent=None, body_text="result body", is_read=True)],
    ]
    with patch("app.main.encode_query", return_value="[" + ",".join(["0.1"] * 384) + "]"):
        resp = await app_client.get("/search/results?q=test query")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Compose
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_compose_page(app_client):
    resp = await app_client.get("/compose")
    assert resp.status_code == 200
    assert "send" in resp.text.lower()
    assert "save draft" in resp.text.lower()


@pytest.mark.asyncio
async def test_compose_load_draft(app_client, pool_with_emails):
    pool_with_emails.conn.fetchrow_returns = [
        make_record(to_addrs="draft-to@example.com", subject="Draft subj", body_text="Draft body"),
    ]
    resp = await app_client.get("/compose?draft_id=5")
    assert resp.status_code == 200
    assert "draft-to@example.com" in resp.text
    assert "Draft subj" in resp.text


# ---------------------------------------------------------------------------
# Mark all read
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_mark_all_read(app_client, pool_with_emails):
    # Reset for mark-all-read then inbox reload
    pool_with_emails.conn.fetchval_returns = [3, 0]
    pool_with_emails.conn.fetch_returns = [
        [make_record(id=1, from_addr="a@b.com", subject="Hi",
                     date_sent=None, body_text="body", is_read=True)],
        [make_record(folder="INBOX", cnt=3)],
    ]
    resp = await app_client.post("/mark-all-read?folder=INBOX")
    assert resp.status_code == 200
    # Should have executed an UPDATE marking is_read = TRUE
    assert any("is_read = TRUE" in q for q, _ in pool_with_emails.conn.executed)


# ---------------------------------------------------------------------------
# Save draft
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_draft(app_client, pool_with_emails):
    # Need CSRF token — get compose page first to set session
    # Since we're using dependency overrides, we just post directly
    # but CSRF will fail. Let's patch the validation.
    with patch("app.main._csrf_validate"):
        resp = await app_client.post("/draft", data={
            "_csrf": "fake",
            "to": "recipient@example.com",
            "subject": "My Draft",
            "body": "Draft content",
        })
    assert resp.status_code == 200
    assert "Draft saved" in resp.text


@pytest.mark.asyncio
async def test_save_draft_csrf_fail(app_client):
    """Draft save with bad CSRF should fail."""
    resp = await app_client.post("/draft", data={
        "_csrf": "bad-token",
        "to": "a@b.com",
        "subject": "x",
        "body": "y",
    })
    assert resp.status_code == 200
    assert "Invalid request" in resp.text


# ---------------------------------------------------------------------------
# Send email
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_send_no_to(app_client):
    with patch("app.main._csrf_validate"):
        resp = await app_client.post("/send", data={
            "_csrf": "fake",
            "to": "",
            "subject": "x",
            "body": "y",
        })
    assert resp.status_code == 200
    assert "To address required" in resp.text


@pytest.mark.asyncio
async def test_send_success(app_client, pool_with_emails):
    with patch("app.main._csrf_validate"), \
         patch("app.main.get_mail_account", return_value={"smtp_host": "smtp.test.com", "smtp_port": 465}), \
         patch("app.main.get_mail_password", return_value="secret"), \
         patch("app.main.smtplib") as mock_smtp:

        mock_server = mock_smtp.SMTP_SSL.return_value.__enter__.return_value
        resp = await app_client.post("/send", data={
            "_csrf": "fake",
            "to": "recipient@example.com",
            "subject": "Test",
            "body": "Hello",
        })
    assert resp.status_code == 200
    assert "Sent to" in resp.text


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync(app_client):
    with patch("app.main.sync_emails_for_user", return_value={"total_new": 5}):
        resp = await app_client.post("/sync")
    assert resp.status_code == 200
    assert "synced (5 new)" in resp.text


@pytest.mark.asyncio
async def test_sync_error(app_client):
    with patch("app.main.sync_emails_for_user", return_value={"error": "IMAP connection failed"}):
        resp = await app_client.post("/sync")
    assert resp.status_code == 200
    assert "IMAP connection failed" in resp.text


# ---------------------------------------------------------------------------
# IMAP diagnostics
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_imap_no_account(app_client):
    with patch("app.main.get_mail_account", return_value=None):
        resp = await app_client.get("/imap")
    assert resp.status_code == 200
    assert "No mail account configured" in resp.text


@pytest.mark.asyncio
async def test_imap_with_account(app_client, pool_with_emails):
    pool_with_emails.conn.fetchrow_returns = [
        make_record(sync_status="idle", last_sync_at=None, sync_error=None),
    ]
    with patch("app.main.get_mail_account", return_value={
        "imap_host": "imap.test.com", "imap_port": 993,
        "smtp_host": "smtp.test.com", "smtp_port": 465,
        "email": "test@eidosagi.com", "display_name": "Test",
    }), \
         patch("app.main.get_mail_password", return_value="secret"), \
         patch("app.main.imaplib") as mock_imap:

        mock_conn = mock_imap.IMAP4_SSL.return_value
        mock_conn.login.return_value = ("OK", [])
        mock_conn.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
        mock_conn.select.return_value = ("OK", [b"42"])
        mock_conn.logout.return_value = ("OK", [])

        resp = await app_client.get("/imap")
    assert resp.status_code == 200
    assert "connected" in resp.text


# ---------------------------------------------------------------------------
# Infra dashboard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_infra(app_client, pool_with_emails):
    pool_with_emails.conn.fetchval_returns = [100, 95]  # total, vector count
    pool_with_emails.conn.fetch_returns = [
        [make_record(folder="INBOX", cnt=80), make_record(folder="Sent", cnt=20)],
        [make_record(email="test@eidosagi.com", sync_status="idle",
                     last_sync_at=None, sync_error=None)],
    ]
    resp = await app_client.get("/infra")
    assert resp.status_code == 200
    assert "Infrastructure" in resp.text
    assert "100" in resp.text  # total emails


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_emails(app_client, pool_with_emails):
    pool_with_emails.conn.fetch_returns = [
        [make_record(id=1, from_addr="a@b.com", to_addrs="c@d.com",
                     subject="Test", date_sent=None, body_text="body", folder="INBOX")],
    ]
    resp = await app_client.get("/api/emails?recent=5")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["subject"] == "Test"


@pytest.mark.asyncio
async def test_api_email_not_found(app_client, pool_with_emails):
    pool_with_emails.conn.fetchrow_returns = [None]
    resp = await app_client.get("/api/emails/999")
    assert resp.status_code == 404

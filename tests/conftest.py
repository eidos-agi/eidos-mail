"""Shared fixtures for eidos-mail tests."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Mock DB pool that returns configurable rows
# ---------------------------------------------------------------------------

class MockConnection:
    """Mock asyncpg connection with configurable return values."""

    def __init__(self):
        self.fetchval_returns: list = [0]
        self.fetch_returns: list = [[]]
        self.fetchrow_returns: list = [None]
        self._fetchval_idx = 0
        self._fetch_idx = 0
        self._fetchrow_idx = 0
        self.executed: list[tuple[str, tuple]] = []

    async def fetchval(self, query, *args):
        val = self.fetchval_returns[min(self._fetchval_idx, len(self.fetchval_returns) - 1)]
        self._fetchval_idx += 1
        return val

    async def fetch(self, query, *args):
        val = self.fetch_returns[min(self._fetch_idx, len(self.fetch_returns) - 1)]
        self._fetch_idx += 1
        return val

    async def fetchrow(self, query, *args):
        val = self.fetchrow_returns[min(self._fetchrow_idx, len(self.fetchrow_returns) - 1)]
        self._fetchrow_idx += 1
        return val

    async def execute(self, query, *args):
        self.executed.append((query, args))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockPool:
    """Mock asyncpg pool."""

    def __init__(self):
        self.conn = MockConnection()

    def acquire(self):
        return self.conn

    def get_size(self):
        return 5

    def get_min_size(self):
        return 2

    def get_max_size(self):
        return 10

    def get_idle_size(self):
        return 3


class MockRecord(dict):
    """Dict subclass that supports attribute access like asyncpg Record."""

    def __getitem__(self, key):
        return super().__getitem__(key)


def make_record(**kwargs) -> MockRecord:
    """Create a mock asyncpg Record."""
    return MockRecord(kwargs)


@pytest.fixture
def mock_pool():
    """Provide a MockPool instance."""
    return MockPool()


@pytest.fixture
def mock_conn(mock_pool):
    """Provide the MockConnection from the pool."""
    return mock_pool.conn


@pytest.fixture
async def client(mock_pool):
    """Async test client with mocked DB and auth."""
    with patch("app.database.get_pool", new_callable=lambda: lambda: AsyncMock(return_value=mock_pool)) as mock_get_pool, \
         patch("app.database.init_pool", new_callable=AsyncMock) as mock_init, \
         patch("app.database.close_pool", new_callable=AsyncMock) as mock_close:

        # Make get_pool return our mock
        mock_get_pool.return_value = mock_pool

        from app.main import app

        # Patch get_pool at the main module level too
        with patch("app.main.get_pool", return_value=mock_pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac


@pytest.fixture
async def authed_client(mock_pool):
    """Async test client with mocked DB, auth session pre-set."""
    with patch("app.database.init_pool", new_callable=AsyncMock), \
         patch("app.database.close_pool", new_callable=AsyncMock):

        from app.main import app

        async def fake_web_auth(request=None):
            return "test@eidosagi.com"

        async def fake_api_auth(request=None):
            return "test@eidosagi.com"

        # Override auth deps
        from app.auth import require_web_auth, require_api_auth
        app.dependency_overrides[require_web_auth] = fake_web_auth
        app.dependency_overrides[require_api_auth] = fake_api_auth

        with patch("app.main.get_pool", return_value=mock_pool):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac

        app.dependency_overrides.clear()

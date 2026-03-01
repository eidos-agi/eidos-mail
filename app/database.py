"""Asyncpg connection pool and migration runner."""

import asyncpg
from pathlib import Path
from app.config import DATABASE_URL

pool: asyncpg.Pool | None = None

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


async def init_pool():
    """Create the connection pool and run migrations."""
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    await run_migrations()


async def close_pool():
    """Close the connection pool."""
    global pool
    if pool:
        await pool.close()
        pool = None


async def get_pool() -> asyncpg.Pool:
    """Get the connection pool."""
    if pool is None:
        raise RuntimeError("Database pool not initialized")
    return pool


async def run_migrations():
    """Run SQL migration files in order."""
    if pool is None:
        raise RuntimeError("Database pool not initialized")

    async with pool.acquire() as conn:
        # Create migrations tracking table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS _migrations (
                id SERIAL PRIMARY KEY,
                filename TEXT UNIQUE NOT NULL,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Get already-applied migrations
        applied = {r["filename"] for r in await conn.fetch(
            "SELECT filename FROM _migrations"
        )}

        # Run pending migrations in order
        migration_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for mf in migration_files:
            if mf.name not in applied:
                sql = mf.read_text()
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO _migrations (filename) VALUES ($1)", mf.name
                )
                print(f"Applied migration: {mf.name}")

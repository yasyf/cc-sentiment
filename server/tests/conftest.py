from __future__ import annotations

import pytest
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

from cc_sentiment_server.db import Database


@pytest.fixture(scope="session")
def timescaledb():
    container = (
        PostgresContainer("timescale/timescaledb-ha:pg17-all", driver=None)
        .with_command(
            "postgres -c fsync=off -c synchronous_commit=off -c full_page_writes=off"
        )
    )
    with container as pg:
        url = pg.get_connection_url()
        yield f"{url}?sslmode=disable" if "?" not in url else f"{url}&sslmode=disable"


@pytest_asyncio.fixture(loop_scope="session", scope="session")
async def _seeded_db(timescaledb: str):
    database = Database(timescaledb)
    await database.open()
    await database.seed()
    try:
        yield database
    finally:
        await database.close()


@pytest_asyncio.fixture(loop_scope="session")
async def db(_seeded_db: Database):
    async with _seeded_db.pool.connection() as conn:
        await conn.execute("TRUNCATE sentiment")
        await conn.execute("TRUNCATE daemon_events")
    yield _seeded_db

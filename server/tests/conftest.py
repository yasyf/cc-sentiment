from __future__ import annotations

import pytest
from testcontainers.postgres import PostgresContainer

from cc_sentiment_server.db import Database


@pytest.fixture(scope="session")
def timescaledb():
    with PostgresContainer("timescale/timescaledb:latest-pg17", driver=None) as pg:
        url = pg.get_connection_url()
        yield f"{url}?sslmode=disable" if "?" not in url else f"{url}&sslmode=disable"


@pytest.fixture
async def db(timescaledb: str):
    database = Database(timescaledb)
    await database.open()
    await database.seed()
    try:
        yield database
    finally:
        async with database.pool.connection() as conn:
            await conn.execute("DELETE FROM sentiment")
        await database.close()

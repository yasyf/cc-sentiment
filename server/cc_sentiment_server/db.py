from __future__ import annotations

from datetime import datetime, timezone

from psycopg_pool import AsyncConnectionPool

from cc_sentiment_server.models import (
    DataResponse,
    DistributionPoint,
    HourlyPoint,
    SentimentRecord,
    TimelinePoint,
    WeekdayPoint,
)

__all__ = ["Database"]

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sentiment (
    time TIMESTAMPTZ NOT NULL,
    conversation_id TEXT NOT NULL,
    bucket_index SMALLINT NOT NULL,
    github_username TEXT NOT NULL,
    sentiment_score SMALLINT NOT NULL CHECK(sentiment_score BETWEEN 1 AND 5),
    prompt_version TEXT NOT NULL,
    model_id TEXT NOT NULL,
    client_version TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(time, conversation_id, bucket_index, github_username)
)
"""

CREATE_HYPERTABLE_SQL = """
SELECT create_hypertable('sentiment', 'time',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE)
"""

CREATE_INDEXES_SQL = [
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_time_score
        ON sentiment (time DESC, sentiment_score)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_username_time
        ON sentiment (github_username, time DESC)
    """,
]

CREATE_CONTINUOUS_AGGREGATE_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS sentiment_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket('1 hour', time) AS bucket,
       AVG(sentiment_score)::float AS avg_score,
       COUNT(*)::int AS count
FROM sentiment
GROUP BY bucket
WITH NO DATA
"""

ADD_CAGG_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('sentiment_hourly',
    start_offset => INTERVAL '1 month',
    end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists => TRUE)
"""

ENABLE_COMPRESSION_SQL = """
ALTER TABLE sentiment SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'github_username',
    timescaledb.compress_orderby = 'time DESC'
)
"""

ADD_COMPRESSION_POLICY_SQL = """
SELECT add_compression_policy('sentiment', INTERVAL '30 days',
    if_not_exists => TRUE)
"""

INGEST_SQL = """
INSERT INTO sentiment (time, conversation_id, bucket_index, github_username,
                       sentiment_score, prompt_version, model_id, client_version)
VALUES (%(time)s, %(conversation_id)s, %(bucket_index)s, %(github_username)s,
        %(sentiment_score)s, %(prompt_version)s, %(model_id)s, %(client_version)s)
ON CONFLICT DO NOTHING
"""

# Query the continuous aggregate for timeline data
TIMELINE_SQL = """
SELECT bucket AS time, avg_score, count
FROM sentiment_hourly
WHERE bucket > NOW() - make_interval(days => %(days)s)
ORDER BY bucket
"""

HOURLY_SQL = """
SELECT EXTRACT(hour FROM time)::int AS hour,
       AVG(sentiment_score)::float AS avg_score,
       COUNT(*)::int AS count
FROM sentiment
GROUP BY hour
ORDER BY hour
"""

WEEKDAY_SQL = """
SELECT EXTRACT(dow FROM time)::int AS dow,
       AVG(sentiment_score)::float AS avg_score,
       COUNT(*)::int AS count
FROM sentiment
GROUP BY dow
ORDER BY dow
"""

DISTRIBUTION_SQL = """
SELECT sentiment_score AS score, COUNT(*)::int AS count
FROM sentiment
GROUP BY score
ORDER BY score
"""

TOTAL_COUNT_SQL = """
SELECT COUNT(*)::int AS total FROM sentiment
"""

LAST_UPDATED_SQL = """
SELECT MAX(ingested_at) FROM sentiment
"""


class Database:
    def __init__(self, dsn: str) -> None:
        self.pool = AsyncConnectionPool(dsn, open=False)

    async def open(self) -> None:
        await self.pool.open()

    async def close(self) -> None:
        await self.pool.close()

    async def seed(self) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(CREATE_TABLE_SQL)
            await conn.execute(CREATE_HYPERTABLE_SQL)
            for idx_sql in CREATE_INDEXES_SQL:
                await conn.execute(idx_sql)
            await conn.execute(CREATE_CONTINUOUS_AGGREGATE_SQL)
            await conn.execute(ADD_CAGG_POLICY_SQL)
            await conn.execute(ENABLE_COMPRESSION_SQL)
            await conn.execute(ADD_COMPRESSION_POLICY_SQL)

    async def ingest(self, records: list[SentimentRecord], github_username: str) -> None:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    INGEST_SQL,
                    [
                        {
                            "time": r.time,
                            "conversation_id": r.conversation_id,
                            "bucket_index": r.bucket_index,
                            "github_username": github_username,
                            "sentiment_score": r.sentiment_score,
                            "prompt_version": r.prompt_version,
                            "model_id": r.model_id,
                            "client_version": r.client_version,
                        }
                        for r in records
                    ],
                )

    async def query_all(self, days: int = 7) -> DataResponse:
        async with self.pool.connection() as conn:
            async with conn.pipeline():
                timeline_cur = await conn.execute(TIMELINE_SQL, {"days": days})
                hourly_cur = await conn.execute(HOURLY_SQL)
                weekday_cur = await conn.execute(WEEKDAY_SQL)
                distribution_cur = await conn.execute(DISTRIBUTION_SQL)
                total_cur = await conn.execute(TOTAL_COUNT_SQL)
                last_updated_cur = await conn.execute(LAST_UPDATED_SQL)

            timeline = [
                TimelinePoint(time=row[0], avg_score=row[1], count=row[2])
                for row in await timeline_cur.fetchall()
            ]
            hourly = [
                HourlyPoint(hour=row[0], avg_score=row[1], count=row[2])
                for row in await hourly_cur.fetchall()
            ]
            weekday = [
                WeekdayPoint(dow=row[0], avg_score=row[1], count=row[2])
                for row in await weekday_cur.fetchall()
            ]
            distribution = [
                DistributionPoint(score=row[0], count=row[1])
                for row in await distribution_cur.fetchall()
            ]
            total = (await total_cur.fetchone())[0]
            last_updated_row = await last_updated_cur.fetchone()

        last_updated = last_updated_row[0] if last_updated_row[0] else datetime.now(timezone.utc)

        return DataResponse(
            timeline=timeline,
            hourly=hourly,
            weekday=weekday,
            distribution=distribution,
            total_records=total,
            last_updated=last_updated,
        )

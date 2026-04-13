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
    contributor_type TEXT NOT NULL CHECK(contributor_type IN ('github', 'gpg')),
    contributor_id TEXT NOT NULL,
    sentiment_score SMALLINT NOT NULL CHECK(sentiment_score BETWEEN 1 AND 5),
    prompt_version TEXT NOT NULL,
    model_id TEXT NOT NULL,
    client_version TEXT NOT NULL,
    ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(time, conversation_id, bucket_index, contributor_id)
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
    CREATE INDEX IF NOT EXISTS idx_sentiment_contributor_time
        ON sentiment (contributor_id, time DESC)
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
    timescaledb.compress_segmentby = 'contributor_id',
    timescaledb.compress_orderby = 'time DESC'
)
"""

ADD_COMPRESSION_POLICY_SQL = """
SELECT add_compression_policy('sentiment', INTERVAL '30 days',
    if_not_exists => TRUE)
"""

SEED_STATEMENTS = [
    CREATE_TABLE_SQL,
    CREATE_HYPERTABLE_SQL,
    *CREATE_INDEXES_SQL,
    CREATE_CONTINUOUS_AGGREGATE_SQL,
    ADD_CAGG_POLICY_SQL,
    ENABLE_COMPRESSION_SQL,
    ADD_COMPRESSION_POLICY_SQL,
]

MIGRATE_SQL = [
    "ALTER TABLE sentiment ADD COLUMN IF NOT EXISTS contributor_type TEXT NOT NULL DEFAULT 'github'",
    "ALTER TABLE sentiment RENAME COLUMN github_username TO contributor_id",
    "UPDATE sentiment SET contributor_type = 'gpg' WHERE contributor_id ~ '^[0-9A-Fa-f]{16,40}$'",
    "ALTER TABLE sentiment ALTER COLUMN contributor_type DROP DEFAULT",
    "DROP INDEX IF EXISTS idx_sentiment_username_time",
    "CREATE INDEX IF NOT EXISTS idx_sentiment_contributor_time ON sentiment (contributor_id, time DESC)",
]

INGEST_SQL = """
INSERT INTO sentiment (time, conversation_id, bucket_index, contributor_type, contributor_id,
                       sentiment_score, prompt_version, model_id, client_version)
VALUES (%(time)s, %(conversation_id)s, %(bucket_index)s, %(contributor_type)s, %(contributor_id)s,
        %(sentiment_score)s, %(prompt_version)s, %(model_id)s, %(client_version)s)
ON CONFLICT DO NOTHING
"""

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

TOTAL_SESSIONS_SQL = """
SELECT COUNT(DISTINCT conversation_id)::int FROM sentiment
"""

TOTAL_CONTRIBUTORS_SQL = """
SELECT COUNT(DISTINCT contributor_id)::int FROM sentiment
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
            for sql in SEED_STATEMENTS:
                await conn.execute(sql)

    async def migrate(self) -> None:
        async with self.pool.connection() as conn:
            for sql in MIGRATE_SQL:
                await conn.execute(sql)

    async def ingest(self, records: list[SentimentRecord], contributor_id: str, contributor_type: str) -> None:
        async with self.pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.executemany(
                    INGEST_SQL,
                    [
                        {
                            "time": r.time,
                            "conversation_id": r.conversation_id,
                            "bucket_index": r.bucket_index,
                            "contributor_type": contributor_type,
                            "contributor_id": contributor_id,
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
            timeline = [
                TimelinePoint(time=row[0], avg_score=row[1], count=row[2])
                for row in await (await conn.execute(TIMELINE_SQL, {"days": days})).fetchall()
            ]
            hourly = [
                HourlyPoint(hour=row[0], avg_score=row[1], count=row[2])
                for row in await (await conn.execute(HOURLY_SQL)).fetchall()
            ]
            weekday = [
                WeekdayPoint(dow=row[0], avg_score=row[1], count=row[2])
                for row in await (await conn.execute(WEEKDAY_SQL)).fetchall()
            ]
            distribution = [
                DistributionPoint(score=row[0], count=row[1])
                for row in await (await conn.execute(DISTRIBUTION_SQL)).fetchall()
            ]
            total = (await (await conn.execute(TOTAL_COUNT_SQL)).fetchone())[0]
            total_sessions = (await (await conn.execute(TOTAL_SESSIONS_SQL)).fetchone())[0]
            total_contributors = (await (await conn.execute(TOTAL_CONTRIBUTORS_SQL)).fetchone())[0]
            last_updated = (await (await conn.execute(LAST_UPDATED_SQL)).fetchone())[0]

        return DataResponse(
            timeline=timeline,
            hourly=hourly,
            weekday=weekday,
            distribution=distribution,
            total_records=total,
            total_sessions=total_sessions,
            total_contributors=total_contributors,
            last_updated=last_updated or datetime.now(timezone.utc),
        )

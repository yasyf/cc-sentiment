from __future__ import annotations

from datetime import datetime, timezone

from psycopg_pool import AsyncConnectionPool

from cc_sentiment_server.models import (
    DataResponse,
    DistributionPoint,
    HourlyPoint,
    ModelBreakdown,
    SentimentRecord,
    TimelinePoint,
    TrendComparison,
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
    read_edit_ratio FLOAT,
    turn_count SMALLINT NOT NULL,
    thinking_present BOOLEAN NOT NULL,
    thinking_chars INT NOT NULL,
    cc_version TEXT NOT NULL,
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
       COUNT(*)::int AS count,
       AVG(read_edit_ratio)::float AS avg_read_edit_ratio
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


INGEST_SQL = """
INSERT INTO sentiment (time, conversation_id, bucket_index, contributor_type, contributor_id,
                       sentiment_score, prompt_version, model_id, client_version,
                       read_edit_ratio, turn_count, thinking_present, thinking_chars, cc_version)
VALUES (%(time)s, %(conversation_id)s, %(bucket_index)s, %(contributor_type)s, %(contributor_id)s,
        %(sentiment_score)s, %(prompt_version)s, %(model_id)s, %(client_version)s,
        %(read_edit_ratio)s, %(turn_count)s, %(thinking_present)s, %(thinking_chars)s, %(cc_version)s)
ON CONFLICT DO NOTHING
"""

TIMELINE_SQL = """
SELECT bucket AS time, avg_score, count, avg_read_edit_ratio
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

TREND_CURRENT_SQL = """
SELECT AVG(sentiment_score)::float, COUNT(*)::int, AVG(read_edit_ratio)::float
FROM sentiment
WHERE time > NOW() - make_interval(days => %(days)s)
"""

TREND_PREVIOUS_SQL = """
SELECT AVG(sentiment_score)::float, COUNT(*)::int, AVG(read_edit_ratio)::float
FROM sentiment
WHERE time BETWEEN NOW() - make_interval(days => %(days_double)s)
  AND NOW() - make_interval(days => %(days)s)
"""

MODEL_BREAKDOWN_SQL = """
SELECT model_id, AVG(sentiment_score)::float AS avg_score,
       COUNT(*)::int AS count, AVG(read_edit_ratio)::float AS avg_read_edit_ratio
FROM sentiment
WHERE time > NOW() - make_interval(days => %(days)s)
GROUP BY model_id
ORDER BY count DESC
"""

AVG_READ_EDIT_SQL = """
SELECT AVG(read_edit_ratio)::float
FROM sentiment
WHERE time > NOW() - make_interval(days => %(days)s)
  AND read_edit_ratio IS NOT NULL
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
                            "read_edit_ratio": r.read_edit_ratio,
                            "turn_count": r.turn_count,
                            "thinking_present": r.thinking_present,
                            "thinking_chars": r.thinking_chars,
                            "cc_version": r.cc_version,
                        }
                        for r in records
                    ],
                )

    async def query_all(self, days: int = 7) -> DataResponse:
        async with self.pool.connection() as conn:
            timeline = [
                TimelinePoint(time=row[0], avg_score=row[1], count=row[2], avg_read_edit_ratio=row[3])
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

            trend_current = await (await conn.execute(TREND_CURRENT_SQL, {"days": days})).fetchone()
            trend_previous = await (await conn.execute(TREND_PREVIOUS_SQL, {"days": days, "days_double": days * 2})).fetchone()

            model_rows = await (await conn.execute(MODEL_BREAKDOWN_SQL, {"days": days})).fetchall()
            model_breakdown = [
                ModelBreakdown(model_id=row[0], avg_score=row[1], count=row[2], avg_read_edit_ratio=row[3])
                for row in model_rows
            ]

            avg_re = (await (await conn.execute(AVG_READ_EDIT_SQL, {"days": days})).fetchone())[0]

        trend = TrendComparison(
            sentiment_current=trend_current[0] or 0.0,
            sentiment_previous=trend_previous[0] or 0.0,
            sessions_current=trend_current[1] or 0,
            sessions_previous=trend_previous[1] or 0,
            read_edit_current=trend_current[2],
            read_edit_previous=trend_previous[2],
        )

        return DataResponse(
            timeline=timeline,
            hourly=hourly,
            weekday=weekday,
            distribution=distribution,
            total_records=total,
            total_sessions=total_sessions,
            total_contributors=total_contributors,
            last_updated=last_updated or datetime.now(timezone.utc),
            trend=trend,
            model_breakdown=model_breakdown,
            avg_read_edit_ratio=avg_re,
        )

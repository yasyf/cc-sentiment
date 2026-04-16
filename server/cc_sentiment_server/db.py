from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from psycopg_pool import AsyncConnectionPool

from cc_sentiment_server.models import (
    DataResponse,
    DistributionPoint,
    ModelBreakdown,
    SentimentRecord,
    TimelinePoint,
    TrendComparison,
)

__all__ = ["Database", "WindowStats", "TrendsStats", "LifetimeStats"]


@dataclass(frozen=True, slots=True)
class WindowStats:
    distribution: list[DistributionPoint]
    trend: TrendComparison


@dataclass(frozen=True, slots=True)
class TrendsStats:
    timeline: list[TimelinePoint]
    model_breakdown: list[ModelBreakdown]
    avg_read_edit_ratio: float | None
    avg_edits_without_prior_read_ratio: float | None
    avg_tool_calls_per_turn: float | None
    avg_write_edit_ratio: float | None
    avg_subagent_count: float | None


@dataclass(frozen=True, slots=True)
class LifetimeStats:
    total_records: int
    total_sessions: int
    total_contributors: int
    last_updated: datetime

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sentiment (
    time TIMESTAMPTZ NOT NULL,
    conversation_id TEXT NOT NULL,
    bucket_index SMALLINT NOT NULL,
    contributor_type TEXT NOT NULL CHECK(contributor_type IN ('github', 'gpg')),
    contributor_id TEXT NOT NULL,
    sentiment_score SMALLINT NOT NULL CHECK(sentiment_score BETWEEN 1 AND 5),
    prompt_version TEXT NOT NULL,
    claude_model TEXT NOT NULL,
    client_version TEXT NOT NULL,
    read_edit_ratio FLOAT,
    edits_without_prior_read_ratio FLOAT,
    write_edit_ratio FLOAT,
    tool_calls_per_turn FLOAT NOT NULL,
    subagent_count SMALLINT NOT NULL,
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
SELECT time_bucket(INTERVAL '1 hour', time, 'UTC') AS bucket,
       AVG(sentiment_score)::float AS avg_score,
       COUNT(*)::int AS count,
       AVG(read_edit_ratio)::float AS avg_read_edit_ratio,
       AVG(edits_without_prior_read_ratio)::float AS avg_edits_without_prior_read_ratio,
       AVG(tool_calls_per_turn)::float AS avg_tool_calls_per_turn
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
                       sentiment_score, prompt_version, claude_model, client_version,
                       read_edit_ratio, edits_without_prior_read_ratio, write_edit_ratio,
                       tool_calls_per_turn, subagent_count,
                       turn_count, thinking_present, thinking_chars, cc_version)
VALUES (%(time)s, %(conversation_id)s, %(bucket_index)s, %(contributor_type)s, %(contributor_id)s,
        %(sentiment_score)s, %(prompt_version)s, %(claude_model)s, %(client_version)s,
        %(read_edit_ratio)s, %(edits_without_prior_read_ratio)s, %(write_edit_ratio)s,
        %(tool_calls_per_turn)s, %(subagent_count)s,
        %(turn_count)s, %(thinking_present)s, %(thinking_chars)s, %(cc_version)s)
ON CONFLICT DO NOTHING
"""

TIMELINE_SQL = """
SELECT bucket AS time, avg_score, count, avg_read_edit_ratio,
       avg_edits_without_prior_read_ratio, avg_tool_calls_per_turn
FROM sentiment_hourly
WHERE bucket > NOW() - make_interval(days => %(days)s)
ORDER BY bucket
"""

DISTRIBUTION_SQL = """
SELECT sentiment_score AS score, COUNT(*)::int AS count
FROM sentiment
WHERE time > NOW() - make_interval(days => %(days)s)
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
SELECT claude_model, AVG(sentiment_score)::float AS avg_score,
       COUNT(*)::int AS count,
       AVG(read_edit_ratio)::float AS avg_read_edit_ratio,
       AVG(write_edit_ratio)::float AS avg_write_edit_ratio,
       AVG(subagent_count)::float AS avg_subagent_count
FROM sentiment
WHERE time > NOW() - make_interval(days => %(days)s)
GROUP BY claude_model
ORDER BY count DESC
"""

SUMMARY_AVERAGES_SQL = """
SELECT AVG(read_edit_ratio)::float AS avg_read_edit_ratio,
       AVG(edits_without_prior_read_ratio)::float AS avg_edits_without_prior_read_ratio,
       AVG(tool_calls_per_turn)::float AS avg_tool_calls_per_turn,
       AVG(write_edit_ratio)::float AS avg_write_edit_ratio,
       AVG(subagent_count)::float AS avg_subagent_count
FROM sentiment
WHERE time > NOW() - make_interval(days => %(days)s)
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
                            "claude_model": r.claude_model,
                            "client_version": r.client_version,
                            "read_edit_ratio": r.read_edit_ratio,
                            "edits_without_prior_read_ratio": r.edits_without_prior_read_ratio,
                            "write_edit_ratio": r.write_edit_ratio,
                            "tool_calls_per_turn": r.tool_calls_per_turn,
                            "subagent_count": r.subagent_count,
                            "turn_count": r.turn_count,
                            "thinking_present": r.thinking_present,
                            "thinking_chars": r.thinking_chars,
                            "cc_version": r.cc_version,
                        }
                        for r in records
                    ],
                )

    async def query_window(self, days: int) -> WindowStats:
        async with self.pool.connection() as conn:
            distribution = [
                DistributionPoint(score=row[0], count=row[1])
                for row in await (await conn.execute(DISTRIBUTION_SQL, {"days": days})).fetchall()
            ]
            trend_current = await (await conn.execute(TREND_CURRENT_SQL, {"days": days})).fetchone()
            trend_previous = await (await conn.execute(
                TREND_PREVIOUS_SQL, {"days": days, "days_double": days * 2}
            )).fetchone()

        return WindowStats(
            distribution=distribution,
            trend=TrendComparison(
                sentiment_current=trend_current[0] or 0.0,
                sentiment_previous=trend_previous[0] or 0.0,
                sessions_current=trend_current[1] or 0,
                sessions_previous=trend_previous[1] or 0,
                read_edit_current=trend_current[2],
                read_edit_previous=trend_previous[2],
            ),
        )

    async def query_trends(self, days: int = 30) -> TrendsStats:
        async with self.pool.connection() as conn:
            timeline = [
                TimelinePoint(
                    time=row[0],
                    avg_score=row[1],
                    count=row[2],
                    avg_read_edit_ratio=row[3],
                    avg_edits_without_prior_read_ratio=row[4],
                    avg_tool_calls_per_turn=row[5],
                )
                for row in await (await conn.execute(TIMELINE_SQL, {"days": days})).fetchall()
            ]
            model_breakdown = [
                ModelBreakdown(
                    claude_model=row[0],
                    avg_score=row[1],
                    count=row[2],
                    avg_read_edit_ratio=row[3],
                    avg_write_edit_ratio=row[4],
                    avg_subagent_count=row[5],
                )
                for row in await (await conn.execute(MODEL_BREAKDOWN_SQL, {"days": days})).fetchall()
            ]
            summary = await (await conn.execute(SUMMARY_AVERAGES_SQL, {"days": days})).fetchone()

        return TrendsStats(
            timeline=timeline,
            model_breakdown=model_breakdown,
            avg_read_edit_ratio=summary[0],
            avg_edits_without_prior_read_ratio=summary[1],
            avg_tool_calls_per_turn=summary[2],
            avg_write_edit_ratio=summary[3],
            avg_subagent_count=summary[4],
        )

    async def query_lifetime(self) -> LifetimeStats:
        async with self.pool.connection() as conn:
            total = (await (await conn.execute(TOTAL_COUNT_SQL)).fetchone())[0]
            total_sessions = (await (await conn.execute(TOTAL_SESSIONS_SQL)).fetchone())[0]
            total_contributors = (await (await conn.execute(TOTAL_CONTRIBUTORS_SQL)).fetchone())[0]
            last_updated = (await (await conn.execute(LAST_UPDATED_SQL)).fetchone())[0]

        return LifetimeStats(
            total_records=total,
            total_sessions=total_sessions,
            total_contributors=total_contributors,
            last_updated=last_updated or datetime.now(timezone.utc),
        )

    async def query_all(self, days: int) -> DataResponse:
        window = await self.query_window(days)
        trends = await self.query_trends()
        lifetime = await self.query_lifetime()
        return DataResponse(
            timeline=trends.timeline,
            distribution=window.distribution,
            total_records=lifetime.total_records,
            total_sessions=lifetime.total_sessions,
            total_contributors=lifetime.total_contributors,
            last_updated=lifetime.last_updated,
            trend=window.trend,
            model_breakdown=trends.model_breakdown,
            avg_read_edit_ratio=trends.avg_read_edit_ratio,
            avg_edits_without_prior_read_ratio=trends.avg_edits_without_prior_read_ratio,
            avg_tool_calls_per_turn=trends.avg_tool_calls_per_turn,
            avg_write_edit_ratio=trends.avg_write_edit_ratio,
            avg_subagent_count=trends.avg_subagent_count,
        )

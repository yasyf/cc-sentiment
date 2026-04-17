from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import ClassVar

from psycopg_pool import AsyncConnectionPool

from cc_sentiment_server.models import (
    DataResponse,
    DistributionPoint,
    ModelBreakdown,
    MyStatResponse,
    SentimentRecord,
    TimelinePoint,
    TrendComparison,
)

__all__ = ["Database", "WindowStats", "TrendsStats", "LifetimeStats", "StatCandidate"]


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
    contributor_type TEXT NOT NULL CHECK(contributor_type IN ('github', 'gpg', 'gist')),
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

CREATE_TOOLKIT_EXTENSION_SQL = """
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit
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
    """
    CREATE INDEX IF NOT EXISTS idx_sentiment_ingested_at
        ON sentiment (ingested_at DESC)
    """,
]

CREATE_CONTINUOUS_AGGREGATE_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS sentiment_hourly
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
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

CREATE_TOTALS_CAGG_SQL = """
CREATE MATERIALIZED VIEW IF NOT EXISTS sentiment_totals_daily
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT time_bucket(INTERVAL '1 day', time, 'UTC') AS day,
       COUNT(*)::bigint AS total,
       hyperloglog(4096, conversation_id) AS hll_sessions,
       hyperloglog(4096, contributor_id) AS hll_contributors
FROM sentiment
GROUP BY day
WITH NO DATA
"""

ADD_TOTALS_CAGG_POLICY_SQL = """
SELECT add_continuous_aggregate_policy('sentiment_totals_daily',
    start_offset => INTERVAL '30 days',
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
    CREATE_TOOLKIT_EXTENSION_SQL,
    CREATE_TABLE_SQL,
    CREATE_HYPERTABLE_SQL,
    *CREATE_INDEXES_SQL,
    CREATE_CONTINUOUS_AGGREGATE_SQL,
    ADD_CAGG_POLICY_SQL,
    CREATE_TOTALS_CAGG_SQL,
    ADD_TOTALS_CAGG_POLICY_SQL,
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

LIFETIME_STATS_SQL = """
SELECT COALESCE(SUM(total)::bigint, 0) AS total,
       COALESCE(distinct_count(rollup(hll_sessions))::int, 0) AS total_sessions,
       COALESCE(distinct_count(rollup(hll_contributors))::int, 0) AS total_contributors
FROM sentiment_totals_daily
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


@dataclass(frozen=True, slots=True)
class StatCandidate:
    QUERY_TEMPLATE: ClassVar[str] = """
    WITH per_user AS (
        SELECT contributor_id, ({metric}) AS val
        FROM sentiment
        GROUP BY contributor_id
        HAVING ({metric}) IS NOT NULL
    ),
    ranked AS (
        SELECT contributor_id, val, PERCENT_RANK() OVER (ORDER BY val) AS pr
        FROM per_user
    )
    SELECT
        (SELECT pr FROM ranked WHERE contributor_id = %(contributor_id)s) AS pr,
        (SELECT COUNT(*)::int FROM per_user) AS total
    """

    kind: str
    metric_sql: str
    high_text: str
    low_text: str

    @property
    def query(self) -> str:
        return self.QUERY_TEMPLATE.format(metric=self.metric_sql)

    def build(self, pr: float, total: int) -> MyStatResponse:
        high_pct = round(pr * 100)
        percentile = high_pct if high_pct >= 50 else 100 - high_pct
        template = self.high_text if high_pct >= 50 else self.low_text
        text = template.format(p=percentile)
        return MyStatResponse(
            kind=self.kind,
            percentile=percentile,
            text=text,
            tweet_text=f"I'm {text}.",
            total_contributors=total,
        )


class Database:
    STAT_CANDIDATES: ClassVar[tuple[StatCandidate, ...]] = (
        StatCandidate(
            kind="kindness",
            metric_sql="AVG(sentiment_score::float)",
            high_text="nicer to Claude than {p}% of developers",
            low_text="tougher on Claude than {p}% of developers",
        ),
        StatCandidate(
            kind="thinking",
            metric_sql="AVG(thinking_chars::float)",
            high_text="getting Claude to think harder than {p}% of developers",
            low_text="getting straight answers from Claude more than {p}% of developers",
        ),
        StatCandidate(
            kind="tool_calls",
            metric_sql="AVG(tool_calls_per_turn)",
            high_text="keeping Claude busier per turn than {p}% of developers",
            low_text="running leaner turns than {p}% of developers",
        ),
        StatCandidate(
            kind="turn_length",
            metric_sql="AVG(turn_count::float)",
            high_text="running longer sessions than {p}% of developers",
            low_text="running snappier sessions than {p}% of developers",
        ),
        StatCandidate(
            kind="read_before_edit",
            metric_sql="AVG(read_edit_ratio)",
            high_text="reading before editing more than {p}% of developers",
            low_text="editing bolder than {p}% of developers",
        ),
        StatCandidate(
            kind="subagents",
            metric_sql="AVG(subagent_count::float)",
            high_text="delegating to subagents more than {p}% of developers",
            low_text="keeping it single-threaded more than {p}% of developers",
        ),
        StatCandidate(
            kind="volume",
            metric_sql="COUNT(DISTINCT conversation_id)::float",
            high_text="running more Claude Code sessions than {p}% of developers",
            low_text="running more focused sessions than {p}% of developers",
        ),
    )

    def __init__(self, dsn: str) -> None:
        self.pool = AsyncConnectionPool(dsn, open=False, min_size=4, max_size=16)

    async def open(self) -> None:
        await self.pool.open()

    async def close(self) -> None:
        await self.pool.close()

    async def seed(self) -> None:
        async with self.pool.connection() as conn:
            for sql in SEED_STATEMENTS:
                await conn.execute(sql)

    async def _fetch_all(self, sql: str, params: dict | None = None) -> list[tuple]:
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params or {})).fetchall()

    async def _fetch_one(self, sql: str, params: dict | None = None) -> tuple:
        async with self.pool.connection() as conn:
            return await (await conn.execute(sql, params or {})).fetchone()

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
        distribution_rows, trend_current, trend_previous = await asyncio.gather(
            self._fetch_all(DISTRIBUTION_SQL, {"days": days}),
            self._fetch_one(TREND_CURRENT_SQL, {"days": days}),
            self._fetch_one(TREND_PREVIOUS_SQL, {"days": days, "days_double": days * 2}),
        )
        return WindowStats(
            distribution=[
                DistributionPoint(score=row[0], count=row[1])
                for row in distribution_rows
            ],
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
        timeline_rows, model_rows, summary = await asyncio.gather(
            self._fetch_all(TIMELINE_SQL, {"days": days}),
            self._fetch_all(MODEL_BREAKDOWN_SQL, {"days": days}),
            self._fetch_one(SUMMARY_AVERAGES_SQL, {"days": days}),
        )
        return TrendsStats(
            timeline=[
                TimelinePoint(
                    time=row[0],
                    avg_score=row[1],
                    count=row[2],
                    avg_read_edit_ratio=row[3],
                    avg_edits_without_prior_read_ratio=row[4],
                    avg_tool_calls_per_turn=row[5],
                )
                for row in timeline_rows
            ],
            model_breakdown=[
                ModelBreakdown(
                    claude_model=row[0],
                    avg_score=row[1],
                    count=row[2],
                    avg_read_edit_ratio=row[3],
                    avg_write_edit_ratio=row[4],
                    avg_subagent_count=row[5],
                )
                for row in model_rows
            ],
            avg_read_edit_ratio=summary[0],
            avg_edits_without_prior_read_ratio=summary[1],
            avg_tool_calls_per_turn=summary[2],
            avg_write_edit_ratio=summary[3],
            avg_subagent_count=summary[4],
        )

    async def query_lifetime(self) -> LifetimeStats:
        lifetime_row, last_updated_row = await asyncio.gather(
            self._fetch_one(LIFETIME_STATS_SQL),
            self._fetch_one(LAST_UPDATED_SQL),
        )
        return LifetimeStats(
            total_records=lifetime_row[0],
            total_sessions=lifetime_row[1],
            total_contributors=lifetime_row[2],
            last_updated=last_updated_row[0] or datetime.now(timezone.utc),
        )

    async def _score_candidate(
        self, candidate: StatCandidate, contributor_id: str
    ) -> tuple[StatCandidate, float, int] | None:
        match await self._fetch_one(candidate.query, {"contributor_id": contributor_id}):
            case (float(pr), int(total)) if total >= 2:
                return (candidate, pr, total)
            case _:
                return None

    async def query_my_stat(self, contributor_id: str) -> MyStatResponse | None:
        scored = await asyncio.gather(
            *(self._score_candidate(c, contributor_id) for c in self.STAT_CANDIDATES)
        )
        match max(filter(None, scored), key=lambda r: abs(r[1] - 0.5), default=None):
            case None:
                return None
            case (candidate, pr, total):
                return candidate.build(pr, total)

    async def query_all(self, days: int) -> DataResponse:
        window, trends, lifetime = await asyncio.gather(
            self.query_window(days),
            self.query_trends(),
            self.query_lifetime(),
        )
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

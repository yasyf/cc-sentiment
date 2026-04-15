from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cc_sentiment_server.db import Database, INGEST_SQL
from cc_sentiment_server.models import DataResponse, SentimentRecord


def make_record(
    score: int = 4,
    bucket: int = 0,
    conv_id: str = "abc-123",
    t: datetime | None = None,
    claude_model: str = "claude-sonnet-4-20250514",
) -> SentimentRecord:
    return SentimentRecord(
        time=t or datetime(2026, 4, 12, 10, 30, tzinfo=timezone.utc),
        conversation_id=conv_id,
        bucket_index=bucket,
        sentiment_score=score,
        prompt_version="v1",
        claude_model=claude_model,
        client_version="0.1.0",
        read_edit_ratio=None,
        edits_without_prior_read_ratio=None,
        write_edit_ratio=None,
        tool_calls_per_turn=0.0,
        subagent_count=0,
        turn_count=1,
        thinking_present=False,
        thinking_chars=0,
        cc_version="2.1.92",
    )


class TestSeed:
    @pytest.mark.asyncio
    async def test_creates_hypertable(self, db: Database) -> None:
        async with db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name = 'sentiment'"
            )).fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_creates_indexes(self, db: Database) -> None:
        async with db.pool.connection() as conn:
            rows = await (await conn.execute(
                "SELECT indexname FROM pg_indexes WHERE tablename = 'sentiment' AND indexname LIKE 'idx_%'"
            )).fetchall()
        index_names = {r[0] for r in rows}
        assert "idx_sentiment_time_score" in index_names
        assert "idx_sentiment_contributor_time" in index_names

    @pytest.mark.asyncio
    async def test_creates_continuous_aggregate(self, db: Database) -> None:
        async with db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT count(*) FROM timescaledb_information.continuous_aggregates WHERE view_name = 'sentiment_hourly'"
            )).fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_seed_is_idempotent(self, db: Database) -> None:
        await db.seed()
        await db.seed()


class TestIngest:
    @pytest.mark.asyncio
    async def test_inserts_records(self, db: Database) -> None:
        records = [make_record(score=4), make_record(score=2, bucket=1)]

        await db.ingest(records, "octocat", "github")

        async with db.pool.connection() as conn:
            row = await (await conn.execute("SELECT count(*) FROM sentiment")).fetchone()
        assert row[0] == 2

    @pytest.mark.asyncio
    async def test_stores_correct_values(self, db: Database) -> None:
        records = [make_record(score=5, conv_id="test-conv")]

        await db.ingest(records, "testuser", "github")

        async with db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT sentiment_score, contributor_id, conversation_id FROM sentiment"
            )).fetchone()
        assert row == (5, "testuser", "test-conv")

    @pytest.mark.asyncio
    async def test_duplicates_ignored(self, db: Database) -> None:
        records = [make_record()]

        await db.ingest(records, "octocat", "github")
        await db.ingest(records, "octocat", "github")

        async with db.pool.connection() as conn:
            row = await (await conn.execute("SELECT count(*) FROM sentiment")).fetchone()
        assert row[0] == 1

    def test_sql_has_on_conflict(self) -> None:
        assert "ON CONFLICT DO NOTHING" in INGEST_SQL


class TestQueryAll:
    @pytest.mark.asyncio
    async def test_empty_db(self, db: Database) -> None:
        result = await db.query_all(days=7)

        assert isinstance(result, DataResponse)
        assert result.timeline == []
        assert result.hourly == []
        assert result.weekday == []
        assert result.distribution == []
        assert result.total_records == 0
        assert result.total_sessions == 0
        assert result.total_contributors == 0
        assert result.last_updated is not None

    @pytest.mark.asyncio
    async def test_returns_populated_response(self, db: Database) -> None:
        now = datetime.now(timezone.utc)
        records = [
            make_record(score=4, bucket=0, t=now),
            make_record(score=2, bucket=1, t=now),
        ]
        await db.ingest(records, "octocat", "github")

        result = await db.query_all(days=7)

        assert result.total_records == 2
        assert result.total_sessions == 1
        assert result.total_contributors == 1
        assert len(result.distribution) >= 1
        assert len(result.hourly) >= 1
        assert len(result.weekday) >= 1

    @pytest.mark.asyncio
    async def test_distribution_counts(self, db: Database) -> None:
        now = datetime.now(timezone.utc)
        records = [
            make_record(score=4, bucket=0, t=now),
            make_record(score=4, bucket=1, t=now),
            make_record(score=1, bucket=2, t=now),
        ]
        await db.ingest(records, "octocat", "github")

        result = await db.query_all(days=7)

        dist = {d.score: d.count for d in result.distribution}
        assert dist[4] == 2
        assert dist[1] == 1

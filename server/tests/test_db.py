from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from cc_sentiment_server.db import Database, INGEST_SQL
from cc_sentiment_server.models import AdminSubmission, DataResponse, MyStatResponse, SentimentRecord


def make_record(
    score: int = 4,
    bucket: int = 0,
    conv_id: str = "abc-123",
    t: datetime | None = None,
    claude_model: str = "claude-sonnet-4-20250514",
    tool_calls_per_turn: float = 0.0,
    turn_count: int = 1,
    thinking_chars: int = 0,
    subagent_count: int = 0,
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
        tool_calls_per_turn=tool_calls_per_turn,
        subagent_count=subagent_count,
        turn_count=turn_count,
        thinking_present=thinking_chars > 0,
        thinking_chars=thinking_chars,
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
        assert result.distribution == []
        assert result.total_records == 0
        assert result.total_sessions == 0
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
        assert len(result.distribution) >= 1

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


class TestQueryMyStat:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_data(self, db: Database) -> None:
        assert await db.query_my_stat("nobody") is None

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown_contributor(self, db: Database) -> None:
        await db.ingest([make_record(score=4)], "octocat", "github")
        await db.ingest([make_record(score=3, conv_id="x")], "otheruser", "github")
        assert await db.query_my_stat("ghost") is None

    @pytest.mark.asyncio
    async def test_returns_my_stat_response(self, db: Database) -> None:
        await db.ingest([make_record(score=5, conv_id="a")], "topuser", "github")
        await db.ingest([make_record(score=3, conv_id="b")], "middleuser", "github")
        await db.ingest([make_record(score=1, conv_id="c")], "lowuser", "github")

        result = await db.query_my_stat("topuser")

        assert isinstance(result, MyStatResponse)
        assert result.kind in {
            "kindness", "thinking", "tool_calls", "turn_length",
            "read_before_edit", "subagents", "volume",
        }
        assert result.text
        assert result.tweet_text.endswith("Check out yours at")
        assert 0 <= result.percentile <= 100

    @pytest.mark.asyncio
    async def test_high_kindness_framed_as_nicer(self, db: Database) -> None:
        await db.ingest([make_record(score=5, conv_id="a")], "topuser", "github")
        await db.ingest([make_record(score=1, conv_id="b")], "lowuser", "github")
        await db.ingest([make_record(score=1, conv_id="c")], "lowuser2", "github")

        result = await db.query_my_stat("topuser")

        assert result is not None
        assert result.kind == "kindness"
        assert "nicer to Claude" in result.text
        assert result.percentile >= 50

    @pytest.mark.asyncio
    async def test_low_kindness_framed_as_tougher(self, db: Database) -> None:
        await db.ingest([make_record(score=5, conv_id="a")], "topuser", "github")
        await db.ingest([make_record(score=5, conv_id="b")], "topuser2", "github")
        await db.ingest([make_record(score=1, conv_id="c")], "meanuser", "github")

        result = await db.query_my_stat("meanuser")

        assert result is not None
        assert result.kind == "kindness"
        assert "tougher on Claude" in result.text
        assert result.percentile >= 50

    @pytest.mark.asyncio
    async def test_picks_most_distinctive_stat(self, db: Database) -> None:
        await db.ingest(
            [make_record(score=3, conv_id="a", thinking_chars=10_000)],
            "thinker", "github",
        )
        await db.ingest(
            [make_record(score=3, conv_id="b", thinking_chars=0)],
            "others", "github",
        )
        await db.ingest(
            [make_record(score=3, conv_id="c", thinking_chars=0)],
            "others2", "github",
        )

        result = await db.query_my_stat("thinker")

        assert result is not None
        assert result.percentile >= 50

    @pytest.mark.asyncio
    async def test_solo_contributor_returns_self_stat(self, db: Database) -> None:
        recent = datetime.now(timezone.utc) - timedelta(days=1)
        await db.ingest(
            [make_record(score=2, conv_id=f"c-{i}", bucket=i, t=recent) for i in range(35)],
            "onlyuser",
            "github",
        )

        result = await db.query_my_stat("onlyuser")

        assert result is not None
        assert result.kind == "angriest_hour"
        assert result.text.startswith("angriest at Claude around ")
        assert result.tweet_text.startswith("My Claude rage peaks around ")
        assert result.tweet_text.endswith("Check out yours at")
        assert result.percentile == 0

    @pytest.mark.asyncio
    async def test_solo_contributor_below_threshold_returns_none(
        self, db: Database
    ) -> None:
        await db.ingest(
            [
                make_record(score=4, conv_id="a"),
                make_record(score=3, conv_id="b", bucket=1),
            ],
            "onlyuser",
            "github",
        )

        assert await db.query_my_stat("onlyuser") is None

    @pytest.mark.asyncio
    async def test_self_stat_skipped_when_peer_candidate_qualifies(
        self, db: Database
    ) -> None:
        await db.ingest([make_record(score=5, conv_id="a")], "alice", "github")
        await db.ingest([make_record(score=2, conv_id="b")], "bob", "github")

        result = await db.query_my_stat("alice")

        assert result is not None
        assert result.kind != "angriest_hour"


class TestQueryRecentSubmissions:
    @pytest.mark.asyncio
    async def test_empty_db(self, db: Database) -> None:
        assert await db.query_recent_submissions(limit=10) == []

    @pytest.mark.asyncio
    async def test_groups_by_contributor_and_orders_by_last_upload(
        self, db: Database
    ) -> None:
        old = datetime.now(timezone.utc) - timedelta(days=5)
        new = datetime.now(timezone.utc) - timedelta(hours=1)
        await db.ingest([make_record(conv_id="a", t=old)], "alice", "github")
        await db.ingest(
            [
                make_record(conv_id="b1", t=new),
                make_record(conv_id="b2", bucket=1, t=new),
                make_record(conv_id="b2", bucket=2, t=new),
            ],
            "bob",
            "github",
        )

        result = await db.query_recent_submissions(limit=10)

        assert len(result) == 2
        assert all(isinstance(s, AdminSubmission) for s in result)
        assert result[0].contributor_id == "bob"
        assert result[0].record_count == 3
        assert result[0].session_count == 2
        assert result[1].contributor_id == "alice"
        assert result[1].record_count == 1
        assert result[1].session_count == 1
        assert result[0].last_uploaded >= result[1].last_uploaded

    @pytest.mark.asyncio
    async def test_respects_limit(self, db: Database) -> None:
        for i in range(5):
            await db.ingest(
                [make_record(conv_id=f"c-{i}")],
                f"user-{i}",
                "github",
            )

        result = await db.query_recent_submissions(limit=2)

        assert len(result) == 2

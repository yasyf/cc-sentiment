from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from db import (
    CREATE_HYPERTABLE_SQL,
    CREATE_TABLE_SQL,
    INGEST_SQL,
    LAST_UPDATED_SQL,
    Database,
)
from models import DataResponse, SentimentRecord


def make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


def make_query_all_side_effect(
    timeline_rows: list = [],
    hourly_rows: list = [],
    weekday_rows: list = [],
    distribution_rows: list = [],
    total: int = 0,
    last_updated: datetime | None = None,
):
    call_count = 0
    fetchall_results = [timeline_rows, hourly_rows, weekday_rows, distribution_rows]

    def side_effect(*args, **kwargs):
        nonlocal call_count
        idx = call_count
        call_count += 1
        mock = MagicMock()
        match idx:
            case 0 | 7:
                pass
            case 5:
                mock.fetchone.return_value = (total,)
            case 6:
                mock.fetchone.return_value = (last_updated,)
            case n if 1 <= n <= 4:
                mock.fetchall.return_value = fetchall_results[n - 1]
        return mock

    return side_effect


class TestCreateTables:
    def test_executes_create_and_hypertable(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        with patch.object(db, "connect", return_value=conn):
            db.create_tables()

        conn.execute.assert_any_call(CREATE_TABLE_SQL)
        conn.execute.assert_any_call(CREATE_HYPERTABLE_SQL)


class TestIngest:
    def test_executemany_with_params(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        records = [
            SentimentRecord(
                time=datetime(2026, 4, 12, 10, 30, tzinfo=timezone.utc),
                conversation_id="abc-123",
                bucket_index=0,
                sentiment_score=4,
                prompt_version="v1",
                model_id="gemma-4-e4b-it-4bit",
                client_version="0.1.0",
            ),
        ]

        with patch.object(db, "connect", return_value=conn):
            db.ingest(records, "octocat")

        conn.executemany.assert_called_once()
        sql_arg = conn.executemany.call_args[0][0]
        assert sql_arg == INGEST_SQL

        params = conn.executemany.call_args[0][1]
        assert len(params) == 1
        assert params[0]["github_username"] == "octocat"
        assert params[0]["sentiment_score"] == 4
        assert params[0]["conversation_id"] == "abc-123"

    def test_duplicate_records_uses_on_conflict(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        records = [
            SentimentRecord(
                time=datetime(2026, 4, 12, 10, 30, tzinfo=timezone.utc),
                conversation_id="abc-123",
                bucket_index=0,
                sentiment_score=4,
                prompt_version="v1",
                model_id="gemma-4-e4b-it-4bit",
                client_version="0.1.0",
            ),
        ]

        with patch.object(db, "connect", return_value=conn):
            db.ingest(records, "octocat")

        sql = conn.executemany.call_args[0][0]
        assert "ON CONFLICT DO NOTHING" in sql


class TestQueryAll:
    def test_returns_data_response(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        t = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
        now = datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc)

        conn.execute.side_effect = make_query_all_side_effect(
            timeline_rows=[(t, 3.5, 10)],
            hourly_rows=[(14, 4.2, 5)],
            weekday_rows=[(1, 3.8, 20)],
            distribution_rows=[(4, 15)],
            total=50,
            last_updated=now,
        )

        with patch.object(db, "connect", return_value=conn):
            result = db.query_all(days=7)

        assert isinstance(result, DataResponse)
        assert len(result.timeline) == 1
        assert len(result.hourly) == 1
        assert len(result.weekday) == 1
        assert len(result.distribution) == 1
        assert result.total_records == 50
        assert result.last_updated == now

    def test_empty_db_returns_zeroed_response(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        conn.execute.side_effect = make_query_all_side_effect(
            total=0,
            last_updated=None,
        )

        with patch.object(db, "connect", return_value=conn):
            result = db.query_all(days=7)

        assert isinstance(result, DataResponse)
        assert result.timeline == []
        assert result.hourly == []
        assert result.weekday == []
        assert result.distribution == []
        assert result.total_records == 0

    def test_uses_read_only_transaction(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        conn.execute.side_effect = make_query_all_side_effect(
            total=0,
            last_updated=None,
        )

        with patch.object(db, "connect", return_value=conn):
            db.query_all(days=7)

        calls = conn.execute.call_args_list
        assert calls[0].args[0] == "BEGIN TRANSACTION READ ONLY"
        assert calls[-1].args[0] == "COMMIT"

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch

from db import (
    CREATE_HYPERTABLE_SQL,
    CREATE_TABLE_SQL,
    DISTRIBUTION_SQL,
    HOURLY_SQL,
    INGEST_SQL,
    TIMELINE_SQL,
    TOTAL_COUNT_SQL,
    WEEKDAY_SQL,
    Database,
)
from models import (
    DataResponse,
    DistributionPoint,
    HourlyPoint,
    SentimentRecord,
    TimelinePoint,
    WeekdayPoint,
)


def make_mock_conn() -> MagicMock:
    conn = MagicMock()
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=False)
    return conn


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


class TestQueryTimeline:
    def test_returns_timeline_points(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        t = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
        conn.execute.return_value.fetchall.return_value = [(t, 3.5, 10)]

        with patch.object(db, "connect", return_value=conn):
            result = db.query_timeline(days=7)

        assert len(result) == 1
        assert isinstance(result[0], TimelinePoint)
        assert result[0].avg_score == 3.5
        assert result[0].count == 10

        conn.execute.assert_called_once_with(TIMELINE_SQL, {"days": 7})


class TestQueryHourly:
    def test_returns_hourly_points(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        conn.execute.return_value.fetchall.return_value = [(14, 4.2, 5)]

        with patch.object(db, "connect", return_value=conn):
            result = db.query_hourly()

        assert len(result) == 1
        assert isinstance(result[0], HourlyPoint)
        assert result[0].hour == 14


class TestQueryWeekday:
    def test_returns_weekday_points(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        conn.execute.return_value.fetchall.return_value = [(1, 3.8, 20)]

        with patch.object(db, "connect", return_value=conn):
            result = db.query_weekday()

        assert len(result) == 1
        assert isinstance(result[0], WeekdayPoint)
        assert result[0].dow == 1


class TestQueryDistribution:
    def test_returns_distribution_points(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        conn.execute.return_value.fetchall.return_value = [(4, 15)]

        with patch.object(db, "connect", return_value=conn):
            result = db.query_distribution()

        assert len(result) == 1
        assert isinstance(result[0], DistributionPoint)
        assert result[0].score == 4
        assert result[0].count == 15


class TestQueryAll:
    def test_returns_data_response(self) -> None:
        conn = make_mock_conn()
        db = Database("postgres://test:test@localhost/test")

        t = datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc)
        call_count = 0
        results = [
            [(t, 3.5, 10)],
            [(14, 4.2, 5)],
            [(1, 3.8, 20)],
            [(4, 15)],
            [(50,)],
        ]

        def side_effect(*args, **kwargs):
            nonlocal call_count
            mock = MagicMock()
            mock.fetchall.return_value = results[call_count] if call_count < 4 else []
            mock.fetchone.return_value = results[call_count][0] if call_count >= 4 else None
            call_count += 1
            return mock

        conn.execute.side_effect = side_effect

        with patch.object(db, "connect", return_value=conn):
            result = db.query_all(days=7)

        assert isinstance(result, DataResponse)
        assert len(result.timeline) == 1
        assert len(result.hourly) == 1
        assert len(result.weekday) == 1
        assert len(result.distribution) == 1
        assert result.total_records == 50

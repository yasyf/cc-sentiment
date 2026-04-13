from __future__ import annotations

import psycopg

from models import (
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
SELECT create_hypertable('sentiment', 'time', if_not_exists => TRUE)
"""

INGEST_SQL = """
INSERT INTO sentiment (time, conversation_id, bucket_index, github_username, sentiment_score, prompt_version, model_id, client_version)
VALUES (%(time)s, %(conversation_id)s, %(bucket_index)s, %(github_username)s, %(sentiment_score)s, %(prompt_version)s, %(model_id)s, %(client_version)s)
ON CONFLICT DO NOTHING
"""

TIMELINE_SQL = """
SELECT time_bucket('1 hour', time) AS bucket, AVG(sentiment_score)::float AS avg_score, COUNT(*)::int AS count
FROM sentiment
WHERE time > NOW() - make_interval(days => %(days)s)
GROUP BY bucket
ORDER BY bucket
"""

HOURLY_SQL = """
SELECT EXTRACT(hour FROM time)::int AS hour, AVG(sentiment_score)::float AS avg_score, COUNT(*)::int AS count
FROM sentiment
GROUP BY hour
ORDER BY hour
"""

WEEKDAY_SQL = """
SELECT EXTRACT(dow FROM time)::int AS dow, AVG(sentiment_score)::float AS avg_score, COUNT(*)::int AS count
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
        self.dsn = dsn

    def connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn)

    def create_tables(self) -> None:
        with self.connect() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_HYPERTABLE_SQL)

    def ingest(self, records: list[SentimentRecord], github_username: str) -> None:
        with self.connect() as conn:
            conn.executemany(
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

    def query_all(self, days: int = 7) -> DataResponse:
        with self.connect() as conn:
            conn.execute("BEGIN TRANSACTION READ ONLY")
            timeline = [
                TimelinePoint(time=row[0], avg_score=row[1], count=row[2])
                for row in conn.execute(TIMELINE_SQL, {"days": days}).fetchall()
            ]
            hourly = [
                HourlyPoint(hour=row[0], avg_score=row[1], count=row[2])
                for row in conn.execute(HOURLY_SQL).fetchall()
            ]
            weekday = [
                WeekdayPoint(dow=row[0], avg_score=row[1], count=row[2])
                for row in conn.execute(WEEKDAY_SQL).fetchall()
            ]
            distribution = [
                DistributionPoint(score=row[0], count=row[1])
                for row in conn.execute(DISTRIBUTION_SQL).fetchall()
            ]
            total = conn.execute(TOTAL_COUNT_SQL).fetchone()[0]
            last_updated_row = conn.execute(LAST_UPDATED_SQL).fetchone()
            conn.execute("COMMIT")

        from datetime import datetime, timezone
        last_updated = last_updated_row[0] if last_updated_row[0] else datetime.now(timezone.utc)

        return DataResponse(
            timeline=timeline,
            hourly=hourly,
            weekday=weekday,
            distribution=distribution,
            total_records=total,
            last_updated=last_updated,
        )

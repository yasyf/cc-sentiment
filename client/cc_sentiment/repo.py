from __future__ import annotations

import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from cc_sentiment.models import (
    BucketIndex,
    BucketKey,
    PromptVersion,
    SentimentRecord,
    SentimentScore,
    SessionId,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  mtime REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scored_buckets (
  path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
  session_id TEXT NOT NULL,
  bucket_index INTEGER NOT NULL,
  PRIMARY KEY (path, session_id, bucket_index)
);
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY,
  uploaded INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS records (
  session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
  bucket_index INTEGER NOT NULL,
  time TEXT NOT NULL,
  sentiment_score INTEGER NOT NULL,
  prompt_version TEXT NOT NULL,
  claude_model TEXT NOT NULL,
  client_version TEXT NOT NULL,
  read_edit_ratio REAL,
  edits_without_prior_read_ratio REAL,
  write_edit_ratio REAL,
  tool_calls_per_turn REAL NOT NULL,
  subagent_count INTEGER NOT NULL,
  turn_count INTEGER NOT NULL,
  thinking_present INTEGER NOT NULL,
  thinking_chars INTEGER NOT NULL,
  cc_version TEXT NOT NULL,
  PRIMARY KEY (session_id, bucket_index)
);
"""

RECORD_COLUMNS = (
    "session_id",
    "bucket_index",
    "time",
    "sentiment_score",
    "prompt_version",
    "claude_model",
    "client_version",
    "read_edit_ratio",
    "edits_without_prior_read_ratio",
    "write_edit_ratio",
    "tool_calls_per_turn",
    "subagent_count",
    "turn_count",
    "thinking_present",
    "thinking_chars",
    "cc_version",
)


class Repository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.lock = threading.Lock()

    @classmethod
    def default_path(cls) -> Path:
        return Path.home() / ".cc-sentiment" / "records.db"

    @classmethod
    def open(cls, path: Path) -> Repository:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        return cls(conn)

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def file_mtimes(self) -> dict[str, float]:
        with self.lock:
            return {
                row["path"]: row["mtime"]
                for row in self.conn.execute("SELECT path, mtime FROM files")
            }

    def scored_buckets_for(self, path: str) -> frozenset[BucketKey]:
        with self.lock:
            return frozenset(
                BucketKey(
                    session_id=SessionId(row["session_id"]),
                    bucket_index=BucketIndex(row["bucket_index"]),
                )
                for row in self.conn.execute(
                    "SELECT session_id, bucket_index FROM scored_buckets WHERE path = ?",
                    (path,),
                )
            )

    def scored_buckets_for_all(self) -> dict[str, frozenset[BucketKey]]:
        with self.lock:
            out: dict[str, set[BucketKey]] = defaultdict(set)
            for row in self.conn.execute(
                "SELECT path, session_id, bucket_index FROM scored_buckets"
            ):
                out[row["path"]].add(BucketKey(
                    session_id=SessionId(row["session_id"]),
                    bucket_index=BucketIndex(row["bucket_index"]),
                ))
            return {p: frozenset(s) for p, s in out.items()}

    def save_records(
        self, path: str, mtime: float, records: list[SentimentRecord]
    ) -> None:
        placeholders = ", ".join("?" * len(RECORD_COLUMNS))
        columns = ", ".join(RECORD_COLUMNS)
        with self.lock, self.conn:
            self.conn.execute(
                "INSERT INTO files(path, mtime) VALUES(?, ?) "
                "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime",
                (path, mtime),
            )
            self.conn.executemany(
                "INSERT OR IGNORE INTO scored_buckets(path, session_id, bucket_index) "
                "VALUES(?, ?, ?)",
                [(path, r.conversation_id, r.bucket_index) for r in records],
            )
            self.conn.executemany(
                "INSERT INTO sessions(session_id, uploaded) VALUES(?, 0) "
                "ON CONFLICT(session_id) DO UPDATE SET uploaded = 0",
                [(sid,) for sid in {r.conversation_id for r in records}],
            )
            self.conn.executemany(
                f"INSERT OR IGNORE INTO records({columns}) VALUES({placeholders})",
                [
                    (
                        r.conversation_id,
                        r.bucket_index,
                        r.time.isoformat(),
                        r.sentiment_score,
                        r.prompt_version,
                        r.claude_model,
                        r.client_version,
                        r.read_edit_ratio,
                        r.edits_without_prior_read_ratio,
                        r.write_edit_ratio,
                        r.tool_calls_per_turn,
                        r.subagent_count,
                        r.turn_count,
                        int(r.thinking_present),
                        r.thinking_chars,
                        r.cc_version,
                    )
                    for r in records
                ],
            )

    def pending_records(self) -> list[SentimentRecord]:
        with self.lock:
            return [
                self.row_to_record(row)
                for row in self.conn.execute(
                    "SELECT r.* FROM records r "
                    "JOIN sessions s ON r.session_id = s.session_id "
                    "WHERE s.uploaded = 0"
                )
            ]

    def all_records(self) -> list[SentimentRecord]:
        with self.lock:
            return [
                self.row_to_record(row)
                for row in self.conn.execute("SELECT * FROM records")
            ]

    def mark_sessions_uploaded(self, session_ids: set[SessionId]) -> None:
        with self.lock, self.conn:
            self.conn.executemany(
                "UPDATE sessions SET uploaded = 1 WHERE session_id = ?",
                [(sid,) for sid in session_ids],
            )

    def stats(self) -> tuple[int, int, int]:
        with self.lock:
            return (
                self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0],
                self.conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0],
                self.conn.execute("SELECT COUNT(*) FROM files").fetchone()[0],
            )

    def clear_all(self) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM records")
            self.conn.execute("DELETE FROM scored_buckets")
            self.conn.execute("DELETE FROM sessions")
            self.conn.execute("DELETE FROM files")

    @staticmethod
    def row_to_record(row: sqlite3.Row) -> SentimentRecord:
        return SentimentRecord(
            time=datetime.fromisoformat(row["time"]),
            conversation_id=SessionId(row["session_id"]),
            bucket_index=BucketIndex(row["bucket_index"]),
            sentiment_score=SentimentScore(row["sentiment_score"]),
            prompt_version=PromptVersion(row["prompt_version"]),
            claude_model=row["claude_model"],
            client_version=row["client_version"],
            read_edit_ratio=row["read_edit_ratio"],
            edits_without_prior_read_ratio=row["edits_without_prior_read_ratio"],
            write_edit_ratio=row["write_edit_ratio"],
            tool_calls_per_turn=row["tool_calls_per_turn"],
            subagent_count=row["subagent_count"],
            turn_count=row["turn_count"],
            thinking_present=bool(row["thinking_present"]),
            thinking_chars=row["thinking_chars"],
            cc_version=row["cc_version"],
        )

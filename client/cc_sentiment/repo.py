from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Self

import aiosqlite
import anyio

from cc_sentiment.models import (
    BucketIndex,
    BucketKey,
    PromptVersion,
    SentimentRecord,
    SentimentScore,
    SessionId,
)

if TYPE_CHECKING:
    from types import TracebackType

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
    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn
        self.lock = anyio.Lock()

    @classmethod
    def default_path(cls) -> Path:
        return Path.home() / ".cc-sentiment" / "records.db"

    @classmethod
    async def open(cls, path: Path) -> Self:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = await aiosqlite.connect(str(path), isolation_level=None)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute("PRAGMA journal_mode = WAL")
        await conn.executescript(SCHEMA)
        return cls(conn)

    async def close(self) -> None:
        async with self.lock:
            await self.conn.close()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def file_mtimes(self) -> dict[str, float]:
        async with self.lock, self.conn.execute("SELECT path, mtime FROM files") as cur:
            return {row["path"]: row["mtime"] async for row in cur}

    async def scored_buckets_for(self, path: str) -> frozenset[BucketKey]:
        async with self.lock, self.conn.execute(
            "SELECT session_id, bucket_index FROM scored_buckets WHERE path = ?",
            (path,),
        ) as cur:
            return frozenset({
                BucketKey(
                    session_id=SessionId(row["session_id"]),
                    bucket_index=BucketIndex(row["bucket_index"]),
                )
                async for row in cur
            })

    async def scored_buckets_for_all(self) -> dict[str, frozenset[BucketKey]]:
        async with self.lock, self.conn.execute(
            "SELECT path, session_id, bucket_index FROM scored_buckets"
        ) as cur:
            out: dict[str, set[BucketKey]] = defaultdict(set)
            async for row in cur:
                out[row["path"]].add(BucketKey(
                    session_id=SessionId(row["session_id"]),
                    bucket_index=BucketIndex(row["bucket_index"]),
                ))
            return {p: frozenset(s) for p, s in out.items()}

    async def save_records(
        self, path: str, mtime: float, records: list[SentimentRecord]
    ) -> None:
        placeholders = ", ".join("?" * len(RECORD_COLUMNS))
        columns = ", ".join(RECORD_COLUMNS)
        async with self.lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.execute(
                    "INSERT INTO files(path, mtime) VALUES(?, ?) "
                    "ON CONFLICT(path) DO UPDATE SET mtime = excluded.mtime",
                    (path, mtime),
                )
                await self.conn.executemany(
                    "INSERT OR IGNORE INTO scored_buckets(path, session_id, bucket_index) "
                    "VALUES(?, ?, ?)",
                    [(path, r.conversation_id, r.bucket_index) for r in records],
                )
                await self.conn.executemany(
                    "INSERT INTO sessions(session_id, uploaded) VALUES(?, 0) "
                    "ON CONFLICT(session_id) DO UPDATE SET uploaded = 0",
                    [(sid,) for sid in {r.conversation_id for r in records}],
                )
                await self.conn.executemany(
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
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    async def pending_records(self) -> list[SentimentRecord]:
        async with self.lock, self.conn.execute(
            "SELECT r.* FROM records r "
            "JOIN sessions s ON r.session_id = s.session_id "
            "WHERE s.uploaded = 0"
        ) as cur:
            return [self.row_to_record(row) async for row in cur]

    async def all_records(self) -> list[SentimentRecord]:
        async with self.lock, self.conn.execute("SELECT * FROM records") as cur:
            return [self.row_to_record(row) async for row in cur]

    async def mark_sessions_uploaded(self, session_ids: set[SessionId]) -> None:
        async with self.lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.executemany(
                    "UPDATE sessions SET uploaded = 1 WHERE session_id = ?",
                    [(sid,) for sid in session_ids],
                )
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    async def stats(self) -> tuple[int, int, int]:
        async with self.lock:
            async with self.conn.execute("SELECT COUNT(*) FROM records") as cur:
                records = (await cur.fetchone())[0]
            async with self.conn.execute("SELECT COUNT(*) FROM sessions") as cur:
                sessions = (await cur.fetchone())[0]
            async with self.conn.execute("SELECT COUNT(*) FROM files") as cur:
                files = (await cur.fetchone())[0]
            return (records, sessions, files)

    async def clear_all(self) -> None:
        async with self.lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                await self.conn.execute("DELETE FROM records")
                await self.conn.execute("DELETE FROM scored_buckets")
                await self.conn.execute("DELETE FROM sessions")
                await self.conn.execute("DELETE FROM files")
            except BaseException:
                await self.conn.rollback()
                raise
            else:
                await self.conn.commit()

    @staticmethod
    def row_to_record(row: aiosqlite.Row) -> SentimentRecord:
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

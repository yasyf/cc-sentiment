from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import orjson

from cc_sentiment.models import (
    BucketIndex,
    ConversationBucket,
    SessionId,
    TranscriptMessage,
)

CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"
BUCKET_MINUTES = 5
ASSISTANT_TRUNCATION = 1024


class TranscriptDiscovery:
    @staticmethod
    def find_transcripts() -> list[Path]:
        if not CLAUDE_PROJECTS_DIR.exists():
            return []
        return sorted(CLAUDE_PROJECTS_DIR.rglob("*.jsonl"))

    @staticmethod
    def transcript_mtime(path: Path) -> float:
        return path.stat().st_mtime


class TranscriptParser:
    @staticmethod
    def parse_line(line: str) -> TranscriptMessage | None:
        data = orjson.loads(line)

        match data["type"]:
            case "queue-operation":
                return None
            case "user" if data.get("isSidechain"):
                return None
            case "user":
                raw_content = data["message"]["content"]
                match raw_content:
                    case str():
                        content = raw_content
                    case list():
                        text_parts = [
                            block["text"] for block in raw_content
                            if isinstance(block, dict) and block.get("type") == "text"
                        ]
                        if not text_parts:
                            return None
                        content = " ".join(text_parts)
                    case _:
                        return None
                return TranscriptMessage(
                    role="user",
                    content=content,
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    session_id=SessionId(data["sessionId"]),
                    uuid=data["uuid"],
                    tool_names=(),
                    thinking_chars=0,
                    cc_version=data.get("version", ""),
                )
            case "assistant":
                blocks = data["message"]["content"]
                text_blocks = [
                    block["text"]
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                tool_names = tuple(
                    block["name"]
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "tool_use"
                )
                thinking_chars = sum(
                    len(block.get("thinking", ""))
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "thinking"
                )
                if not text_blocks and not tool_names:
                    return None
                combined = " ".join(text_blocks)
                truncated = (
                    combined[:ASSISTANT_TRUNCATION] + "[...]"
                    if len(combined) > ASSISTANT_TRUNCATION
                    else combined
                )
                return TranscriptMessage(
                    role="assistant",
                    content=truncated,
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    session_id=SessionId(data["sessionId"]),
                    uuid=data["uuid"],
                    tool_names=tool_names,
                    thinking_chars=thinking_chars,
                    cc_version="",
                )
            case _:
                return None

    @classmethod
    def parse_file(cls, path: Path) -> list[TranscriptMessage]:
        return [
            msg
            for line in path.read_text().splitlines()
            if line.strip() and (msg := cls.parse_line(line)) is not None
        ]


class ConversationBucketer:
    @staticmethod
    def align_to_bucket(ts: datetime) -> datetime:
        return ts.replace(
            minute=(ts.minute // BUCKET_MINUTES) * BUCKET_MINUTES,
            second=0,
            microsecond=0,
        )

    @classmethod
    def bucket_messages(
        cls, messages: list[TranscriptMessage]
    ) -> list[ConversationBucket]:
        by_session: dict[SessionId, list[TranscriptMessage]] = defaultdict(list)
        for msg in messages:
            by_session[msg.session_id].append(msg)

        buckets: list[ConversationBucket] = []
        for session_id, session_msgs in by_session.items():
            session_msgs.sort(key=lambda m: m.timestamp)
            session_start = cls.align_to_bucket(session_msgs[0].timestamp)

            grouped: dict[int, list[TranscriptMessage]] = defaultdict(list)
            for msg in session_msgs:
                idx = int(
                    (msg.timestamp - session_start) // timedelta(minutes=BUCKET_MINUTES)
                )
                grouped[idx].append(msg)

            for idx, bucket_msgs in sorted(grouped.items()):
                bucket_start = session_start + timedelta(minutes=BUCKET_MINUTES * idx)
                buckets.append(
                    ConversationBucket(
                        session_id=session_id,
                        bucket_index=BucketIndex(idx),
                        bucket_start=bucket_start,
                        messages=tuple(bucket_msgs),
                    )
                )

        return buckets

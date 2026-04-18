from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import ClassVar, Literal

from cc_sentiment import _transcripts_rs as rust
from cc_sentiment.models import (
    AssistantMessage,
    BucketIndex,
    BucketKey,
    SessionId,
    ToolCall,
    TranscriptMessage,
    UserMessage,
)


class RustBackend:
    name: ClassVar[Literal["rust", "python"]] = "rust"

    def parse_line(self, line: str) -> TranscriptMessage | None:
        data = rust.parse_line(line)
        return self.message_from_dict(data) if data is not None else None

    def parse_file(self, path: Path) -> list[TranscriptMessage]:
        return [self.message_from_dict(d) for d in rust.parse_file(str(path))]

    def bucket_keys_for(self, path: Path) -> list[BucketKey]:
        return [
            BucketKey(session_id=SessionId(s), bucket_index=BucketIndex(i))
            for s, i in rust.bucket_keys_for(str(path))
        ]

    def scan_bucket_keys(
        self,
        directory: Path,
        *,
        name_contains: str | None = None,
        limit: int | None = None,
        known_mtimes: dict[str, float] | None = None,
    ) -> list[tuple[Path, float, list[BucketKey]]]:
        return [
            (
                Path(p),
                mtime,
                [
                    BucketKey(session_id=SessionId(s), bucket_index=BucketIndex(i))
                    for s, i in keys
                ],
            )
            for p, mtime, keys in rust.scan_bucket_keys(
                str(directory),
                name_contains=name_contains,
                limit=limit,
                known_mtimes=known_mtimes,
            )
        ]

    @staticmethod
    def message_from_dict(data: dict) -> TranscriptMessage:
        tool_calls = tuple(
            ToolCall(name=tc["name"], file_path=tc.get("file_path"))
            for tc in data.get("tool_calls", ())
        )
        ts = datetime.fromisoformat(data["timestamp"])
        session_id = SessionId(data["session_id"])
        match data["kind"]:
            case "user":
                return UserMessage(
                    content=data["content"],
                    timestamp=ts,
                    session_id=session_id,
                    uuid=data["uuid"],
                    tool_calls=tool_calls,
                    thinking_chars=data.get("thinking_chars", 0),
                    cc_version=data["cc_version"],
                )
            case "assistant":
                return AssistantMessage(
                    content=data["content"],
                    timestamp=ts,
                    session_id=session_id,
                    uuid=data["uuid"],
                    tool_calls=tool_calls,
                    thinking_chars=data.get("thinking_chars", 0),
                    claude_model=data["claude_model"],
                )
            case other:
                raise ValueError(f"unknown message kind: {other!r}")

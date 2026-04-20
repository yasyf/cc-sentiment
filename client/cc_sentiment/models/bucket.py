from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import NamedTuple, NewType

from pydantic import BaseModel

from .transcript import AssistantMessage, TranscriptMessage, UserMessage

SessionId = NewType("SessionId", str)
BucketIndex = NewType("BucketIndex", int)
SentimentScore = NewType("SentimentScore", int)
PromptVersion = NewType("PromptVersion", str)

PROMPT_VERSION = PromptVersion("v2")


class BucketMetrics(BaseModel, frozen=True):
    tool_counts: dict[str, int]
    read_edit_ratio: float | None
    edits_without_prior_read_ratio: float | None
    write_edit_ratio: float | None
    tool_calls_per_turn: float
    subagent_count: int
    turn_count: int
    thinking_present: bool
    thinking_chars: int
    cc_version: str
    claude_model: str

    @staticmethod
    def from_messages(messages: tuple[TranscriptMessage, ...]) -> BucketMetrics:
        return BucketMetrics.from_messages_with_history(messages, frozenset())

    @staticmethod
    def from_messages_with_history(
        messages: tuple[TranscriptMessage, ...],
        prior_reads: frozenset[str] | set[str],
    ) -> BucketMetrics:
        users = tuple(m for m in messages if isinstance(m, UserMessage))
        assistants = tuple(m for m in messages if isinstance(m, AssistantMessage))
        if not users or not assistants:
            raise ValueError("bucket must have both user and assistant messages")

        all_calls = tuple(c for m in messages for c in m.tool_calls)
        tool_counts = Counter(c.name for c in all_calls)
        read_ops = sum(tool_counts[t] for t in ("Read", "Grep", "Glob"))
        write_ops = sum(tool_counts[t] for t in ("Edit", "Write"))
        thinking = sum(m.thinking_chars for m in messages)

        reads_seen = set(prior_reads)
        edits_count = 0
        edits_without_read = 0
        for msg in messages:
            for call in msg.tool_calls:
                match call.name:
                    case "Read" if call.file_path:
                        reads_seen.add(call.file_path)
                    case "Edit" | "Write" if call.file_path:
                        edits_count += 1
                        if call.file_path not in reads_seen:
                            edits_without_read += 1

        writes_only = tool_counts.get("Write", 0)
        edits_only = tool_counts.get("Edit", 0)
        write_edit_total = writes_only + edits_only

        return BucketMetrics(
            tool_counts=dict(tool_counts),
            read_edit_ratio=read_ops / write_ops if write_ops else None,
            edits_without_prior_read_ratio=(
                edits_without_read / edits_count if edits_count else None
            ),
            write_edit_ratio=(
                writes_only / write_edit_total if write_edit_total else None
            ),
            tool_calls_per_turn=len(all_calls) / len(users),
            subagent_count=tool_counts.get("Agent", 0),
            turn_count=len(users),
            thinking_present=thinking > 0,
            thinking_chars=thinking,
            cc_version=users[-1].cc_version,
            claude_model=assistants[-1].claude_model,
        )


class ConversationBucket(NamedTuple):
    session_id: SessionId
    bucket_index: BucketIndex
    bucket_start: datetime
    messages: tuple[TranscriptMessage, ...]

    @property
    def metrics(self) -> BucketMetrics:
        return BucketMetrics.from_messages(self.messages)


class BucketKey(NamedTuple):
    session_id: SessionId
    bucket_index: BucketIndex

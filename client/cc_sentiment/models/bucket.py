from __future__ import annotations

from collections import Counter
from typing import NewType

from cc_transcript.models import AssistantEvent, SessionId, UserEvent, thinking_chars, tool_uses
from cc_transcript.sentiment.buckets import (
    BucketIndex,
    BucketKey,
    ConversationBucket,
    ConversationEvent,
    SentimentScore,
)
from cc_transcript.tools import EditCall, ReadCall, WriteCall, parse_tool_call
from pydantic import BaseModel

PromptVersion = NewType("PromptVersion", str)

PROMPT_VERSION = PromptVersion("v1")


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
    def from_events(events: tuple[ConversationEvent, ...]) -> BucketMetrics:
        return BucketMetrics.from_events_with_history(events, frozenset())

    @staticmethod
    def from_events_with_history(
        events: tuple[ConversationEvent, ...],
        prior_reads: frozenset[str] | set[str],
    ) -> BucketMetrics:
        users = tuple(e for e in events if isinstance(e, UserEvent))
        assistants = tuple(e for e in events if isinstance(e, AssistantEvent))
        if not users or not assistants:
            raise ValueError("bucket must have both user and assistant events")

        all_calls = tuple(block for e in events for block in tool_uses(e))
        tool_counts = Counter(block.name for block in all_calls)
        read_ops = sum(tool_counts[t] for t in ("Read", "Grep", "Glob"))
        write_ops = sum(tool_counts[t] for t in ("Edit", "Write"))
        thinking = sum(thinking_chars(e) for e in events)

        reads_seen = set(prior_reads)
        edits_count = 0
        edits_without_read = 0
        for event in events:
            for block in tool_uses(event):
                match parse_tool_call(block.name, block.input, on_error="other"):
                    case ReadCall(file_path=path):
                        reads_seen.add(path)
                    case EditCall(file_path=path) | WriteCall(file_path=path):
                        edits_count += 1
                        if path not in reads_seen:
                            edits_without_read += 1

        writes_only = tool_counts.get("Write", 0)
        edits_only = tool_counts.get("Edit", 0)
        write_edit_total = writes_only + edits_only

        return BucketMetrics(
            tool_counts=dict(tool_counts),
            read_edit_ratio=read_ops / write_ops if write_ops else None,
            edits_without_prior_read_ratio=(edits_without_read / edits_count if edits_count else None),
            write_edit_ratio=(writes_only / write_edit_total if write_edit_total else None),
            tool_calls_per_turn=len(all_calls) / len(users),
            subagent_count=tool_counts.get("Agent", 0),
            turn_count=len(users),
            thinking_present=thinking > 0,
            thinking_chars=thinking,
            cc_version=users[-1].meta.cc_version or "",
            claude_model=assistants[-1].model,
        )

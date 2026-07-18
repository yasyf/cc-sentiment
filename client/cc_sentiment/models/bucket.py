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
from cc_transcript.tools import (
    EditCall,
    GlobCall,
    GrepCall,
    ReadCall,
    TaskCall,
    WriteCall,
    parse_tool_call,
)
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

        all_blocks = tuple(block for e in events for block in tool_uses(e))
        thinking = sum(thinking_chars(e) for e in events)

        reads_seen = set(prior_reads)
        read_ops = writes_only = edits_only = edits_without_read = subagent_count = 0
        for block in all_blocks:
            match parse_tool_call(block.name, block.input, on_error="other"):
                case ReadCall(file_path=path):
                    read_ops += 1
                    reads_seen.add(path)
                case GrepCall() | GlobCall():
                    read_ops += 1
                case TaskCall():
                    subagent_count += 1
                case WriteCall(file_path=path):
                    writes_only += 1
                    if path not in reads_seen:
                        edits_without_read += 1
                case EditCall(file_path=path):
                    edits_only += 1
                    if path not in reads_seen:
                        edits_without_read += 1

        edit_ops = writes_only + edits_only

        return BucketMetrics(
            tool_counts=dict(Counter(block.name for block in all_blocks)),
            read_edit_ratio=read_ops / edit_ops if edit_ops else None,
            edits_without_prior_read_ratio=(edits_without_read / edit_ops if edit_ops else None),
            write_edit_ratio=(writes_only / edit_ops if edit_ops else None),
            tool_calls_per_turn=len(all_blocks) / len(users),
            subagent_count=subagent_count,
            turn_count=len(users),
            thinking_present=thinking > 0,
            thinking_chars=thinking,
            cc_version=users[-1].meta.cc_version or "",
            claude_model=assistants[-1].model,
        )

from __future__ import annotations

from typing import TYPE_CHECKING

from cc_transcript.models import AssistantEvent, ThinkingBlock, ToolUseBlock, UserEvent

from cc_sentiment.models import AssistantMessage, SessionId, ToolCall, TranscriptMessage, UserMessage

if TYPE_CHECKING:
    from collections.abc import Iterable

    from cc_transcript.models import TranscriptEvent

ASSISTANT_TRUNCATION = 1024


def to_message(event: TranscriptEvent) -> TranscriptMessage | None:
    """Maps a filtered cc-transcript event onto a cc-sentiment message.

    Reproduces the legacy parser's derivations exactly: user content is
    stripped, assistant content is truncated to ``ASSISTANT_TRUNCATION`` code
    points with a ``[...]`` suffix, thinking is summed by character count, and
    only ``tool_use`` blocks contribute tool calls.
    """
    match event:
        case UserEvent():
            return UserMessage(
                content=event.text.strip(),
                timestamp=event.meta.timestamp,
                session_id=SessionId(event.meta.session_id),
                uuid=event.meta.uuid,
                tool_calls=(),
                thinking_chars=0,
                cc_version=event.meta.cc_version or "",
            )
        case AssistantEvent():
            combined = event.text
            return AssistantMessage(
                content=combined[:ASSISTANT_TRUNCATION] + "[...]" if len(combined) > ASSISTANT_TRUNCATION else combined,
                timestamp=event.meta.timestamp,
                session_id=SessionId(event.meta.session_id),
                uuid=event.meta.uuid,
                tool_calls=tuple(
                    ToolCall(name=block.name, file_path=block.input.get("file_path"))
                    for block in event.blocks
                    if isinstance(block, ToolUseBlock)
                ),
                thinking_chars=sum(len(block.thinking) for block in event.blocks if isinstance(block, ThinkingBlock)),
                claude_model=event.model,
            )
        case _:
            return None


def to_messages(events: Iterable[TranscriptEvent]) -> tuple[TranscriptMessage, ...]:
    """Adapts a filtered event stream into cc-sentiment messages."""
    return tuple(message for event in events if (message := to_message(event)) is not None)

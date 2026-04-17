from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_sentiment.models import (
    AssistantMessage,
    BucketMetrics,
    SessionId,
    ToolCall,
    UserMessage,
)
from cc_sentiment.transcripts import (
    ASSISTANT_TRUNCATION,
    ConversationBucketer,
    TranscriptParser,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


class TestTranscriptParser:
    def test_skips_queue_operations(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        types_present = {m.content for m in messages}
        assert "queued task" not in types_present

    def test_skips_sidechain_messages(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        uuids = {m.uuid for m in messages}
        assert "u3-sidechain" not in uuids

    def test_parses_user_messages(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        user_msgs = [m for m in messages if m.role == "user"]
        assert len(user_msgs) == 5
        assert user_msgs[0].content == "fix the login bug please, it keeps crashing on submit"

    def test_parses_assistant_text_blocks(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(assistant_msgs) == 5
        assert "thinking" not in assistant_msgs[0].content.lower()
        assert "I'll fix the login bug" in assistant_msgs[0].content

    def test_excludes_tool_use_blocks(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        for msg in assistant_msgs:
            assert "read_file" not in msg.content

    def test_groups_by_session(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        sessions = {m.session_id for m in messages}
        assert sessions == {SessionId("session-aaa"), SessionId("session-bbb")}

    def test_preserves_timestamps(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        assert messages[0].timestamp == datetime(
            2026, 4, 10, 7, 36, 0, tzinfo=timezone.utc
        )

    def test_truncates_long_assistant_messages(self) -> None:
        long_text = "x" * 2000
        line = (
            '{"parentUuid":"p","message":{"model":"m","type":"message",'
            '"role":"assistant","content":[{"type":"text","text":"'
            + long_text
            + '"}]},"type":"assistant","uuid":"u","timestamp":"2026-04-10T07:40:00.000Z","sessionId":"s"}'
        )
        msg = TranscriptParser.parse_line(line)
        assert msg is not None
        assert len(msg.content) == ASSISTANT_TRUNCATION + len("[...]")
        assert msg.content.endswith("[...]")

    def test_skips_synthetic_terminator_assistant_lines(self) -> None:
        line = (
            '{"parentUuid":"p","message":{"model":"<synthetic>","type":"message",'
            '"role":"assistant","stop_reason":"stop_sequence",'
            '"content":[{"type":"text","text":"No response requested."}]},'
            '"type":"assistant","uuid":"u","timestamp":"2026-04-10T07:40:00.000Z","sessionId":"s"}'
        )
        assert TranscriptParser.parse_line(line) is None

    def test_skips_ephemeral_sdk_entrypoints(self) -> None:
        for entrypoint in ("sdk-cli", "sdk-ts"):
            line = (
                '{"parentUuid":null,"isSidechain":false,"type":"user",'
                '"message":{"role":"user","content":"one-shot"},'
                f'"entrypoint":"{entrypoint}",'
                '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
                '"sessionId":"s","version":"2.1.92"}'
            )
            assert TranscriptParser.parse_line(line) is None

    def test_allows_cli_entrypoint(self) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"hello"},'
            '"entrypoint":"cli",'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s","version":"2.1.92"}'
        )
        msg = TranscriptParser.parse_line(line)
        assert msg is not None
        assert msg.content == "hello"


class TestConversationBucketer:
    def test_bucket_count(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        session_aaa_buckets = [
            b for b in buckets if b.session_id == SessionId("session-aaa")
        ]
        assert len(session_aaa_buckets) == 1

    def test_bucket_alignment(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        for bucket in buckets:
            assert bucket.bucket_start.second == 0
            assert bucket.bucket_start.microsecond == 0
            assert bucket.bucket_start.minute % 5 == 0

    def test_messages_in_correct_buckets(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        bucket = next(
            b for b in buckets if b.session_id == SessionId("session-aaa")
        )
        user_contents = [m.content for m in bucket.messages if m.role == "user"]
        assert "great, that fixed it! now can you add email validation too?" in user_contents

    def test_separate_sessions_bucketed_independently(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        session_bbb_buckets = [
            b for b in buckets if b.session_id == SessionId("session-bbb")
        ]
        assert len(session_bbb_buckets) == 1

    def test_drops_single_user_turn_sessions(self) -> None:
        messages = [
            UserMessage(
                content="one-shot prompt",
                timestamp=datetime(2026, 4, 10, 7, 36, 0, tzinfo=timezone.utc),
                session_id=SessionId("one-shot"),
                uuid="u1",
                tool_calls=(),
                thinking_chars=0,
                cc_version="2.1.92",
            ),
            AssistantMessage(
                content="sure",
                timestamp=datetime(2026, 4, 10, 7, 36, 30, tzinfo=timezone.utc),
                session_id=SessionId("one-shot"),
                uuid="a1",
                tool_calls=(),
                thinking_chars=0,
                claude_model="claude-sonnet-4-20250514",
            ),
        ]
        buckets = ConversationBucketer.bucket_messages(messages)
        assert [b for b in buckets if b.session_id == SessionId("one-shot")] == []


class TestBucketMetrics:
    @staticmethod
    def _user(cc_version: str = "2.1.92") -> UserMessage:
        return UserMessage(
            content="hi",
            timestamp=datetime(2026, 4, 10, 7, 36, 0, tzinfo=timezone.utc),
            session_id=SessionId("s"),
            uuid="u",
            tool_calls=(),
            thinking_chars=0,
            cc_version=cc_version,
        )

    @staticmethod
    def _assistant(
        claude_model: str = "claude-sonnet-4-20250514",
        tool_calls: tuple[ToolCall, ...] = (),
    ) -> AssistantMessage:
        return AssistantMessage(
            content="ok",
            timestamp=datetime(2026, 4, 10, 7, 36, 30, tzinfo=timezone.utc),
            session_id=SessionId("s"),
            uuid="a",
            tool_calls=tool_calls,
            thinking_chars=0,
            claude_model=claude_model,
        )

    def test_requires_assistant(self) -> None:
        with pytest.raises(ValueError, match="both user and assistant"):
            BucketMetrics.from_messages((self._user(),))

    def test_requires_user(self) -> None:
        with pytest.raises(ValueError, match="both user and assistant"):
            BucketMetrics.from_messages((self._assistant(),))

    def test_carries_last_user_cc_version_and_last_assistant_model(self) -> None:
        metrics = BucketMetrics.from_messages((
            self._user(cc_version="2.1.0"),
            self._assistant(claude_model="claude-opus-4-6"),
            self._user(cc_version="2.1.92"),
            self._assistant(claude_model="claude-sonnet-4-20250514"),
        ))
        assert metrics.cc_version == "2.1.92"
        assert metrics.claude_model == "claude-sonnet-4-20250514"
        assert metrics.turn_count == 2

    def test_tool_calls_per_turn_and_subagent_count(self) -> None:
        metrics = BucketMetrics.from_messages((
            self._user(),
            self._assistant(tool_calls=(
                ToolCall(name="Read", file_path="/a.py"),
                ToolCall(name="Agent"),
                ToolCall(name="Agent"),
            )),
        ))
        assert metrics.tool_calls_per_turn == 3.0
        assert metrics.subagent_count == 2

    def test_write_edit_ratio(self) -> None:
        metrics = BucketMetrics.from_messages((
            self._user(),
            self._assistant(tool_calls=(
                ToolCall(name="Write", file_path="/a.py"),
                ToolCall(name="Write", file_path="/b.py"),
                ToolCall(name="Edit", file_path="/c.py"),
            )),
        ))
        assert metrics.write_edit_ratio == 2 / 3

    def test_write_edit_ratio_none_when_no_writes_or_edits(self) -> None:
        metrics = BucketMetrics.from_messages((
            self._user(),
            self._assistant(tool_calls=(ToolCall(name="Read", file_path="/a.py"),)),
        ))
        assert metrics.write_edit_ratio is None

    def test_edits_without_prior_read_within_bucket(self) -> None:
        metrics = BucketMetrics.from_messages((
            self._user(),
            self._assistant(tool_calls=(
                ToolCall(name="Read", file_path="/a.py"),
                ToolCall(name="Edit", file_path="/a.py"),
                ToolCall(name="Edit", file_path="/b.py"),
            )),
        ))
        assert metrics.edits_without_prior_read_ratio == 0.5

    def test_edits_without_prior_read_uses_history(self) -> None:
        metrics = BucketMetrics.from_messages_with_history(
            (
                self._user(),
                self._assistant(tool_calls=(ToolCall(name="Edit", file_path="/a.py"),)),
            ),
            frozenset({"/a.py"}),
        )
        assert metrics.edits_without_prior_read_ratio == 0.0

    def test_edits_without_prior_read_none_when_no_edits(self) -> None:
        metrics = BucketMetrics.from_messages((
            self._user(),
            self._assistant(tool_calls=(ToolCall(name="Read", file_path="/a.py"),)),
        ))
        assert metrics.edits_without_prior_read_ratio is None

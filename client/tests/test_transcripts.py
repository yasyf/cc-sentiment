from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from cc_sentiment.models import SessionId
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
        assert len(assistant_msgs) == 3
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
            2026, 4, 10, 7, 39, 20, tzinfo=timezone.utc
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

    def test_drops_user_only_buckets(self) -> None:
        messages = TranscriptParser.parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        for bucket in buckets:
            assert any(m.role == "assistant" for m in bucket.messages)

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

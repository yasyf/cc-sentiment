from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import anyio
import orjson
import pytest

from cc_sentiment.models import (
    AssistantMessage,
    BucketKey,
    BucketMetrics,
    SessionId,
    ToolCall,
    TranscriptMessage,
    UserMessage,
)
from cc_sentiment.transcripts import (
    ASSISTANT_TRUNCATION,
    BUCKET_MINUTES,
    ConversationBucketer,
    ParsedTranscript,
    PythonBackend,
    TranscriptParser,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def parse_paths(paths: list[tuple[Path, float]]) -> list[ParsedTranscript]:
    async def run() -> list[ParsedTranscript]:
        return [p async for p in TranscriptParser.stream_transcripts(paths)]

    return anyio.run(run)


def parse_file(path: Path) -> list[TranscriptMessage]:
    results = parse_paths([(path, 0.0)])
    assert len(results) == 1
    return list(results[0].messages)


def parse_single_line(tmp_path: Path, line: str) -> TranscriptMessage | None:
    f = tmp_path / "single.jsonl"
    f.write_text(line)
    messages = parse_file(f)
    return messages[0] if messages else None


@pytest.fixture(params=["python", "rust"])
def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    if request.param == "rust":
        try:
            from cc_sentiment.transcripts.rust import RustBackend
        except ImportError:
            pytest.skip("rust extension not built")
        monkeypatch.setattr(TranscriptParser, "BACKEND", RustBackend())
    else:
        monkeypatch.setattr(TranscriptParser, "BACKEND", PythonBackend())
    return request.param


@pytest.mark.usefixtures("backend")
class TestStreamTranscripts:
    def test_skips_queue_operations(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        types_present = {m.content for m in messages}
        assert "queued task" not in types_present

    def test_skips_sidechain_messages(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        uuids = {m.uuid for m in messages}
        assert "u3-sidechain" not in uuids

    def test_parses_user_messages(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        user_msgs = [m for m in messages if m.role == "user"]
        assert len(user_msgs) == 5
        assert user_msgs[0].content == "fix the login bug please, it keeps crashing on submit"

    def test_parses_assistant_text_blocks(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        assert len(assistant_msgs) == 5
        assert "thinking" not in assistant_msgs[0].content.lower()
        assert "I'll fix the login bug" in assistant_msgs[0].content

    def test_excludes_tool_use_blocks(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        assistant_msgs = [m for m in messages if m.role == "assistant"]
        for msg in assistant_msgs:
            assert "read_file" not in msg.content

    def test_groups_by_session(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        sessions = {m.session_id for m in messages}
        assert sessions == {SessionId("session-aaa"), SessionId("session-bbb")}

    def test_preserves_timestamps(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        assert messages[0].timestamp == datetime(
            2026, 4, 10, 7, 36, 0, tzinfo=timezone.utc
        )

    def test_truncates_long_assistant_messages(self, tmp_path: Path) -> None:
        long_text = "x" * 2000
        line = (
            '{"parentUuid":"p","message":{"model":"m","type":"message",'
            '"role":"assistant","content":[{"type":"text","text":"'
            + long_text
            + '"}]},"type":"assistant","uuid":"u","timestamp":"2026-04-10T07:40:00.000Z","sessionId":"s"}'
        )
        msg = parse_single_line(tmp_path, line)
        assert msg is not None
        assert len(msg.content) == ASSISTANT_TRUNCATION + len("[...]")
        assert msg.content.endswith("[...]")

    def test_skips_synthetic_terminator_assistant_lines(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":"p","message":{"model":"<synthetic>","type":"message",'
            '"role":"assistant","stop_reason":"stop_sequence",'
            '"content":[{"type":"text","text":"No response requested."}]},'
            '"type":"assistant","uuid":"u","timestamp":"2026-04-10T07:40:00.000Z","sessionId":"s"}'
        )
        assert parse_single_line(tmp_path, line) is None

    def test_skips_ephemeral_sdk_cli_entrypoint(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"one-shot"},'
            '"entrypoint":"sdk-cli",'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s","version":"2.1.92"}'
        )
        assert parse_single_line(tmp_path, line) is None

    def test_allows_conductor_sdk_ts_entrypoint(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"fix the failing test"},'
            '"entrypoint":"sdk-ts","promptId":"prompt-123",'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s","version":"2.1.7",'
            '"cwd":"/Users/me/conductor/workspaces/project/branch"}'
        )
        msg = parse_single_line(tmp_path, line)
        assert isinstance(msg, UserMessage)
        assert msg.content == "fix the failing test"
        assert msg.cc_version == "2.1.7"

    def test_allows_cli_entrypoint(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"hello"},'
            '"entrypoint":"cli",'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s","version":"2.1.92"}'
        )
        msg = parse_single_line(tmp_path, line)
        assert msg is not None
        assert msg.content == "hello"

    def test_allows_user_message_without_version(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"hello"},'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s"}'
        )
        msg = parse_single_line(tmp_path, line)
        assert isinstance(msg, UserMessage)
        assert msg.content == "hello"
        assert msg.cc_version == ""

    def test_drops_user_message_with_system_reminder(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"<system-reminder>tool notes</system-reminder>"},'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s","version":"2.1.92"}'
        )
        assert parse_single_line(tmp_path, line) is None

    def test_drops_compact_summary_user_message(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "This session is being continued from a previous conversation that ran out of context.\n\nSummary: did stuff"},
            "isCompactSummary": True, "isVisibleInTranscriptOnly": True,
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.111", "entrypoint": "sdk-ts",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_visible_in_transcript_only_user_message(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "real-looking content that would otherwise pass filters"},
            "isVisibleInTranscriptOnly": True,
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_user_message_with_caveat_preamble(self, tmp_path: Path) -> None:
        content = (
            "Caveat: The messages below were generated by the user while running local commands.\n"
            "<local-command-stdout>...</local-command-stdout>"
        )
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": content},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_user_message_with_interrupt_marker(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "[Request interrupted by user for tool use] actually let's redo it"},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_user_message_with_persisted_output(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "<persisted-output>past output</persisted-output>"},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_stop_hook_feedback(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "Stop hook feedback: please finish the remaining tasks"},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_remaining_tasks_acknowledged(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "REMAINING_TASKS_ACKNOWLEDGED"},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_user_message_with_autonomous_loop_sentinel(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "<<autonomous-loop-dynamic>>"},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_bare_request_interrupted(self, tmp_path: Path) -> None:
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": "[Request interrupted by user]"},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_bucket_with_only_junk_user_text(self, tmp_path: Path) -> None:
        lines = [
            orjson.dumps({
                "parentUuid": None, "isSidechain": False, "type": "user",
                "message": {"role": "user", "content": "please implement the thing"},
                "uuid": "u1", "timestamp": "2026-04-10T07:36:00.000Z",
                "sessionId": "s", "version": "2.1.92",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "type": "assistant",
                "message": {"model": "m", "content": [{"type": "text", "text": "ok"}]},
                "uuid": "a1", "timestamp": "2026-04-10T07:36:10.000Z",
                "sessionId": "s",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "isSidechain": False, "type": "user",
                "message": {"role": "user", "content": "Stop hook feedback: keep going"},
                "uuid": "u2", "timestamp": "2026-04-10T07:39:00.000Z",
                "sessionId": "s", "version": "2.1.92",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "type": "assistant",
                "message": {"model": "m", "content": [{"type": "text", "text": "continuing"}]},
                "uuid": "a2", "timestamp": "2026-04-10T07:39:10.000Z",
                "sessionId": "s",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "isSidechain": False, "type": "user",
                "message": {"role": "user", "content": "now add the tests"},
                "uuid": "u3", "timestamp": "2026-04-10T07:42:00.000Z",
                "sessionId": "s", "version": "2.1.92",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "type": "assistant",
                "message": {"model": "m", "content": [{"type": "text", "text": "will do"}]},
                "uuid": "a3", "timestamp": "2026-04-10T07:42:10.000Z",
                "sessionId": "s",
            }).decode(),
        ]
        f = tmp_path / "t.jsonl"
        f.write_text("\n".join(lines) + "\n")
        [parsed] = parse_paths([(f, 0.0)])
        indices = sorted(k.bucket_index for k in parsed.bucket_keys)
        assert indices == [0, 2]

    def test_drops_bucket_with_sub_min_user_chars(self, tmp_path: Path) -> None:
        lines = [
            orjson.dumps({
                "parentUuid": None, "isSidechain": False, "type": "user",
                "message": {"role": "user", "content": "ok"},
                "uuid": "u1", "timestamp": "2026-04-10T07:36:00.000Z",
                "sessionId": "s", "version": "2.1.92",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "type": "assistant",
                "message": {"model": "m", "content": [{"type": "text", "text": "ack"}]},
                "uuid": "a1", "timestamp": "2026-04-10T07:36:10.000Z",
                "sessionId": "s",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "isSidechain": False, "type": "user",
                "message": {"role": "user", "content": "actually fix the bug"},
                "uuid": "u2", "timestamp": "2026-04-10T07:39:00.000Z",
                "sessionId": "s", "version": "2.1.92",
            }).decode(),
            orjson.dumps({
                "parentUuid": None, "type": "assistant",
                "message": {"model": "m", "content": [{"type": "text", "text": "sure"}]},
                "uuid": "a2", "timestamp": "2026-04-10T07:39:10.000Z",
                "sessionId": "s",
            }).decode(),
        ]
        f = tmp_path / "t.jsonl"
        f.write_text("\n".join(lines) + "\n")
        [parsed] = parse_paths([(f, 0.0)])
        indices = sorted(k.bucket_index for k in parsed.bucket_keys)
        assert indices == [1]

    def test_parsed_transcript_includes_bucket_keys_and_mtime(self, tmp_path: Path) -> None:
        f = tmp_path / "t.jsonl"
        f.write_bytes(FIXTURE_PATH.read_bytes())

        async def collect() -> list[ParsedTranscript]:
            return [
                p async for p in TranscriptParser.stream_transcripts([(f, 123.0)])
            ]

        [parsed] = anyio.run(collect)
        assert parsed.mtime == 123.0
        expected_keys = {
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index)
            for b in ConversationBucketer.bucket_messages(list(parsed.messages))
        }
        assert set(parsed.bucket_keys) == expected_keys


@pytest.mark.usefixtures("backend")
class TestScanBucketKeys:
    def test_scan_bucket_keys_matches_full_parse(self, tmp_path: Path) -> None:
        f = tmp_path / "t.jsonl"
        f.write_bytes(FIXTURE_PATH.read_bytes())

        scanned = TranscriptParser.scan_bucket_keys(tmp_path)
        assert len(scanned) == 1
        _, _, keys = scanned[0]

        full_messages = parse_file(f)
        expected = {
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index)
            for b in ConversationBucketer.bucket_messages(full_messages)
        }
        assert set(keys) == expected


@pytest.mark.usefixtures("backend")
class TestConversationBucketer:
    def test_bucket_count(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        session_aaa_buckets = [
            b for b in buckets if b.session_id == SessionId("session-aaa")
        ]
        assert len(session_aaa_buckets) == 1

    def test_bucket_alignment(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        for bucket in buckets:
            assert bucket.bucket_start.second == 0
            assert bucket.bucket_start.microsecond == 0
            assert bucket.bucket_start.minute % BUCKET_MINUTES == 0

    def test_messages_in_correct_buckets(self) -> None:
        messages = parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_messages(messages)
        bucket = next(
            b for b in buckets if b.session_id == SessionId("session-aaa")
        )
        user_contents = [m.content for m in bucket.messages if m.role == "user"]
        assert "great, that fixed it! now can you add email validation too?" in user_contents

    def test_separate_sessions_bucketed_independently(self) -> None:
        messages = parse_file(FIXTURE_PATH)
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

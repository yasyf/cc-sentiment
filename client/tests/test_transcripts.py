from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import anyio
import orjson
import pytest

from cc_transcript.models import AssistantEvent, ToolUseBlock, ToolUseId, UserEvent
from cc_transcript.sentiment.buckets import ConversationEvent

from cc_sentiment.models import BucketKey, BucketMetrics, SessionId
from cc_sentiment.transcripts import (
    BUCKET_MINUTES,
    ConversationBucketer,
    ParsedTranscript,
    TranscriptParser,
)
from tests.helpers import make_assistant_event, make_user_event

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def parse_paths(paths: list[tuple[Path, float]]) -> list[ParsedTranscript]:
    async def run() -> list[ParsedTranscript]:
        return [p async for p in TranscriptParser.stream_transcripts(paths)]

    return anyio.run(run)


def parse_file(path: Path) -> list[ConversationEvent]:
    results = parse_paths([(path, 0.0)])
    assert len(results) == 1
    return list(results[0].events)


def parse_single_line(tmp_path: Path, line: str) -> ConversationEvent | None:
    f = tmp_path / "single.jsonl"
    f.write_text(line)
    events = parse_file(f)
    return events[0] if events else None


class TestStreamTranscripts:
    def test_skips_queue_operations(self) -> None:
        events = parse_file(FIXTURE_PATH)
        texts_present = {e.text for e in events}
        assert "queued task" not in texts_present

    def test_skips_sidechain_messages(self) -> None:
        events = parse_file(FIXTURE_PATH)
        uuids = {e.meta.uuid for e in events}
        assert "u3-sidechain" not in uuids

    def test_parses_user_events(self) -> None:
        events = parse_file(FIXTURE_PATH)
        user_events = [e for e in events if isinstance(e, UserEvent)]
        assert len(user_events) == 5
        assert user_events[0].text == "fix the login bug please, it keeps crashing on submit"

    def test_parses_assistant_text_blocks(self) -> None:
        events = parse_file(FIXTURE_PATH)
        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        assert len(assistant_events) == 5
        assert "thinking" not in assistant_events[0].text.lower()
        assert "I'll fix the login bug" in assistant_events[0].text

    def test_excludes_tool_use_blocks_from_text(self) -> None:
        events = parse_file(FIXTURE_PATH)
        assistant_events = [e for e in events if isinstance(e, AssistantEvent)]
        for event in assistant_events:
            assert "read_file" not in event.text

    def test_groups_by_session(self) -> None:
        events = parse_file(FIXTURE_PATH)
        sessions = {e.meta.session_id for e in events}
        assert sessions == {SessionId("session-aaa"), SessionId("session-bbb")}

    def test_preserves_timestamps(self) -> None:
        events = parse_file(FIXTURE_PATH)
        assert events[0].meta.timestamp == datetime(
            2026, 4, 10, 7, 36, 0, tzinfo=timezone.utc
        )

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
        event = parse_single_line(tmp_path, line)
        assert isinstance(event, UserEvent)
        assert event.text == "fix the failing test"
        assert event.meta.cc_version == "2.1.7"

    def test_allows_cli_entrypoint(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"hello"},'
            '"entrypoint":"cli",'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s","version":"2.1.92"}'
        )
        event = parse_single_line(tmp_path, line)
        assert event is not None
        assert event.text == "hello"

    def test_allows_user_message_without_version(self, tmp_path: Path) -> None:
        line = (
            '{"parentUuid":null,"isSidechain":false,"type":"user",'
            '"message":{"role":"user","content":"hello"},'
            '"uuid":"u","timestamp":"2026-04-10T07:36:00.000Z",'
            '"sessionId":"s"}'
        )
        event = parse_single_line(tmp_path, line)
        assert isinstance(event, UserEvent)
        assert event.text == "hello"
        assert event.meta.cc_version is None

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

    def test_keeps_user_message_quoting_interrupt_marker_mid_text(self, tmp_path: Path) -> None:
        content = "why did the log say [Request interrupted by user] earlier?"
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": content},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        event = parse_single_line(tmp_path, line)
        assert isinstance(event, UserEvent)
        assert event.text == content

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

    def test_drops_skill_directory_inject(self, tmp_path: Path) -> None:
        content = (
            "Base directory for this skill: /Users/me/.claude/plugins/cache/playwright-cli/playwright-cli/0.0.1/skills/playwright-cli\n"
            "\n"
            "# Browser Automation with playwright-cli\n"
            "## Quick start\n"
        )
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": content},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_drops_inline_markdown_menu_paste(self, tmp_path: Path) -> None:
        content = "Help me with: ### Keyboard ### Mouse ### Save as ### Tabs ### DevTools"
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": content},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        assert parse_single_line(tmp_path, line) is None

    def test_allows_legitimate_single_h3_heading(self, tmp_path: Path) -> None:
        content = "Reading the docs:\n### Setup\nWhat config goes here?"
        line = orjson.dumps({
            "parentUuid": None, "isSidechain": False, "type": "user",
            "message": {"role": "user", "content": content},
            "uuid": "u", "timestamp": "2026-04-10T07:36:00.000Z",
            "sessionId": "s", "version": "2.1.92",
        }).decode()
        event = parse_single_line(tmp_path, line)
        assert isinstance(event, UserEvent)
        assert "Setup" in event.text

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
            for b in ConversationBucketer.bucket_events(parsed.events)
        }
        assert set(parsed.bucket_keys) == expected_keys


class TestScanBucketKeys:
    async def test_scan_bucket_keys_matches_full_parse(self, tmp_path: Path) -> None:
        f = tmp_path / "t.jsonl"
        f.write_bytes(FIXTURE_PATH.read_bytes())

        scanned = await TranscriptParser.scan_bucket_keys(tmp_path)
        assert len(scanned) == 1
        _, _, keys = scanned[0]

        full_events = [
            e
            async for p in TranscriptParser.stream_transcripts([(f, 0.0)])
            for e in p.events
        ]
        expected = {
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index)
            for b in ConversationBucketer.bucket_events(full_events)
        }
        assert set(keys) == expected


class TestConversationBucketer:
    def test_bucket_count(self) -> None:
        events = parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_events(events)
        session_aaa_buckets = [
            b for b in buckets if b.session_id == SessionId("session-aaa")
        ]
        assert len(session_aaa_buckets) == 1

    def test_bucket_alignment(self) -> None:
        events = parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_events(events)
        for bucket in buckets:
            assert bucket.bucket_start.second == 0
            assert bucket.bucket_start.microsecond == 0
            assert bucket.bucket_start.minute % BUCKET_MINUTES == 0

    def test_events_in_correct_buckets(self) -> None:
        events = parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_events(events)
        bucket = next(
            b for b in buckets if b.session_id == SessionId("session-aaa")
        )
        user_texts = [e.text for e in bucket.events if isinstance(e, UserEvent)]
        assert "great, that fixed it! now can you add email validation too?" in user_texts

    def test_separate_sessions_bucketed_independently(self) -> None:
        events = parse_file(FIXTURE_PATH)
        buckets = ConversationBucketer.bucket_events(events)
        session_bbb_buckets = [
            b for b in buckets if b.session_id == SessionId("session-bbb")
        ]
        assert len(session_bbb_buckets) == 1

    def test_drops_single_user_turn_sessions(self) -> None:
        events = [
            make_user_event(
                "one-shot prompt",
                session_id="one-shot",
                timestamp=datetime(2026, 4, 10, 7, 36, 0, tzinfo=timezone.utc),
            ),
            make_assistant_event(
                "sure",
                session_id="one-shot",
                timestamp=datetime(2026, 4, 10, 7, 36, 30, tzinfo=timezone.utc),
            ),
        ]
        buckets = ConversationBucketer.bucket_events(events)
        assert [b for b in buckets if b.session_id == SessionId("one-shot")] == []


class TestBucketMetrics:
    @staticmethod
    def _user(cc_version: str = "2.1.92") -> UserEvent:
        return make_user_event(
            "hi",
            uuid="u",
            timestamp=datetime(2026, 4, 10, 7, 36, 0, tzinfo=timezone.utc),
            cc_version=cc_version,
        )

    @staticmethod
    def _input(name: str, file_path: str | None) -> dict[str, str]:
        match (name, file_path):
            case (_, None):
                return {}
            case ("Edit", path):
                return {"file_path": path, "old_string": "old", "new_string": "new"}
            case ("Write", path):
                return {"file_path": path, "content": "content"}
            case (_, path):
                return {"file_path": path}

    @classmethod
    def _assistant(
        cls,
        claude_model: str = "claude-sonnet-4-20250514",
        tool_uses: tuple[tuple[str, str | None], ...] = (),
    ) -> AssistantEvent:
        return make_assistant_event(
            "ok",
            uuid="a",
            timestamp=datetime(2026, 4, 10, 7, 36, 30, tzinfo=timezone.utc),
            model=claude_model,
            blocks=tuple(
                ToolUseBlock(id=ToolUseId(f"t{i}"), name=name, input=cls._input(name, file_path))
                for i, (name, file_path) in enumerate(tool_uses)
            ),
        )

    def test_requires_assistant(self) -> None:
        with pytest.raises(ValueError, match="both user and assistant"):
            BucketMetrics.from_events((self._user(),))

    def test_requires_user(self) -> None:
        with pytest.raises(ValueError, match="both user and assistant"):
            BucketMetrics.from_events((self._assistant(),))

    def test_requires_both_on_empty(self) -> None:
        with pytest.raises(ValueError, match="both user and assistant"):
            BucketMetrics.from_events(())

    def test_carries_last_user_cc_version_and_last_assistant_model(self) -> None:
        metrics = BucketMetrics.from_events((
            self._user(cc_version="2.1.0"),
            self._assistant(claude_model="claude-opus-4-6"),
            self._user(cc_version="2.1.92"),
            self._assistant(claude_model="claude-sonnet-4-20250514"),
        ))
        assert metrics.cc_version == "2.1.92"
        assert metrics.claude_model == "claude-sonnet-4-20250514"
        assert metrics.turn_count == 2

    def test_tool_calls_per_turn_and_subagent_count(self) -> None:
        metrics = BucketMetrics.from_events((
            self._user(),
            self._assistant(tool_uses=(
                ("Read", "/a.py"),
                ("Agent", None),
                ("Agent", None),
            )),
        ))
        assert metrics.tool_calls_per_turn == 3.0
        assert metrics.subagent_count == 2

    def test_write_edit_ratio(self) -> None:
        metrics = BucketMetrics.from_events((
            self._user(),
            self._assistant(tool_uses=(
                ("Write", "/a.py"),
                ("Write", "/b.py"),
                ("Edit", "/c.py"),
            )),
        ))
        assert metrics.write_edit_ratio == 2 / 3

    def test_write_edit_ratio_none_when_no_writes_or_edits(self) -> None:
        metrics = BucketMetrics.from_events((
            self._user(),
            self._assistant(tool_uses=(("Read", "/a.py"),)),
        ))
        assert metrics.write_edit_ratio is None

    def test_edits_without_prior_read_within_bucket(self) -> None:
        metrics = BucketMetrics.from_events((
            self._user(),
            self._assistant(tool_uses=(
                ("Read", "/a.py"),
                ("Edit", "/a.py"),
                ("Edit", "/b.py"),
            )),
        ))
        assert metrics.edits_without_prior_read_ratio == 0.5

    def test_edits_without_prior_read_uses_history(self) -> None:
        metrics = BucketMetrics.from_events_with_history(
            (
                self._user(),
                self._assistant(tool_uses=(("Edit", "/a.py"),)),
            ),
            frozenset({"/a.py"}),
        )
        assert metrics.edits_without_prior_read_ratio == 0.0

    def test_edits_without_prior_read_none_when_no_edits(self) -> None:
        metrics = BucketMetrics.from_events((
            self._user(),
            self._assistant(tool_uses=(("Read", "/a.py"),)),
        ))
        assert metrics.edits_without_prior_read_ratio is None

    def test_malformed_tool_input_degrades_instead_of_crashing(self) -> None:
        metrics = BucketMetrics.from_events((
            self._user(),
            make_assistant_event(
                "ok",
                uuid="a",
                timestamp=datetime(2026, 4, 10, 7, 36, 30, tzinfo=timezone.utc),
                blocks=(
                    ToolUseBlock(id=ToolUseId("t0"), name="Edit", input={"file_path": "/a.py"}),
                ),
            ),
        ))
        assert metrics.tool_counts == {"Edit": 1}
        assert metrics.edits_without_prior_read_ratio is None

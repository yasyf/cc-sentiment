from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import anyio
import orjson
import pytest

from cc_sentiment.debug import BucketHash, BucketLookup, BucketLookupResult
from cc_sentiment.models import (
    BucketIndex,
    PromptVersion,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from cc_sentiment.repo import Repository


def make_record(session_id: str, bucket_index: int, score: int) -> SentimentRecord:
    return SentimentRecord(
        time=datetime(2026, 4, 28, 10, 0, tzinfo=timezone.utc),
        conversation_id=SessionId(session_id),
        bucket_index=BucketIndex(bucket_index),
        sentiment_score=SentimentScore(score),
        prompt_version=PromptVersion("v1"),
        claude_model="claude-sonnet-4-20250514",
        client_version="0.0.1",
        read_edit_ratio=None,
        edits_without_prior_read_ratio=None,
        write_edit_ratio=None,
        tool_calls_per_turn=1.0,
        subagent_count=0,
        turn_count=2,
        thinking_present=False,
        thinking_chars=0,
        cc_version="2.1.92",
    )


def write_jsonl(path: Path, lines: list[dict]) -> None:
    path.write_bytes(b"\n".join(orjson.dumps(line) for line in lines) + b"\n")


def make_jsonl_payload(session_id: str, base_minute: int, count: int = 3) -> list[dict]:
    return [
        {
            "parentUuid": None,
            "isSidechain": False,
            "type": "user",
            "message": {"role": "user", "content": f"hello user {base_minute}"},
            "uuid": f"u-{session_id}-{base_minute}-1",
            "timestamp": f"2026-04-10T07:{base_minute:02d}:00.000Z",
            "sessionId": session_id,
            "version": "2.1.92",
        },
        {
            "parentUuid": "u1",
            "isSidechain": False,
            "type": "assistant",
            "message": {
                "model": "m",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"reply {base_minute}"}],
            },
            "uuid": f"u-{session_id}-{base_minute}-2",
            "timestamp": f"2026-04-10T07:{base_minute:02d}:05.000Z",
            "sessionId": session_id,
            "version": "2.1.92",
        },
        {
            "parentUuid": "u2",
            "isSidechain": False,
            "type": "user",
            "message": {"role": "user", "content": f"thanks user {base_minute}"},
            "uuid": f"u-{session_id}-{base_minute}-3",
            "timestamp": f"2026-04-10T07:{base_minute:02d}:10.000Z",
            "sessionId": session_id,
            "version": "2.1.92",
        },
    ][:count]


class TestBucketHash:
    def test_deterministic(self) -> None:
        h1 = BucketHash.of(SessionId("abc"), BucketIndex(0))
        h2 = BucketHash.of(SessionId("abc"), BucketIndex(0))
        assert h1 == h2

    def test_length_is_eight(self) -> None:
        h = BucketHash.of(SessionId("abc"), BucketIndex(0))
        assert len(h) == 8

    def test_different_indices_yield_different_hashes(self) -> None:
        a = BucketHash.of(SessionId("abc"), BucketIndex(0))
        b = BucketHash.of(SessionId("abc"), BucketIndex(1))
        assert a != b

    def test_of_record_matches_of(self) -> None:
        rec = make_record("abc", 5, 4)
        assert BucketHash.of_record(rec) == BucketHash.of(rec.conversation_id, rec.bucket_index)


class TestBucketLookup:
    def test_returns_none_for_unknown_prefix(self, tmp_path: Path) -> None:
        with Repository.open(tmp_path / "records.db") as repo:
            result = anyio.run(BucketLookup.find, repo, "deadbeef")
        assert result is None

    def test_strips_leading_hash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from cc_sentiment.debug import TranscriptDiscovery
        monkeypatch.setattr(TranscriptDiscovery, "find_transcripts", staticmethod(lambda: []))

        with Repository.open(tmp_path / "records.db") as repo:
            rec = make_record("session-x", 0, 4)
            repo.save_records("/tmp/x.jsonl", 1.0, [rec])
            full_hash = BucketHash.of_record(rec)
            with_hash = anyio.run(BucketLookup.find, repo, f"#{full_hash}")
        assert with_hash is None

    def test_resolves_known_record_via_real_transcript(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cc_sentiment.debug import TranscriptDiscovery

        session_id = "session-resolve"
        jsonl = tmp_path / "transcripts" / f"{session_id}.jsonl"
        jsonl.parent.mkdir(parents=True)
        write_jsonl(jsonl, make_jsonl_payload(session_id, base_minute=36))

        monkeypatch.setattr(
            TranscriptDiscovery,
            "find_transcripts",
            staticmethod(lambda: [jsonl]),
        )

        with Repository.open(tmp_path / "records.db") as repo:
            rec = make_record(session_id, 0, 4)
            repo.save_records(str(jsonl), jsonl.stat().st_mtime, [rec])
            target = BucketHash.of_record(rec)
            result = anyio.run(BucketLookup.find, repo, target)
        assert isinstance(result, BucketLookupResult)
        assert result.record.conversation_id == session_id
        assert result.transcript_path == jsonl
        assert any(m.role == "user" for m in result.bucket.messages)

    def test_distinguishes_long_prefix_among_collisions(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cc_sentiment.debug import TranscriptDiscovery

        sessions = ["alpha-session", "beta-session"]
        jsonls: list[Path] = []
        for sid in sessions:
            p = tmp_path / "transcripts" / f"{sid}.jsonl"
            p.parent.mkdir(parents=True, exist_ok=True)
            write_jsonl(p, make_jsonl_payload(sid, base_minute=36))
            jsonls.append(p)
        monkeypatch.setattr(
            TranscriptDiscovery,
            "find_transcripts",
            staticmethod(lambda: list(jsonls)),
        )

        with Repository.open(tmp_path / "records.db") as repo:
            recs = [make_record(sid, 0, 4) for sid in sessions]
            for rec, jsonl in zip(recs, jsonls):
                repo.save_records(str(jsonl), jsonl.stat().st_mtime, [rec])
            target = BucketHash.of_record(recs[1])
            result = anyio.run(BucketLookup.find, repo, target)
        assert result is not None
        assert result.record.conversation_id == sessions[1]


class TestBucketLookupFormat:
    def test_format_includes_score_and_path(self, tmp_path: Path) -> None:
        from cc_sentiment.models import (
            AssistantMessage,
            ConversationBucket,
            UserMessage,
        )
        rec = make_record("session-fmt", 0, 4)
        bucket = ConversationBucket(
            session_id=rec.conversation_id,
            bucket_index=rec.bucket_index,
            bucket_start=rec.time,
            messages=(
                UserMessage(
                    content="hi",
                    timestamp=rec.time,
                    session_id=rec.conversation_id,
                    uuid="u1",
                    tool_calls=(),
                    thinking_chars=0,
                    cc_version="2.1.92",
                ),
                AssistantMessage(
                    content="ok",
                    timestamp=rec.time,
                    session_id=rec.conversation_id,
                    uuid="u2",
                    tool_calls=(),
                    thinking_chars=0,
                    claude_model="m",
                ),
            ),
        )
        out = BucketLookup.format(
            BucketLookupResult(record=rec, bucket=bucket, transcript_path=tmp_path / "x.jsonl")
        )
        assert f"score={int(rec.sentiment_score)}" in out
        assert "session=session-fmt" in out
        assert "x.jsonl" in out
        assert "USER" in out
        assert "AI" in out

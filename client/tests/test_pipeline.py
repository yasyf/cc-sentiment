from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from cc_sentiment.models import (
    BucketIndex,
    BucketKey,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from cc_sentiment.pipeline import Pipeline, ScannedTranscript, ScanResult
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import ParsedTranscript, TranscriptParser
from tests.helpers import make_parsed, make_record

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def parse_fixture() -> ParsedTranscript:
    async def run() -> list[ParsedTranscript]:
        return [p async for p in TranscriptParser.stream_transcripts([(FIXTURE_PATH, 1.0)])]

    [parsed] = anyio.run(run)
    return parsed


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository.open(tmp_path / "records.db")
    try:
        yield r
    finally:
        r.close()


class TestScan:
    def test_finds_new_files(self, repo: Repository) -> None:
        fake_path = Path("/fake/transcript.jsonl")
        key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))
        with patch(
            "cc_sentiment.pipeline.TranscriptParser.scan_bucket_keys",
            return_value=[(str(fake_path), 100.0, [key])],
        ):
            result = anyio.run(lambda: Pipeline.scan(repo))
        assert len(result.transcripts) == 1
        assert result.transcripts[0].path == fake_path
        assert result.transcripts[0].mtime == 100.0
        assert result.transcripts[0].new_bucket_keys == (key,)
        assert result.total_new_buckets == 1

    def test_skips_fully_scored_files(self, repo: Repository) -> None:
        key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))
        repo.save_records("/fake/transcript.jsonl", 100.0, [
            make_record(session_id="s1", bucket_index=0)
        ])
        with patch(
            "cc_sentiment.pipeline.TranscriptParser.scan_bucket_keys",
            return_value=[("/fake/transcript.jsonl", 100.0, [key])],
        ):
            result = anyio.run(lambda: Pipeline.scan(repo))
        assert result.transcripts == ()

    def test_includes_partially_new_files(self, repo: Repository) -> None:
        scored_key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))
        new_key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(1))
        repo.save_records("/fake/transcript.jsonl", 100.0, [
            make_record(session_id="s1", bucket_index=0)
        ])
        with patch(
            "cc_sentiment.pipeline.TranscriptParser.scan_bucket_keys",
            return_value=[("/fake/transcript.jsonl", 200.0, [scored_key, new_key])],
        ):
            result = anyio.run(lambda: Pipeline.scan(repo))
        assert len(result.transcripts) == 1
        assert result.transcripts[0].new_bucket_keys == (new_key,)


@pytest.fixture
def fixture_parsed() -> ParsedTranscript:
    return parse_fixture()


class TestScoreTranscript:
    def test_empty_messages_returns_empty(self) -> None:
        parsed = ParsedTranscript(path=Path("/empty.jsonl"), mtime=0.0, bucket_keys=(), messages=())
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[])

        async def run() -> list[SentimentRecord]:
            return await Pipeline.score_transcript(parsed, classifier)

        result = anyio.run(run)
        assert result == []
        classifier.score.assert_not_called()

    def test_correct_record_count(self, fixture_parsed: ParsedTranscript) -> None:
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 2)

        async def run() -> list[SentimentRecord]:
            return await Pipeline.score_transcript(fixture_parsed, classifier)

        result = anyio.run(run)
        assert len(result) == 2
        classifier.score.assert_called_once()


class TestBucketCaching:
    def test_skips_cached_buckets(self, fixture_parsed: ParsedTranscript) -> None:
        cached = frozenset({BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))})
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 4)

        async def run() -> list[SentimentRecord]:
            return await Pipeline.score_transcript(fixture_parsed, classifier, scored_buckets=cached)

        anyio.run(run)
        called_buckets = classifier.score.call_args[0][0]
        assert all(
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index) not in cached
            for b in called_buckets
        )

    def test_all_cached_returns_empty(self, fixture_parsed: ParsedTranscript) -> None:
        all_keys = frozenset(fixture_parsed.bucket_keys)
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 5)

        async def run() -> list[SentimentRecord]:
            return await Pipeline.score_transcript(fixture_parsed, classifier, scored_buckets=all_keys)

        result = anyio.run(run)
        assert result == []
        classifier.score.assert_not_called()

    def test_save_records_persists_bucket_keys(self, repo: Repository) -> None:
        record = make_record()
        repo.save_records("/fake.jsonl", 100.0, [record])

        scored = repo.scored_buckets_for("/fake.jsonl")
        assert BucketKey(session_id=SessionId("session-1"), bucket_index=BucketIndex(0)) in scored

    def test_save_records_merges_bucket_keys(self, repo: Repository) -> None:
        first = make_record(session_id="old", bucket_index=99)
        second = make_record()
        repo.save_records("/fake.jsonl", 50.0, [first])
        repo.save_records("/fake.jsonl", 100.0, [second])

        scored = repo.scored_buckets_for("/fake.jsonl")
        assert BucketKey(session_id=SessionId("old"), bucket_index=BucketIndex(99)) in scored
        assert BucketKey(session_id=SessionId("session-1"), bucket_index=BucketIndex(0)) in scored


class TestCrossBucketReadHistory:
    def test_read_in_earlier_bucket_counts_for_later_bucket(self, tmp_path: Path) -> None:
        path = tmp_path / "cross.jsonl"
        path.write_text(
            "\n".join([
                '{"parentUuid":null,"isSidechain":false,"type":"user","message":{"role":"user","content":"open it"},"uuid":"u1","timestamp":"2026-04-10T07:36:00.000Z","sessionId":"session-cross","version":"2.1.92"}',
                '{"parentUuid":"u1","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"reading"},{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"/a.py"}}]},"type":"assistant","uuid":"a1","timestamp":"2026-04-10T07:36:30.000Z","sessionId":"session-cross"}',
                '{"parentUuid":"a1","isSidechain":false,"type":"user","message":{"role":"user","content":"now edit it"},"uuid":"u2","timestamp":"2026-04-10T07:46:00.000Z","sessionId":"session-cross","version":"2.1.92"}',
                '{"parentUuid":"u2","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"editing"},{"type":"tool_use","id":"t2","name":"Edit","input":{"file_path":"/a.py"}}]},"type":"assistant","uuid":"a2","timestamp":"2026-04-10T07:46:30.000Z","sessionId":"session-cross"}',
            ])
        )

        async def load() -> list[ParsedTranscript]:
            return [p async for p in TranscriptParser.stream_transcripts([(path, 0.0)])]

        [parsed] = anyio.run(load)
        new_buckets, metrics_by_key = Pipeline.buckets_with_metrics(parsed.messages, frozenset())
        assert len(new_buckets) == 2
        bucket_1_key = BucketKey(session_id=SessionId("session-cross"), bucket_index=BucketIndex(3))
        assert metrics_by_key[bucket_1_key].edits_without_prior_read_ratio == 0.0

    def test_edit_without_any_prior_read_marked(self, tmp_path: Path) -> None:
        path = tmp_path / "noread.jsonl"
        path.write_text(
            "\n".join([
                '{"parentUuid":null,"isSidechain":false,"type":"user","message":{"role":"user","content":"edit"},"uuid":"u1","timestamp":"2026-04-10T07:36:00.000Z","sessionId":"session-x","version":"2.1.92"}',
                '{"parentUuid":"u1","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"k"},{"type":"tool_use","id":"t","name":"Edit","input":{"file_path":"/a.py"}}]},"type":"assistant","uuid":"a1","timestamp":"2026-04-10T07:36:30.000Z","sessionId":"session-x"}',
                '{"parentUuid":"a1","isSidechain":false,"type":"user","message":{"role":"user","content":"thanks"},"uuid":"u2","timestamp":"2026-04-10T07:37:00.000Z","sessionId":"session-x","version":"2.1.92"}',
                '{"parentUuid":"u2","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"ok"}]},"type":"assistant","uuid":"a2","timestamp":"2026-04-10T07:37:30.000Z","sessionId":"session-x"}',
            ])
        )

        async def load() -> list[ParsedTranscript]:
            return [p async for p in TranscriptParser.stream_transcripts([(path, 0.0)])]

        [parsed] = anyio.run(load)
        _, metrics_by_key = Pipeline.buckets_with_metrics(parsed.messages, frozenset())
        bucket_key = BucketKey(session_id=SessionId("session-x"), bucket_index=BucketIndex(0))
        assert metrics_by_key[bucket_key].edits_without_prior_read_ratio == 1.0


def _stub_stream(parsed_list: list[ParsedTranscript]):
    async def stream(paths, *, prefetch=None) -> AsyncIterator[ParsedTranscript]:
        for parsed in parsed_list:
            yield parsed

    return stream


class TestPipelineStateUpdate:
    def test_repo_updated_with_records(self, repo: Repository) -> None:
        record = make_record()
        parsed = make_parsed(Path("/fake.jsonl"), (), mtime=100.0)
        scan_result = ScanResult(
            transcripts=(
                ScannedTranscript(
                    path=Path("/fake.jsonl"),
                    mtime=100.0,
                    new_bucket_keys=(BucketKey(
                        session_id=SessionId("session-1"), bucket_index=BucketIndex(0)
                    ),),
                ),
            ),
            scored_by_path={},
        )

        mock_classifier = MagicMock()
        mock_classifier.score = AsyncMock(return_value=[])
        mock_classifier.close = AsyncMock()

        with patch.object(TranscriptParser, "stream_transcripts", new=_stub_stream([parsed])), \
             patch.object(Pipeline, "score_transcript", new_callable=AsyncMock, return_value=[record]):

            async def do_run() -> list[SentimentRecord]:
                return await Pipeline.run(repo, scan_result, classifier=mock_classifier)

            result = anyio.run(do_run)

        all_records = repo.all_records()
        assert len(all_records) == 1
        assert all_records[0].conversation_id == SessionId("session-1")
        pending = repo.pending_records()
        assert len(pending) == 1
        assert pending[0] == record
        assert str(Path("/fake.jsonl")) in repo.file_mtimes()
        assert len(result) == 1


class TestOnBucketPlumbing:
    def test_score_transcript_passes_on_bucket_to_classifier(self, fixture_parsed: ParsedTranscript) -> None:
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 2)

        cb = MagicMock()

        async def run() -> None:
            await Pipeline.score_transcript(fixture_parsed, classifier, on_bucket=cb)

        anyio.run(run)
        _, kwargs = classifier.score.call_args
        assert kwargs.get("on_progress") is cb

    def test_run_threads_on_bucket_through_to_score_transcript(self, repo: Repository) -> None:
        cb = MagicMock()
        captured: dict[str, object] = {}

        async def fake_score(parsed, classifier, scored_buckets=frozenset(), **kwargs):
            captured["on_bucket"] = kwargs.get("on_bucket")
            return []

        parsed = make_parsed(FIXTURE_PATH, (), mtime=1.0)
        classifier = MagicMock()
        classifier.close = AsyncMock()
        scan_result = ScanResult(
            transcripts=(
                ScannedTranscript(
                    path=FIXTURE_PATH,
                    mtime=1.0,
                    new_bucket_keys=(BucketKey(
                        session_id=SessionId("session-aaa"), bucket_index=BucketIndex(0)
                    ),),
                ),
            ),
            scored_by_path={},
        )

        async def run() -> None:
            with patch.object(TranscriptParser, "stream_transcripts", new=_stub_stream([parsed])), \
                 patch.object(Pipeline, "score_transcript", new=fake_score):
                await Pipeline.run(repo, scan_result, classifier=classifier, on_bucket=cb)

        anyio.run(run)
        assert captured["on_bucket"] is cb

from __future__ import annotations

import sys
from collections.abc import Iterator
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
from cc_sentiment.pipeline import Pipeline
from cc_sentiment.repo import Repository
from tests.helpers import make_record

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository.open(tmp_path / "records.db")
    try:
        yield r
    finally:
        r.close()


class TestDiscoverNewTranscripts:
    def test_finds_new_files(self, repo: Repository) -> None:
        fake_path = Path("/fake/transcript.jsonl")
        with patch("cc_sentiment.transcripts.TranscriptDiscovery.find_transcripts", return_value=[fake_path]), \
             patch("cc_sentiment.transcripts.TranscriptDiscovery.transcript_mtime", return_value=100.0):
            result = Pipeline.discover_new_transcripts(repo)
        assert len(result) == 1
        assert result[0] == (fake_path, 100.0)

    def test_skips_unchanged_files(self, repo: Repository) -> None:
        repo.save_records("/fake/transcript.jsonl", 100.0, [])
        with patch("cc_sentiment.transcripts.TranscriptDiscovery.find_transcripts", return_value=[Path("/fake/transcript.jsonl")]), \
             patch("cc_sentiment.transcripts.TranscriptDiscovery.transcript_mtime", return_value=100.0):
            result = Pipeline.discover_new_transcripts(repo)
        assert result == []

    def test_reprocesses_updated_files(self, repo: Repository) -> None:
        repo.save_records("/fake/transcript.jsonl", 100.0, [])
        with patch("cc_sentiment.transcripts.TranscriptDiscovery.find_transcripts", return_value=[Path("/fake/transcript.jsonl")]), \
             patch("cc_sentiment.transcripts.TranscriptDiscovery.transcript_mtime", return_value=200.0):
            result = Pipeline.discover_new_transcripts(repo)
        assert len(result) == 1
        assert result[0][1] == 200.0


class TestProcessTranscript:
    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("")
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[])
        classifier.close = AsyncMock()

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(empty_file, classifier)

        result = anyio.run(run)
        assert result == []
        classifier.score.assert_not_called()

    def test_correct_record_count(self) -> None:
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 2)
        classifier.close = AsyncMock()

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(FIXTURE_PATH, classifier)

        result = anyio.run(run)
        assert len(result) == 2
        classifier.score.assert_called_once()


class TestBucketCaching:
    def test_skips_cached_buckets(self) -> None:
        cached = frozenset({BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))})
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 4)
        classifier.close = AsyncMock()

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(FIXTURE_PATH, classifier, scored_buckets=cached)

        anyio.run(run)
        called_buckets = classifier.score.call_args[0][0]
        assert all(
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index) not in cached
            for b in called_buckets
        )

    def test_all_cached_returns_empty(self) -> None:
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 5)
        classifier.close = AsyncMock()

        async def get_all_keys() -> frozenset[BucketKey]:
            from cc_sentiment.transcripts import ConversationBucketer, TranscriptParser
            messages = TranscriptParser.parse_file(FIXTURE_PATH)
            buckets = ConversationBucketer.bucket_messages(messages)
            return frozenset(BucketKey(session_id=b.session_id, bucket_index=b.bucket_index) for b in buckets)

        all_keys = anyio.run(get_all_keys)

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(FIXTURE_PATH, classifier, scored_buckets=all_keys)

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

        new_buckets, metrics_by_key = Pipeline._parse_buckets_with_metrics(path, frozenset())
        assert len(new_buckets) == 2
        bucket_1_key = BucketKey(session_id=SessionId("session-cross"), bucket_index=BucketIndex(2))
        assert metrics_by_key[bucket_1_key].edits_without_prior_read_ratio == 0.0

    def test_edit_without_any_prior_read_marked(self, tmp_path: Path) -> None:
        path = tmp_path / "noread.jsonl"
        path.write_text(
            '{"parentUuid":null,"isSidechain":false,"type":"user","message":{"role":"user","content":"edit"},"uuid":"u1","timestamp":"2026-04-10T07:36:00.000Z","sessionId":"session-x","version":"2.1.92"}\n'
            '{"parentUuid":"u1","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"k"},{"type":"tool_use","id":"t","name":"Edit","input":{"file_path":"/a.py"}}]},"type":"assistant","uuid":"a1","timestamp":"2026-04-10T07:36:30.000Z","sessionId":"session-x"}'
        )

        _, metrics_by_key = Pipeline._parse_buckets_with_metrics(path, frozenset())
        bucket_key = BucketKey(session_id=SessionId("session-x"), bucket_index=BucketIndex(0))
        assert metrics_by_key[bucket_key].edits_without_prior_read_ratio == 1.0


class TestPipelineStateUpdate:
    def test_repo_updated_with_records(self, repo: Repository) -> None:
        record = make_record()

        mock_classifier = MagicMock()
        mock_classifier.score = AsyncMock(return_value=[])
        mock_classifier.close = AsyncMock()

        mock_sentiment_mod = MagicMock()
        mock_sentiment_mod.SentimentClassifier.return_value = mock_classifier

        with patch.dict(sys.modules, {"cc_sentiment.sentiment": mock_sentiment_mod}), \
             patch.object(Pipeline, "discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 100.0)]), \
             patch.object(Pipeline, "process_transcript", new_callable=AsyncMock, return_value=[record]):

            async def do_run() -> list[SentimentRecord]:
                return await Pipeline.run(repo, engine="mlx")

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
    def test_process_transcript_passes_on_bucket_to_classifier(self) -> None:
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 2)
        classifier.close = AsyncMock()

        cb = MagicMock()

        async def run() -> None:
            await Pipeline.process_transcript(FIXTURE_PATH, classifier, on_bucket=cb)

        anyio.run(run)
        _, kwargs = classifier.score.call_args
        assert kwargs.get("on_progress") is cb

    def test_run_threads_on_bucket_through_to_process_transcript(self, repo: Repository) -> None:
        cb = MagicMock()
        captured: dict[str, object] = {}

        async def fake_process(path, classifier, scored_buckets=frozenset(), on_bucket=None):
            captured["on_bucket"] = on_bucket
            return []

        classifier = MagicMock()
        classifier.close = AsyncMock()

        async def run() -> None:
            with patch("cc_sentiment.pipeline.build_engine", AsyncMock(return_value=classifier)), \
                 patch.object(Pipeline, "process_transcript", new=fake_process):
                await Pipeline.run(
                    repo,
                    engine="omlx",
                    new_transcripts=[(FIXTURE_PATH, 1.0)],
                    on_bucket=cb,
                )

        anyio.run(run)
        assert captured["on_bucket"] is cb

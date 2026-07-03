from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from datetime import datetime, timezone

from cc_sentiment.models import (
    BucketIndex,
    BucketKey,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from cc_sentiment.pipeline import Pipeline, ScanCache, ScannedTranscript, ScanResult
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import ParsedTranscript, TranscriptParser
from tests.helpers import make_assistant_event, make_parsed, make_record, make_user_event

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def parse_fixture() -> ParsedTranscript:
    async def run() -> list[ParsedTranscript]:
        return [p async for p in TranscriptParser.stream_transcripts([(FIXTURE_PATH, 1.0)])]

    [parsed] = anyio.run(run)
    return parsed


@pytest.fixture
async def repo(tmp_path: Path) -> AsyncIterator[Repository]:
    r = await Repository.open(tmp_path / "records.db")
    try:
        yield r
    finally:
        await r.close()


class TestScan:
    async def test_finds_new_files(self, repo: Repository) -> None:
        fake_path = Path("/fake/transcript.jsonl")
        key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))
        with patch(
            "cc_sentiment.pipeline.TranscriptParser.scan_bucket_keys",
            new_callable=AsyncMock,
            return_value=[(str(fake_path), 100.0, [key])],
        ):
            result = await Pipeline.scan(repo)
        assert len(result.transcripts) == 1
        assert result.transcripts[0].path == fake_path
        assert result.transcripts[0].mtime == 100.0
        assert result.transcripts[0].new_bucket_keys == (key,)
        assert result.total_new_buckets == 1

    async def test_skips_fully_scored_files(self, repo: Repository) -> None:
        key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))
        await repo.save_records("/fake/transcript.jsonl", 100.0, [
            make_record(session_id="s1", bucket_index=0)
        ])
        with patch(
            "cc_sentiment.pipeline.TranscriptParser.scan_bucket_keys",
            new_callable=AsyncMock,
            return_value=[("/fake/transcript.jsonl", 100.0, [key])],
        ):
            result = await Pipeline.scan(repo)
        assert result.transcripts == ()

    async def test_includes_partially_new_files(self, repo: Repository) -> None:
        scored_key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))
        new_key = BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(1))
        await repo.save_records("/fake/transcript.jsonl", 100.0, [
            make_record(session_id="s1", bucket_index=0)
        ])
        with patch(
            "cc_sentiment.pipeline.TranscriptParser.scan_bucket_keys",
            new_callable=AsyncMock,
            return_value=[("/fake/transcript.jsonl", 200.0, [scored_key, new_key])],
        ):
            result = await Pipeline.scan(repo)
        assert len(result.transcripts) == 1
        assert result.transcripts[0].new_bucket_keys == (new_key,)


@pytest.fixture
def fixture_parsed() -> ParsedTranscript:
    return parse_fixture()


class TestScoreTranscript:
    def test_empty_events_returns_empty(self) -> None:
        parsed = ParsedTranscript(path=Path("/empty.jsonl"), mtime=0.0, bucket_keys=(), events=())
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

    async def test_save_records_persists_bucket_keys(self, repo: Repository) -> None:
        record = make_record()
        await repo.save_records("/fake.jsonl", 100.0, [record])

        scored = await repo.scored_buckets_for("/fake.jsonl")
        assert BucketKey(session_id=SessionId("session-1"), bucket_index=BucketIndex(0)) in scored

    async def test_save_records_merges_bucket_keys(self, repo: Repository) -> None:
        first = make_record(session_id="old", bucket_index=99)
        second = make_record()
        await repo.save_records("/fake.jsonl", 50.0, [first])
        await repo.save_records("/fake.jsonl", 100.0, [second])

        scored = await repo.scored_buckets_for("/fake.jsonl")
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
                '{"parentUuid":"u2","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"editing"},{"type":"tool_use","id":"t2","name":"Edit","input":{"file_path":"/a.py","old_string":"x","new_string":"y"}}]},"type":"assistant","uuid":"a2","timestamp":"2026-04-10T07:46:30.000Z","sessionId":"session-cross"}',
            ])
        )

        async def load() -> list[ParsedTranscript]:
            return [p async for p in TranscriptParser.stream_transcripts([(path, 0.0)])]

        [parsed] = anyio.run(load)
        new_buckets, metrics_by_key = Pipeline.buckets_with_metrics(parsed.events, frozenset())
        assert len(new_buckets) == 2
        bucket_1_key = BucketKey(session_id=SessionId("session-cross"), bucket_index=BucketIndex(3))
        assert metrics_by_key[bucket_1_key].edits_without_prior_read_ratio == 0.0

    def test_edit_without_any_prior_read_marked(self, tmp_path: Path) -> None:
        path = tmp_path / "noread.jsonl"
        path.write_text(
            "\n".join([
                '{"parentUuid":null,"isSidechain":false,"type":"user","message":{"role":"user","content":"edit"},"uuid":"u1","timestamp":"2026-04-10T07:36:00.000Z","sessionId":"session-x","version":"2.1.92"}',
                '{"parentUuid":"u1","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"k"},{"type":"tool_use","id":"t","name":"Edit","input":{"file_path":"/a.py","old_string":"x","new_string":"y"}}]},"type":"assistant","uuid":"a1","timestamp":"2026-04-10T07:36:30.000Z","sessionId":"session-x"}',
                '{"parentUuid":"a1","isSidechain":false,"type":"user","message":{"role":"user","content":"thanks"},"uuid":"u2","timestamp":"2026-04-10T07:37:00.000Z","sessionId":"session-x","version":"2.1.92"}',
                '{"parentUuid":"u2","message":{"model":"claude-sonnet-4-20250514","type":"message","role":"assistant","content":[{"type":"text","text":"ok"}]},"type":"assistant","uuid":"a2","timestamp":"2026-04-10T07:37:30.000Z","sessionId":"session-x"}',
            ])
        )

        async def load() -> list[ParsedTranscript]:
            return [p async for p in TranscriptParser.stream_transcripts([(path, 0.0)])]

        [parsed] = anyio.run(load)
        _, metrics_by_key = Pipeline.buckets_with_metrics(parsed.events, frozenset())
        bucket_key = BucketKey(session_id=SessionId("session-x"), bucket_index=BucketIndex(0))
        assert metrics_by_key[bucket_key].edits_without_prior_read_ratio == 1.0


def _stub_stream(parsed_list: list[ParsedTranscript]):
    async def stream(paths, *, prefetch=None) -> AsyncIterator[ParsedTranscript]:
        for parsed in parsed_list:
            yield parsed

    return stream


class TestPipelineStateUpdate:
    async def test_repo_updated_with_records(self, repo: Repository) -> None:
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
            result = await Pipeline.run(repo, scan_result, classifier=mock_classifier)

        all_records = await repo.all_records()
        assert len(all_records) == 1
        assert all_records[0].conversation_id == SessionId("session-1")
        pending = await repo.pending_records()
        assert len(pending) == 1
        assert pending[0] == record
        assert str(Path("/fake.jsonl")) in await repo.file_mtimes()
        assert len(result) == 1


class TestOnFrustration:
    @staticmethod
    def make_pair(user_text: str) -> list:
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            make_user_event(user_text, uuid="u1", session_id="s1", timestamp=ts),
            make_assistant_event("ack", uuid="a1", session_id="s1", timestamp=ts),
            make_user_event("ok thanks", uuid="u2", session_id="s1", timestamp=ts),
            make_assistant_event("np", uuid="a2", session_id="s1", timestamp=ts),
        ]

    @pytest.mark.parametrize("score", [SentimentScore(s) for s in (1, 2, 3, 4, 5)])
    def test_emits_for_any_score_when_profanity_present(
        self, score: SentimentScore
    ) -> None:
        parsed = make_parsed(Path("/p.jsonl"), self.make_pair("shit, that's not right"))
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[score])

        captured: list[list[str]] = []

        async def run() -> None:
            await Pipeline.score_transcript(
                parsed, classifier,
                on_frustration=captured.append,
            )

        anyio.run(run)
        assert captured == [["shit"]]

    def test_no_emission_when_no_profanity(self) -> None:
        parsed = make_parsed(Path("/p.jsonl"), self.make_pair("everything fine"))
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(1)])

        captured: list[list[str]] = []

        async def run() -> None:
            await Pipeline.score_transcript(
                parsed, classifier,
                on_frustration=captured.append,
            )

        anyio.run(run)
        assert captured == []


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

    async def test_run_threads_on_bucket_through_to_score_transcript(self, repo: Repository) -> None:
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

        with patch.object(TranscriptParser, "stream_transcripts", new=_stub_stream([parsed])), \
             patch.object(Pipeline, "score_transcript", new=fake_score):
            await Pipeline.run(repo, scan_result, classifier=classifier, on_bucket=cb)
        assert captured["on_bucket"] is cb


class TestScanCache:
    def _scan_result(self) -> ScanResult:
        return ScanResult(transcripts=(), scored_by_path={})

    async def test_first_get_calls_scan_once(self, repo: Repository) -> None:
        result = self._scan_result()
        cache = ScanCache(repo)
        with patch.object(Pipeline, "scan", AsyncMock(return_value=result)) as scan:
            got = await cache.get()
        assert got is result
        assert scan.call_count == 1

    async def test_repeat_get_uses_cache(self, repo: Repository) -> None:
        result = self._scan_result()
        cache = ScanCache(repo)
        with patch.object(Pipeline, "scan", AsyncMock(return_value=result)) as scan:
            first, second = await cache.get(), await cache.get()
        assert first is second is result
        assert scan.call_count == 1

    async def test_invalidate_triggers_rescan(self, repo: Repository) -> None:
        a, b = self._scan_result(), self._scan_result()
        cache = ScanCache(repo)
        with patch.object(Pipeline, "scan", AsyncMock(side_effect=[a, b])) as scan:
            first = await cache.get()
            cache.invalidate()
            second = await cache.get()
        assert first is a
        assert second is b
        assert scan.call_count == 2

    async def test_concurrent_get_runs_one_scan(self, repo: Repository) -> None:
        result = self._scan_result()
        cache = ScanCache(repo)
        scan_calls = 0

        async def slow_scan(_: Repository) -> ScanResult:
            nonlocal scan_calls
            scan_calls += 1
            await anyio.sleep(0.01)
            return result

        with patch.object(Pipeline, "scan", new=slow_scan):
            results: list[ScanResult] = []

            async def collect() -> None:
                results.append(await cache.get())

            async with anyio.create_task_group() as tg:
                for _ in range(5):
                    tg.start_soon(collect)
        assert scan_calls == 1
        assert all(r is result for r in results)

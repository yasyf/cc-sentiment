from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from importlib.util import find_spec
from unittest.mock import MagicMock, patch

import pytest

from cc_sentiment.text import (
    MAX_CONVERSATION_CHARS,
    build_bucket_messages,
    build_bucket_user_content,
    build_user_content,
    extract_score,
    format_conversation,
)
from cc_transcript.sentiment.buckets import ConversationEvent

from cc_sentiment.models import (
    BucketIndex,
    ConversationBucket,
    SentimentScore,
    SessionId,
)
from tests.helpers import make_assistant_event, make_user_event

MLX_AVAILABLE: bool = (
    find_spec("mlx_lm") is not None and sys.platform == "darwin"
)


def _make_event(role: str, content: str, i: int) -> ConversationEvent:
    timestamp = datetime(2026, 4, 10, 7, 35, i, tzinfo=timezone.utc)
    match role:
        case "user":
            return make_user_event(
                content, uuid=f"uuid-{i}", session_id="test-session", timestamp=timestamp
            )
        case "assistant":
            return make_assistant_event(
                content, uuid=f"uuid-{i}", session_id="test-session", timestamp=timestamp
            )
        case _:
            raise ValueError(f"unknown role: {role}")


def make_bucket(
    messages: list[tuple[str, str]] | None = None,
) -> ConversationBucket:
    msgs = messages or [("user", "this is great!")]
    return ConversationBucket(
        session_id=SessionId("test-session"),
        bucket_index=BucketIndex(0),
        bucket_start=datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
        events=tuple(
            _make_event(role, content, i) for i, (role, content) in enumerate(msgs)
        ),
    )


class TestScoreExtraction:
    def test_single_digit(self) -> None:
        assert extract_score("4") == SentimentScore(4)

    def test_digit_in_text(self) -> None:
        assert extract_score("Score: 2") == SentimentScore(2)

    def test_valid_json(self) -> None:
        assert extract_score('{"score": 4, "reason": "happy developer"}') == SentimentScore(4)

    def test_unparseable_raises(self) -> None:
        with pytest.raises(ValueError, match="Could not extract score"):
            extract_score("I cannot determine the sentiment")

    def test_score_boundaries(self) -> None:
        assert extract_score("1") == SentimentScore(1)
        assert extract_score("5") == SentimentScore(5)



class TestConversationFormatting:
    def test_format_user_message(self) -> None:
        bucket = make_bucket([("user", "fix the bug")])
        text = format_conversation(bucket)
        assert "DEVELOPER: fix the bug" in text

    def test_format_assistant_message(self) -> None:
        bucket = make_bucket([("assistant", "I'll fix it")])
        text = format_conversation(bucket)
        assert "AI: I'll fix it" in text

    def test_format_mixed_conversation(self) -> None:
        bucket = make_bucket([
            ("user", "fix the bug"),
            ("assistant", "I'll fix it"),
            ("user", "thanks!"),
        ])
        text = format_conversation(bucket)
        lines = text.strip().splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("DEVELOPER:")
        assert lines[1].startswith("AI:")
        assert lines[2].startswith("DEVELOPER:")

    def test_truncates_long_conversations(self) -> None:
        long_content = "x" * (MAX_CONVERSATION_CHARS + 1000)
        bucket = make_bucket([("user", long_content)])
        text = format_conversation(bucket)
        assert text.endswith("\n[... truncated]")
        assert len(text) == MAX_CONVERSATION_CHARS + len("\n[... truncated]")


class TestBuildUserContent:
    def test_flat_text(self) -> None:
        assert build_user_content("continue") == "CONVERSATION:\nDEVELOPER: continue"

    def test_strips_whitespace(self) -> None:
        assert build_user_content("  continue  ") == "CONVERSATION:\nDEVELOPER: continue"

    def test_preserves_role_marked_text(self) -> None:
        text = "DEVELOPER: build it\nAI: done\nDEVELOPER: continue"
        assert build_user_content(text) == f"CONVERSATION:\n{text}"

    def test_role_marked_text_starting_with_ai(self) -> None:
        text = "AI: starting work\nDEVELOPER: continue"
        assert build_user_content(text) == f"CONVERSATION:\n{text}"

    def test_matches_bucket_helper_for_single_user(self) -> None:
        bucket = make_bucket([("user", "continue")])
        assert build_user_content(format_conversation(bucket)) == build_bucket_user_content(bucket)

    def test_matches_bucket_helper_for_multi_turn(self) -> None:
        bucket = make_bucket([
            ("user", "build it"),
            ("assistant", "done"),
            ("user", "continue"),
        ])
        assert build_user_content(format_conversation(bucket)) == build_bucket_user_content(bucket)

    def test_no_double_developer_prefix_on_multi_turn(self) -> None:
        bucket = make_bucket([
            ("user", "build it"),
            ("assistant", "done"),
            ("user", "continue"),
        ])
        result = build_user_content(format_conversation(bucket))
        assert "DEVELOPER: DEVELOPER:" not in result
        assert result.count("CONVERSATION:") == 1


class TestBuildBucketMessages:
    def test_inference_path_byte_equality_with_text_helper(self) -> None:
        bucket = make_bucket([
            ("user", "build it"),
            ("assistant", "done"),
            ("user", "continue"),
        ])
        from cc_sentiment.text import build_prefix_messages

        inf = build_bucket_messages(bucket)
        from_text = [
            *build_prefix_messages(),
            {"role": "user", "content": build_user_content(format_conversation(bucket))},
        ]
        assert inf == from_text


class TestScoreMessagesSorting:
    @staticmethod
    def make_classifier(batch_size: int = 2):
        from spawnllm.mlx import MlxEngine

        from cc_sentiment.sentiment import SentimentClassifier

        classifier = SentimentClassifier.__new__(SentimentClassifier)
        classifier._engine = MlxEngine.__new__(MlxEngine)
        classifier._engine._batch_size = batch_size
        return classifier

    async def test_score_messages_sorts_by_last_content_length(self) -> None:
        from spawnllm.mlx import MlxEngine

        message_lists = [
            [{"role": "user", "content": "longest" * 100}],   # idx 0, very long
            [{"role": "user", "content": "short"}],            # idx 1, shortest
            [{"role": "user", "content": "medium" * 30}],      # idx 2, middle
        ]
        seen_chunks: list[list[str]] = []

        def fake_generate_chunk(self, chunk, max_tokens):
            seen_chunks.append([m[-1]["content"] for m in chunk])
            return [str(len(m[-1]["content"])) for m in chunk]

        async def fake_submit(self, fn, *args):
            return fn(*args)

        classifier = self.make_classifier()
        with patch.object(MlxEngine, "_generate_chunk", fake_generate_chunk), \
             patch.object(MlxEngine, "submit", fake_submit):
            responses = await classifier.score_messages(message_lists, on_progress=lambda n: None)

        flat_seen = [c for chunk in seen_chunks for c in chunk]
        assert flat_seen == sorted(flat_seen, key=len)
        assert responses == [str(len(m[-1]["content"])) for m in message_lists]

    async def test_score_messages_preserves_original_order(self) -> None:
        from spawnllm.mlx import MlxEngine

        message_lists = [
            [{"role": "user", "content": "x" * n}] for n in (50, 5, 200, 1, 30)
        ]

        def fake_generate_chunk(self, chunk, max_tokens):
            return [str(len(m[-1]["content"])) for m in chunk]

        async def fake_submit(self, fn, *args):
            return fn(*args)

        classifier = self.make_classifier()
        with patch.object(MlxEngine, "_generate_chunk", fake_generate_chunk), \
             patch.object(MlxEngine, "submit", fake_submit):
            responses = await classifier.score_messages(message_lists, on_progress=lambda n: None)

        assert responses == ["50", "5", "200", "1", "30"]


@pytest.mark.slow
@pytest.mark.skipif(not MLX_AVAILABLE, reason="requires mlx-lm (Apple Silicon)")
async def test_score_meets_calibrated_throughput_floor() -> None:
    from cc_sentiment.engines.factory import EngineFactory
    from cc_sentiment.engines.protocol import DEFAULT_MODEL
    from cc_sentiment.hardware import Hardware
    from cc_sentiment.transcripts import (
        ConversationBucketer,
        TranscriptDiscovery,
        TranscriptParser,
    )

    predicted = Hardware.estimate_buckets_per_sec("mlx")
    if predicted is None:
        pytest.skip("hardware below minimum spec")

    transcripts = TranscriptDiscovery.find_transcripts()
    if not transcripts:
        pytest.skip("no transcripts available; run cc-sentiment first")

    paths = [(p, TranscriptDiscovery.stat_mtime(p) or 0.0) for p in transcripts]
    buckets: list[ConversationBucket] = []
    async for parsed in TranscriptParser.stream_transcripts(paths):
        buckets.extend(ConversationBucketer.bucket_events(parsed.events))
        if len(buckets) >= 50:
            break
    buckets = buckets[:50]
    if len(buckets) < 50:
        pytest.skip(f"only {len(buckets)} buckets available, need 50")

    engine = await EngineFactory.build("mlx", DEFAULT_MODEL)
    try:
        await engine.score(buckets[:5])  # warmup
        t0 = time.monotonic()
        await engine.score(buckets)
        elapsed = time.monotonic() - t0
    finally:
        await engine.close()

    measured = len(buckets) / elapsed
    floor = predicted * 0.5
    assert measured >= floor, (
        f"throughput {measured:.1f} b/s below floor {floor:.1f} "
        f"(predicted {predicted:.1f}, hardware regressed by >2x)"
    )


@pytest.mark.skipif(not MLX_AVAILABLE, reason="requires mlx-lm (Apple Silicon)")
class TestSentimentClassifierThreadAffinity:
    async def test_load_and_inference_run_on_same_thread(self) -> None:
        import threading

        from cc_sentiment.sentiment import SentimentClassifier

        observed: list[int] = []

        def fake_load(path: str):
            observed.append(threading.get_ident())
            return MagicMock(), MagicMock(apply_chat_template=lambda *a, **k: [1, 2, 3])

        def fake_batch_generate(*args, **kwargs):
            observed.append(threading.get_ident())
            result = MagicMock()
            result.caches = [MagicMock()]
            result.texts = ["3"]
            return result

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.load = fake_load
        mock_mlx_lm.batch_generate = fake_batch_generate

        from pathlib import Path
        with patch.dict(sys.modules, {"mlx_lm": mock_mlx_lm}), \
             patch("spawnllm.mlx.engine.MLXPatches.apply"), \
             patch("cc_sentiment.sentiment.score_only_processor", lambda tokenizer: MagicMock()):
            classifier = SentimentClassifier(Path("/tmp/fake-fused-dir"))
            await classifier.ensure_loaded()
            for _ in range(3):
                await classifier._engine.submit(classifier._engine._generate_chunk, [
                    [{"role": "user", "content": "hi"}],
                ], 1)

        assert len(set(observed)) == 1, f"MLX work spread across threads: {observed}"
        assert observed[0] != threading.get_ident(), (
            "MLX work ran on the event loop thread, not the dedicated worker"
        )


class TestClassifierIntegration:
    async def test_score_calls_batch_generate(self) -> None:
        from spawnllm.mlx import MlxEngine

        from cc_sentiment.sentiment import SentimentClassifier
        from cc_sentiment.text import build_prefix_messages

        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = [1, 2, 3, 4, 5]

        mock_batch_result = MagicMock()
        mock_batch_result.texts = ["4"]

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.batch_generate.return_value = mock_batch_result

        async def fake_submit(self, fn, *args):
            return fn(*args)

        with patch.dict(sys.modules, {"mlx_lm": mock_mlx_lm}), \
             patch.object(MlxEngine, "submit", fake_submit):
            engine = MlxEngine.__new__(MlxEngine)
            engine._batch_size = 2
            engine.model = MagicMock()
            engine.tokenizer = mock_tokenizer
            engine.logit_processor = MagicMock()
            engine.prefix_messages = build_prefix_messages()
            engine.prefix_tokens = [1, 2]
            engine.base_cache = [MagicMock()]
            classifier = SentimentClassifier.__new__(SentimentClassifier)
            classifier._engine = engine

            scores = await classifier.score([make_bucket([("user", "this works perfectly!")])])

        assert scores == [SentimentScore(4)]
        mock_mlx_lm.batch_generate.assert_called_once()

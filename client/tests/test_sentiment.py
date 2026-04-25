from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from importlib.util import find_spec
from unittest.mock import MagicMock, patch

import pytest

from cc_sentiment.text import (
    MAX_CONVERSATION_CHARS,
    extract_score,
    format_conversation,
)
from cc_sentiment.models import (
    AssistantMessage,
    BucketIndex,
    ConversationBucket,
    SentimentScore,
    SessionId,
    TranscriptMessage,
    UserMessage,
)

MLX_AVAILABLE: bool = (
    find_spec("mlx_lm") is not None and sys.platform == "darwin"
)


def _make_message(role: str, content: str, i: int) -> TranscriptMessage:
    match role:
        case "user":
            return UserMessage(
                content=content,
                timestamp=datetime(2026, 4, 10, 7, 35, i, tzinfo=timezone.utc),
                session_id=SessionId("test-session"),
                uuid=f"uuid-{i}",
                tool_calls=(),
                thinking_chars=0,
                cc_version="2.1.92",
            )
        case "assistant":
            return AssistantMessage(
                content=content,
                timestamp=datetime(2026, 4, 10, 7, 35, i, tzinfo=timezone.utc),
                session_id=SessionId("test-session"),
                uuid=f"uuid-{i}",
                tool_calls=(),
                thinking_chars=0,
                claude_model="claude-sonnet-4-20250514",
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
        messages=tuple(
            _make_message(role, content, i) for i, (role, content) in enumerate(msgs)
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


class TestScoreMessagesSorting:
    @staticmethod
    def make_classifier_stub() -> "object":
        from cc_sentiment.sentiment import SentimentClassifier

        classifier = SentimentClassifier.__new__(SentimentClassifier)
        return classifier

    async def test_score_messages_sorts_by_last_content_length(self) -> None:
        from cc_sentiment.sentiment import SentimentClassifier

        message_lists = [
            [{"role": "user", "content": "longest" * 100}],   # idx 0, very long
            [{"role": "user", "content": "short"}],            # idx 1, shortest
            [{"role": "user", "content": "medium" * 30}],      # idx 2, middle
        ]
        seen_chunks: list[list[str]] = []

        def fake_generate_chunk(self, chunk):
            seen_chunks.append([m[-1]["content"] for m in chunk])
            return [str(len(m[-1]["content"])) for m in chunk]

        classifier = SentimentClassifier.__new__(SentimentClassifier)
        classifier.BATCH_SIZE = 2
        with patch.object(SentimentClassifier, "_generate_chunk", fake_generate_chunk):
            responses = await classifier.score_messages(message_lists, on_progress=lambda n: None)

        flat_seen = [c for chunk in seen_chunks for c in chunk]
        assert flat_seen == sorted(flat_seen, key=len)
        assert responses == [str(len(m[-1]["content"])) for m in message_lists]

    async def test_score_messages_preserves_original_order(self) -> None:
        from cc_sentiment.sentiment import SentimentClassifier

        message_lists = [
            [{"role": "user", "content": "x" * n}] for n in (50, 5, 200, 1, 30)
        ]

        def fake_generate_chunk(self, chunk):
            return [str(len(m[-1]["content"])) for m in chunk]

        classifier = SentimentClassifier.__new__(SentimentClassifier)
        classifier.BATCH_SIZE = 2
        with patch.object(SentimentClassifier, "_generate_chunk", fake_generate_chunk):
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
        buckets.extend(ConversationBucketer.bucket_messages(list(parsed.messages)))
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


class TestClassifierIntegration:
    async def test_score_calls_batch_generate(self) -> None:
        from cc_sentiment.sentiment import SentimentClassifier
        from cc_sentiment.text import build_prefix_messages

        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = [1, 2, 3, 4, 5]

        mock_batch_result = MagicMock()
        mock_batch_result.texts = ["4"]

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.batch_generate.return_value = mock_batch_result

        with patch.dict(sys.modules, {"mlx_lm": mock_mlx_lm}):
            classifier = SentimentClassifier.__new__(SentimentClassifier)
            classifier.model = MagicMock()
            classifier.tokenizer = mock_tokenizer
            classifier.logit_processor = MagicMock()
            classifier.prefix_messages = build_prefix_messages()
            classifier.prefix_tokens = [1, 2]
            classifier.base_cache = [MagicMock()]

            scores = await classifier.score([make_bucket([("user", "this works perfectly!")])])

        assert scores == [SentimentScore(4)]
        mock_mlx_lm.batch_generate.assert_called_once()

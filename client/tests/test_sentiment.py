from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from client.models import (
    BucketIndex,
    ConversationBucket,
    SentimentScore,
    SessionId,
    TranscriptMessage,
)
from client.sentiment import SentimentClassifier


def make_bucket(
    messages: list[tuple[str, str]] | None = None,
) -> ConversationBucket:
    msgs = messages or [("user", "this is great!")]
    return ConversationBucket(
        session_id=SessionId("test-session"),
        bucket_index=BucketIndex(0),
        bucket_start=datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
        messages=tuple(
            TranscriptMessage(
                role=role,
                content=content,
                timestamp=datetime(2026, 4, 10, 7, 35, i, tzinfo=timezone.utc),
                session_id=SessionId("test-session"),
                uuid=f"uuid-{i}",
            )
            for i, (role, content) in enumerate(msgs)
        ),
    )


class TestScoreExtraction:
    def test_valid_json(self) -> None:
        response = '{"score": 4, "reason": "happy developer"}'
        assert SentimentClassifier.extract_score(response) == SentimentScore(4)

    def test_json_with_extra_text(self) -> None:
        response = 'Here is the analysis:\n{"score": 2, "reason": "frustrated"}\n'
        assert SentimentClassifier.extract_score(response) == SentimentScore(2)

    def test_regex_fallback(self) -> None:
        response = 'The score is {"score": 5, broken json'
        assert SentimentClassifier.extract_score(response) == SentimentScore(5)

    def test_neutral_fallback(self) -> None:
        response = "I cannot determine the sentiment"
        assert SentimentClassifier.extract_score(response) == SentimentScore(3)

    def test_score_boundaries(self) -> None:
        assert SentimentClassifier.extract_score('{"score": 1, "reason": "x"}') == SentimentScore(1)
        assert SentimentClassifier.extract_score('{"score": 5, "reason": "x"}') == SentimentScore(5)


class TestConversationFormatting:
    def test_format_user_message(self) -> None:
        bucket = make_bucket([("user", "fix the bug")])
        text = SentimentClassifier.format_conversation(bucket)
        assert "DEVELOPER: fix the bug" in text

    def test_format_assistant_message(self) -> None:
        bucket = make_bucket([("assistant", "I'll fix it")])
        text = SentimentClassifier.format_conversation(bucket)
        assert "AI: I'll fix it" in text

    def test_format_mixed_conversation(self) -> None:
        bucket = make_bucket([
            ("user", "fix the bug"),
            ("assistant", "I'll fix it"),
            ("user", "thanks!"),
        ])
        text = SentimentClassifier.format_conversation(bucket)
        lines = text.strip().splitlines()
        assert len(lines) == 3
        assert lines[0].startswith("DEVELOPER:")
        assert lines[1].startswith("AI:")
        assert lines[2].startswith("DEVELOPER:")


class TestClassifierIntegration:
    def test_score_bucket(self) -> None:
        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = [1, 2, 3]
        mock_tokenizer.encode.return_value = [4, 5, 6]

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.load.return_value = (mock_model, mock_tokenizer)
        mock_mlx_lm.generate.return_value = '{"score": 4, "reason": "productive session"}'

        mock_cache_module = MagicMock()
        mock_cache_module.make_prompt_cache.return_value = []

        mock_generate_module = MagicMock()
        mock_generate_module.generate_step.return_value = iter([])

        mock_mx = MagicMock()

        with patch.dict(sys.modules, {
            "mlx_lm": mock_mlx_lm,
            "mlx_lm.models": MagicMock(),
            "mlx_lm.models.cache": mock_cache_module,
            "mlx_lm.generate": mock_generate_module,
            "mlx.core": mock_mx,
        }), patch("client.patches.apply_kv_cache_patch"):
            classifier = SentimentClassifier.__new__(SentimentClassifier)
            classifier.model = mock_model
            classifier.tokenizer = mock_tokenizer
            classifier.system_cache = []

            bucket = make_bucket([("user", "this works perfectly!")])
            score = classifier.score_bucket(bucket)

        assert score == SentimentScore(4)

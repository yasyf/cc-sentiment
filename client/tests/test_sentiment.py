from __future__ import annotations

import sys
from datetime import datetime, timezone
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


class TestClassifierIntegration:
    def test_score_chunk(self) -> None:
        from cc_sentiment.engines import SYSTEM_PROMPT
        from cc_sentiment.sentiment import SentimentClassifier

        mock_model = MagicMock()
        mock_tokenizer = MagicMock()
        mock_tokenizer.apply_chat_template.return_value = [1, 2, 3]

        mock_batch_result = MagicMock()
        mock_batch_result.texts = ["4"]

        mock_mlx_lm = MagicMock()
        mock_mlx_lm.load.return_value = (mock_model, mock_tokenizer)
        mock_mlx_lm.batch_generate.return_value = mock_batch_result

        mock_logit_proc = MagicMock()

        with patch.dict(sys.modules, {
            "mlx_lm": mock_mlx_lm,
        }), patch("cc_sentiment.patches.apply_kv_cache_patch"), \
             patch("cc_sentiment.sentiment.SentimentClassifier.make_score_logit_processor", return_value=mock_logit_proc):
            classifier = SentimentClassifier.__new__(SentimentClassifier)
            classifier.model = mock_model
            classifier.tokenizer = mock_tokenizer
            classifier.logit_processor = mock_logit_proc
            classifier.system_prompt = SYSTEM_PROMPT
            classifier.system_tokens = [1, 2, 3]
            classifier._load_prompt_caches = MagicMock(return_value=[None])

            bucket = make_bucket([("user", "this works perfectly!")])
            scores = classifier._score_chunk([bucket])

        assert scores == [SentimentScore(4)]
        mock_mlx_lm.batch_generate.assert_called_once()

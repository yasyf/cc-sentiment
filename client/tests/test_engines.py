from __future__ import annotations

from datetime import datetime, timezone

import pytest

from cc_sentiment.engines import check_frustration, extract_score
from cc_sentiment.models import (
    BucketIndex,
    ConversationBucket,
    SentimentScore,
    SessionId,
    TranscriptMessage,
)


def make_bucket(user_text: str) -> ConversationBucket:
    return ConversationBucket(
        session_id=SessionId("test"),
        bucket_index=BucketIndex(0),
        bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        messages=(
            TranscriptMessage(
                role="user",
                content=user_text,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                session_id=SessionId("test"),
                uuid="u1",
            ),
        ),
    )


class TestExtractScore:
    def test_single_digit(self) -> None:
        assert extract_score("3") == SentimentScore(3)

    def test_single_digit_with_padding(self) -> None:
        assert extract_score("<pad><pad>4<pad>") == SentimentScore(4)

    def test_parses_valid_json(self) -> None:
        assert extract_score('{"score": 3, "reason": "neutral"}') == SentimentScore(3)

    def test_finds_digit_in_text(self) -> None:
        assert extract_score("The score is 4.") == SentimentScore(4)

    def test_raises_on_garbage(self) -> None:
        with pytest.raises(ValueError, match="Could not extract score"):
            extract_score("no score here at all")


class TestFrustrationDetection:
    def test_detects_wtf(self) -> None:
        assert check_frustration(make_bucket("wtf is this"))

    def test_detects_this_sucks(self) -> None:
        assert check_frustration(make_bucket("this sucks"))

    def test_detects_fucking_broken(self) -> None:
        assert check_frustration(make_bucket("this is fucking broken"))

    def test_detects_correction_phrase(self) -> None:
        assert check_frustration(make_bucket("no, that's wrong"))

    def test_detects_not_what_i_asked(self) -> None:
        assert check_frustration(make_bucket("that's not what I asked"))

    def test_detects_giving_up(self) -> None:
        assert check_frustration(make_bucket("I give up"))

    def test_no_false_positive_on_neutral(self) -> None:
        assert not check_frustration(make_bucket("please fix the login form"))

    def test_no_false_positive_on_positive(self) -> None:
        assert not check_frustration(make_bucket("this is great, thanks!"))

    def test_ignores_assistant_messages(self) -> None:
        bucket = ConversationBucket(
            session_id=SessionId("test"),
            bucket_index=BucketIndex(0),
            bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            messages=(
                TranscriptMessage(
                    role="assistant",
                    content="wtf this sucks",
                    timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    session_id=SessionId("test"),
                    uuid="u1",
                ),
            ),
        )
        assert not check_frustration(bucket)

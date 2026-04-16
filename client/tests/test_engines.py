from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_sentiment.engines import (
    CLAUDE_EST_INPUT_TOKENS_PER_BUCKET,
    CLAUDE_EST_OUTPUT_TOKENS_PER_BUCKET,
    HAIKU_INPUT_USD_PER_MTOK,
    HAIKU_OUTPUT_USD_PER_MTOK,
    NOOP_PROGRESS,
    ClaudeCLIEngine,
    FrustrationFilter,
    check_frustration,
    claude_cli_available,
    default_engine,
    estimate_claude_cost_usd,
    extract_score,
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


def make_message(role: str, content: str) -> TranscriptMessage:
    match role:
        case "user":
            return UserMessage(
                content=content,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                session_id=SessionId("test"),
                uuid="u1",
                tool_calls=(),
                thinking_chars=0,
                cc_version="2.1.92",
            )
        case "assistant":
            return AssistantMessage(
                content=content,
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                session_id=SessionId("test"),
                uuid="u1",
                tool_calls=(),
                thinking_chars=0,
                claude_model="claude-sonnet-4-20250514",
            )
        case _:
            raise ValueError(f"unknown role: {role}")


def make_bucket(user_text: str) -> ConversationBucket:
    return ConversationBucket(
        session_id=SessionId("test"),
        bucket_index=BucketIndex(0),
        bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
        messages=(make_message("user", user_text),),
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
            messages=(make_message("assistant", "wtf this sucks"),),
        )
        assert not check_frustration(bucket)


class TestEstimateClaudeCost:
    def test_zero_buckets(self) -> None:
        assert estimate_claude_cost_usd(0) == 0.0

    def test_matches_haiku_rates(self) -> None:
        n = 1000
        expected = (
            n * CLAUDE_EST_INPUT_TOKENS_PER_BUCKET / 1_000_000 * HAIKU_INPUT_USD_PER_MTOK
            + n * CLAUDE_EST_OUTPUT_TOKENS_PER_BUCKET / 1_000_000 * HAIKU_OUTPUT_USD_PER_MTOK
        )
        assert estimate_claude_cost_usd(n) == pytest.approx(expected)

    def test_scales_linearly(self) -> None:
        assert estimate_claude_cost_usd(200) == pytest.approx(2 * estimate_claude_cost_usd(100))


class TestClaudeCliAvailable:
    def test_false_when_binary_missing(self) -> None:
        with patch("cc_sentiment.engines.shutil.which", return_value=None):
            assert claude_cli_available() is False

    def test_true_when_auth_status_zero(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("cc_sentiment.engines.shutil.which", return_value="/usr/bin/claude"), \
             patch("cc_sentiment.engines.subprocess.run", return_value=completed):
            assert claude_cli_available() is True

    def test_false_when_auth_status_nonzero(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not logged in")
        with patch("cc_sentiment.engines.shutil.which", return_value="/usr/bin/claude"), \
             patch("cc_sentiment.engines.subprocess.run", return_value=completed):
            assert claude_cli_available() is False

    def test_false_on_timeout(self) -> None:
        with patch("cc_sentiment.engines.shutil.which", return_value="/usr/bin/claude"), \
             patch("cc_sentiment.engines.subprocess.run", side_effect=subprocess.TimeoutExpired("claude", 10)):
            assert claude_cli_available() is False


class TestDefaultEngine:
    def test_apple_silicon_prefers_omlx(self) -> None:
        with patch("cc_sentiment.engines.sys.platform", "darwin"), \
             patch("cc_sentiment.engines.platform.machine", return_value="arm64"):
            assert default_engine() == "omlx"

    def test_linux_falls_back_to_claude(self) -> None:
        with patch("cc_sentiment.engines.sys.platform", "linux"), \
             patch("cc_sentiment.engines.platform.machine", return_value="x86_64"):
            assert default_engine() == "claude"

    def test_darwin_intel_falls_back_to_claude(self) -> None:
        with patch("cc_sentiment.engines.sys.platform", "darwin"), \
             patch("cc_sentiment.engines.platform.machine", return_value="x86_64"):
            assert default_engine() == "claude"


class TestClaudeCLIEngine:
    def test_init_raises_without_claude_binary(self) -> None:
        with patch("cc_sentiment.engines.shutil.which", return_value=None), \
             pytest.raises(RuntimeError, match="claude.*not found"):
            ClaudeCLIEngine(model="claude-haiku-4-5")

    def test_score_parses_json_response_and_tracks_cost(self) -> None:
        import asyncio
        response = json.dumps({
            "type": "result",
            "is_error": False,
            "result": "4",
            "total_cost_usd": 0.0025,
            "usage": {"input_tokens": 2500, "output_tokens": 1},
        }).encode()
        proc = MagicMock(returncode=0, communicate=AsyncMock(return_value=(response, b"")))

        with patch("cc_sentiment.engines.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            scores = asyncio.run(engine.score([make_bucket("please help me fix this")]))

        assert scores == [SentimentScore(4)]
        assert engine.total_cost_usd == pytest.approx(0.0025)
        assert engine.total_input_tokens == 2500
        assert engine.total_output_tokens == 1

    def test_score_raises_on_subprocess_failure(self) -> None:
        import asyncio
        proc = MagicMock(returncode=2, communicate=AsyncMock(return_value=(b"", b"auth failed")))

        with patch("cc_sentiment.engines.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(RuntimeError, match="claude -p failed"):
            asyncio.run(engine.score([make_bucket("please help me fix this")]))


class StubEngine:
    def __init__(self, scores: list[SentimentScore]) -> None:
        self.scores = scores
        self.received: list[ConversationBucket] | None = None
        self.closed = False

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        self.received = list(buckets)
        for _ in buckets:
            on_progress(1)
        return self.scores[: len(buckets)]

    def peak_memory_gb(self) -> float:
        return 0.42

    async def close(self) -> None:
        self.closed = True


class TestFrustrationFilter:
    def test_all_frustrated_skips_inner(self) -> None:
        import asyncio
        stub = StubEngine(scores=[])
        wrapper = FrustrationFilter(stub)
        buckets = [make_bucket("wtf is this"), make_bucket("this is fucking broken")]
        scores = asyncio.run(wrapper.score(buckets))
        assert scores == [SentimentScore(1), SentimentScore(1)]
        assert stub.received == []

    def test_none_frustrated_forwards_all(self) -> None:
        import asyncio
        stub = StubEngine(scores=[SentimentScore(4), SentimentScore(3)])
        wrapper = FrustrationFilter(stub)
        buckets = [make_bucket("please help me"), make_bucket("great job")]
        scores = asyncio.run(wrapper.score(buckets))
        assert scores == [SentimentScore(4), SentimentScore(3)]
        assert stub.received == buckets

    def test_mixed_scatters_by_index(self) -> None:
        import asyncio
        stub = StubEngine(scores=[SentimentScore(4)])
        wrapper = FrustrationFilter(stub)
        buckets = [
            make_bucket("wtf is this"),
            make_bucket("please help me"),
            make_bucket("fuck you"),
        ]
        scores = asyncio.run(wrapper.score(buckets))
        assert scores == [SentimentScore(1), SentimentScore(4), SentimentScore(1)]
        assert stub.received == [buckets[1]]

    def test_on_progress_fires_pre_then_per_inferred(self) -> None:
        import asyncio
        stub = StubEngine(scores=[SentimentScore(4)])
        wrapper = FrustrationFilter(stub)
        buckets = [
            make_bucket("wtf is this"),
            make_bucket("please help me"),
            make_bucket("fuck you"),
        ]
        calls: list[int] = []
        asyncio.run(wrapper.score(buckets, on_progress=calls.append))
        assert calls == [2, 1]

    def test_on_progress_skipped_when_no_frustration(self) -> None:
        import asyncio
        stub = StubEngine(scores=[SentimentScore(4)])
        wrapper = FrustrationFilter(stub)
        calls: list[int] = []
        asyncio.run(wrapper.score([make_bucket("please help me")], on_progress=calls.append))
        assert calls == [1]

    def test_close_and_peak_memory_delegate(self) -> None:
        import asyncio
        stub = StubEngine(scores=[])
        wrapper = FrustrationFilter(stub)
        assert wrapper.peak_memory_gb() == 0.42
        asyncio.run(wrapper.close())
        assert stub.closed is True


class TestClaudeCLIEngineProgress:
    def test_score_fires_on_progress_for_inference_path(self) -> None:
        import asyncio
        response = json.dumps({
            "type": "result",
            "is_error": False,
            "result": "4",
            "total_cost_usd": 0.0025,
            "usage": {"input_tokens": 2500, "output_tokens": 1},
        }).encode()
        proc = MagicMock(returncode=0, communicate=AsyncMock(return_value=(response, b"")))

        with patch("cc_sentiment.engines.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")

        calls: list[int] = []
        with patch("cc_sentiment.engines.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            asyncio.run(engine.score([make_bucket("please help me")], on_progress=calls.append))
        assert calls == [1]

from __future__ import annotations

import subprocess
import sys

import orjson
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib.util import find_spec
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_sentiment.engines import (
    NOOP_PROGRESS,
    ClaudeCLIEngine,
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeReady,
    ClaudeUnavailable,
    EngineFactory,
    FrustrationFilter,
    ImperativeMildIrritationFilter,
)
from cc_sentiment.engines.protocol import DEFAULT_MODEL
from cc_sentiment.text import extract_score
from cc_sentiment.models import (
    AssistantMessage,
    BucketIndex,
    ConversationBucket,
    SentimentScore,
    SessionId,
    TranscriptMessage,
    UserMessage,
)

MLX_AVAILABLE: bool = find_spec("mlx_lm") is not None


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


@dataclass
class StubEngine:
    scores: list[SentimentScore]
    received: list[ConversationBucket] = field(default_factory=list)
    closed: bool = False

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
        assert FrustrationFilter.check_frustration(make_bucket("wtf is this"))

    def test_detects_fucking_broken(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("this is fucking broken"))

    def test_detects_fuck_you(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("fuck you"))

    def test_detects_piece_of_shit(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("this is a piece of shit"))

    def test_detects_completely_useless(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("you are completely useless"))

    def test_detects_giving_up(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("I give up"))

    def test_detects_stop_guessing_caps(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("STOP GUESSING"))

    def test_detects_stop_guessing_lower(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop guessing"))

    def test_detects_stop_guessing_in_context(self) -> None:
        assert FrustrationFilter.check_frustration(
            make_bucket("> quoted AI proposal text here\n\nSTOP GUESSING")
        )

    def test_detects_stop_making_things_up(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop making things up"))

    def test_detects_stop_making_shit_up(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop making shit up"))

    def test_detects_stop_hallucinating(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop hallucinating"))

    def test_detects_stop_being_lazy(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop being lazy"))

    def test_detects_stop_making_excuses(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop making excuses, figure it out"))

    def test_detects_stop_pretending(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop pretending you understand"))

    def test_detects_stop_lying(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("stop lying to me"))

    def test_detects_just_stop_it(self) -> None:
        assert FrustrationFilter.check_frustration(make_bucket("just stop it"))

    def test_detects_just_stop_already(self) -> None:
        assert FrustrationFilter.check_frustration(
            make_bucket("just stop already, this is getting worse")
        )

    def test_does_not_flag_stop_the_server(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("stop the server"))

    def test_does_not_flag_stop_at_line_10(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("stop at line 10"))

    def test_does_not_flag_stop_when_done(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("stop when done"))

    def test_does_not_flag_stop_processing(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("stop processing"))

    def test_does_not_flag_stop_the_build_caps(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("STOP THE BUILD"))

    def test_does_not_flag_stop_after_n(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("stop after 5 iterations"))

    def test_does_not_flag_just_stop_and_think(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("just stop and think"))

    def test_does_not_flag_dont_stop_force(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("dont stop, force remove it"))

    def test_does_not_flag_go_go_go(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("GO GO GO"))

    def test_does_not_flag_ship_it(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("SHIP IT"))

    def test_does_not_flag_great_monitor(self) -> None:
        assert not FrustrationFilter.check_frustration(
            make_bucket("great, monitor it and fix anything that goes wrong")
        )

    def test_does_not_flag_this_sucks(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("this sucks"))

    def test_does_not_flag_try_again(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("try again with a different approach"))

    def test_does_not_flag_no_thats_wrong(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("no, that's wrong"))

    def test_does_not_flag_undo(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("undo that"))

    def test_no_false_positive_on_neutral(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("please fix the login form"))

    def test_no_false_positive_on_positive(self) -> None:
        assert not FrustrationFilter.check_frustration(make_bucket("this is great, thanks!"))

    def test_ignores_assistant_messages(self) -> None:
        bucket = ConversationBucket(
            session_id=SessionId("test"),
            bucket_index=BucketIndex(0),
            bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            messages=(make_message("assistant", "wtf fuck you"),),
        )
        assert not FrustrationFilter.check_frustration(bucket)

    def test_matched_user_message_returns_matching_text(self) -> None:
        bucket = ConversationBucket(
            session_id=SessionId("test"),
            bucket_index=BucketIndex(0),
            bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            messages=(
                make_message("user", "commit that"),
                make_message("user", "wtf is this fucking broken"),
                make_message("user", "ok proceed"),
            ),
        )
        assert FrustrationFilter.matched_user_message(bucket) == "wtf is this fucking broken"

    def test_matched_user_message_returns_none_when_absent(self) -> None:
        assert FrustrationFilter.matched_user_message(make_bucket("looks good")) is None


class TestEstimateClaudeCost:
    def test_zero_buckets(self) -> None:
        assert ClaudeCLIEngine.estimate_cost_usd(0) == 0.0

    def test_matches_haiku_rates(self) -> None:
        n = 1000
        expected = (
            n * ClaudeCLIEngine.EST_INPUT_TOKENS_PER_BUCKET / 1_000_000 * ClaudeCLIEngine.HAIKU_INPUT_USD_PER_MTOK
            + n * ClaudeCLIEngine.EST_OUTPUT_TOKENS_PER_BUCKET / 1_000_000 * ClaudeCLIEngine.HAIKU_OUTPUT_USD_PER_MTOK
        )
        assert ClaudeCLIEngine.estimate_cost_usd(n) == pytest.approx(expected)

    def test_scales_linearly(self) -> None:
        assert ClaudeCLIEngine.estimate_cost_usd(200) == pytest.approx(
            2 * ClaudeCLIEngine.estimate_cost_usd(100)
        )


class TestClaudeCliStatus:
    def test_not_installed_with_brew(self) -> None:
        def which(name: str) -> str | None:
            return "/opt/homebrew/bin/brew" if name == "brew" else None
        with patch("cc_sentiment.engines.claude_cli.shutil.which", side_effect=which):
            assert ClaudeCLIEngine.check_status() == ClaudeNotInstalled(brew_available=True)

    def test_not_installed_without_brew(self) -> None:
        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value=None):
            assert ClaudeCLIEngine.check_status() == ClaudeNotInstalled(brew_available=False)

    def test_ready_when_auth_status_zero(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"), \
             patch("cc_sentiment.engines.claude_cli.subprocess.run", return_value=completed):
            assert ClaudeCLIEngine.check_status() == ClaudeReady()

    def test_not_authenticated_when_auth_status_nonzero(self) -> None:
        completed = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="not logged in")
        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"), \
             patch("cc_sentiment.engines.claude_cli.subprocess.run", return_value=completed):
            assert ClaudeCLIEngine.check_status() == ClaudeNotAuthenticated()


class TestDefaultEngine:
    def test_apple_silicon_prefers_mlx(self) -> None:
        with patch("cc_sentiment.engines.factory.sys.platform", "darwin"), \
             patch("cc_sentiment.engines.factory.platform.machine", return_value="arm64"):
            assert EngineFactory.default() == "mlx"

    def test_linux_falls_back_to_claude(self) -> None:
        with patch("cc_sentiment.engines.factory.sys.platform", "linux"), \
             patch("cc_sentiment.engines.factory.platform.machine", return_value="x86_64"):
            assert EngineFactory.default() == "claude"

    def test_darwin_intel_falls_back_to_claude(self) -> None:
        with patch("cc_sentiment.engines.factory.sys.platform", "darwin"), \
             patch("cc_sentiment.engines.factory.platform.machine", return_value="x86_64"):
            assert EngineFactory.default() == "claude"


@pytest.mark.slow
@pytest.mark.skipif(not MLX_AVAILABLE, reason="requires mlx-lm (Apple Silicon)")
class TestMlxBuild:
    async def test_build_wraps_with_filter_chain(self) -> None:
        engine = await EngineFactory.build("mlx", DEFAULT_MODEL)
        try:
            assert isinstance(engine, ImperativeMildIrritationFilter)
            assert isinstance(engine.inner, FrustrationFilter)
        finally:
            await engine.close()


class TestResolveEngine:
    def test_configure_hub_progress_avoids_textual_bad_fileno(self) -> None:
        code = """
import multiprocessing as mp
import sys
from cc_sentiment.engines.factory import EngineFactory

mp.set_start_method("spawn", force=True)
EngineFactory.configure_hub_progress()

from huggingface_hub.utils.tqdm import tqdm

class FakeStderr:
    def write(self, s):
        pass

    def flush(self):
        pass

    def isatty(self):
        return True

    def fileno(self):
        return -1

sys.stderr = FakeStderr()
for _ in tqdm(range(1), disable=False):
    pass
print("ok")
"""
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "ok"
        assert "bad value(s) in fds_to_keep" not in result.stderr

    def test_non_claude_skips_status_check(self) -> None:
        with patch.object(ClaudeCLIEngine, "check_status") as m:
            assert EngineFactory.resolve("mlx") == "mlx"
            m.assert_not_called()

    def test_claude_ready_returns_engine(self) -> None:
        with patch.object(ClaudeCLIEngine, "check_status", return_value=ClaudeReady()):
            assert EngineFactory.resolve("claude") == "claude"

    def test_claude_not_installed_raises(self) -> None:
        status = ClaudeNotInstalled(brew_available=True)
        with patch.object(ClaudeCLIEngine, "check_status", return_value=status), \
             pytest.raises(ClaudeUnavailable) as exc_info:
            EngineFactory.resolve("claude")
        assert exc_info.value.status == status

    def test_claude_not_authenticated_raises(self) -> None:
        with patch.object(ClaudeCLIEngine, "check_status", return_value=ClaudeNotAuthenticated()), \
             pytest.raises(ClaudeUnavailable) as exc_info:
            EngineFactory.resolve("claude")
        assert exc_info.value.status == ClaudeNotAuthenticated()


class TestClaudeCLIEngine:
    async def test_score_parses_json_response_and_tracks_cost(self) -> None:
        response = orjson.dumps({
            "type": "result",
            "is_error": False,
            "result": "4",
            "total_cost_usd": 0.0025,
            "usage": {"input_tokens": 2500, "output_tokens": 1},
        })
        proc = MagicMock(returncode=0, communicate=AsyncMock(return_value=(response, b"")))

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            scores = await engine.score([make_bucket("please help me fix this")])

        assert scores == [SentimentScore(4)]
        assert engine.total_cost_usd == pytest.approx(0.0025)
        assert engine.total_input_tokens == 2500
        assert engine.total_output_tokens == 1

    async def test_score_raises_on_subprocess_failure(self) -> None:
        proc = MagicMock(returncode=2, communicate=AsyncMock(return_value=(b"", b"auth failed")))

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(RuntimeError, match="claude -p failed"):
            await engine.score([make_bucket("please help me fix this")])

    async def test_score_fires_on_progress_for_inference_path(self) -> None:
        response = orjson.dumps({
            "type": "result",
            "is_error": False,
            "result": "4",
            "total_cost_usd": 0.0025,
            "usage": {"input_tokens": 2500, "output_tokens": 1},
        })
        proc = MagicMock(returncode=0, communicate=AsyncMock(return_value=(response, b"")))

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")

        calls: list[int] = []
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            await engine.score([make_bucket("please help me")], on_progress=calls.append)
        assert calls == [1]


class TestFrustrationFilter:
    async def test_all_frustrated_skips_inner(self) -> None:
        stub = StubEngine(scores=[])
        wrapper = FrustrationFilter(stub)
        buckets = [make_bucket("wtf is this"), make_bucket("this is fucking broken")]
        scores = await wrapper.score(buckets)
        assert scores == [SentimentScore(1), SentimentScore(1)]
        assert stub.received == []

    async def test_none_frustrated_forwards_all(self) -> None:
        stub = StubEngine(scores=[SentimentScore(4), SentimentScore(3)])
        wrapper = FrustrationFilter(stub)
        buckets = [make_bucket("please help me"), make_bucket("great job")]
        scores = await wrapper.score(buckets)
        assert scores == [SentimentScore(4), SentimentScore(3)]
        assert stub.received == buckets

    async def test_mixed_scatters_by_index(self) -> None:
        stub = StubEngine(scores=[SentimentScore(4)])
        wrapper = FrustrationFilter(stub)
        buckets = [
            make_bucket("wtf is this"),
            make_bucket("please help me"),
            make_bucket("fuck you"),
        ]
        scores = await wrapper.score(buckets)
        assert scores == [SentimentScore(1), SentimentScore(4), SentimentScore(1)]
        assert stub.received == [buckets[1]]

    async def test_on_progress_fires_pre_then_per_inferred(self) -> None:
        stub = StubEngine(scores=[SentimentScore(4)])
        wrapper = FrustrationFilter(stub)
        buckets = [
            make_bucket("wtf is this"),
            make_bucket("please help me"),
            make_bucket("fuck you"),
        ]
        calls: list[int] = []
        await wrapper.score(buckets, on_progress=calls.append)
        assert calls == [2, 1]

    async def test_on_progress_skipped_when_no_frustration(self) -> None:
        stub = StubEngine(scores=[SentimentScore(4)])
        wrapper = FrustrationFilter(stub)
        calls: list[int] = []
        await wrapper.score([make_bucket("please help me")], on_progress=calls.append)
        assert calls == [1]

    async def test_close_and_peak_memory_delegate(self) -> None:
        stub = StubEngine(scores=[])
        wrapper = FrustrationFilter(stub)
        assert wrapper.peak_memory_gb() == 0.42
        await wrapper.close()
        assert stub.closed is True


class TestImperativeMildIrritationTrigger:
    def test_and_again_with_exclamation(self) -> None:
        assert ImperativeMildIrritationFilter.matches_trigger(
            "and again! dont modify the repo, just do it in a python call"
        )

    def test_and_again_with_comma(self) -> None:
        assert ImperativeMildIrritationFilter.matches_trigger(
            "and again, dont touch the config"
        )

    def test_yet_again(self) -> None:
        assert ImperativeMildIrritationFilter.matches_trigger("yet again, please use X")

    def test_once_again(self) -> None:
        assert ImperativeMildIrritationFilter.matches_trigger("once again, do this")

    def test_for_the_third_time(self) -> None:
        assert ImperativeMildIrritationFilter.matches_trigger(
            "for the third time, dont touch that file"
        )

    def test_for_the_umpteenth_time(self) -> None:
        assert ImperativeMildIrritationFilter.matches_trigger(
            "for the umpteenth time, please run pytest first"
        )

    def test_case_insensitive(self) -> None:
        assert ImperativeMildIrritationFilter.matches_trigger(
            "AND AGAIN! dont break it"
        )

    def test_no_match_for_bare_again(self) -> None:
        assert not ImperativeMildIrritationFilter.matches_trigger(
            "again, dont modify the repo"
        )

    def test_no_match_for_neutral_imperative(self) -> None:
        assert not ImperativeMildIrritationFilter.matches_trigger(
            "dont modify the repo, just do it in a python call"
        )

    def test_no_match_for_question_with_again(self) -> None:
        assert not ImperativeMildIrritationFilter.matches_trigger(
            "can you try this approach again?"
        )

    def test_no_match_for_for_the_first_time(self) -> None:
        assert not ImperativeMildIrritationFilter.matches_trigger(
            "for the first time, this works"
        )


class TestImperativeMildIrritationDemote:
    @staticmethod
    def patch_hostile(monkeypatch: pytest.MonkeyPatch, hostile: bool) -> None:
        monkeypatch.setattr(
            ImperativeMildIrritationFilter,
            "has_hostile_lexicon",
            staticmethod(lambda _text: hostile),
        )

    def test_demotes_when_trigger_no_hostile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.patch_hostile(monkeypatch, hostile=False)
        bucket = make_bucket("and again! dont modify the repo")
        assert ImperativeMildIrritationFilter.should_demote(bucket) is True

    def test_no_demote_when_trigger_with_hostile(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.patch_hostile(monkeypatch, hostile=True)
        bucket = make_bucket("and again! this is broken")
        assert ImperativeMildIrritationFilter.should_demote(bucket) is False

    def test_no_demote_without_trigger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.patch_hostile(monkeypatch, hostile=False)
        bucket = make_bucket("dont modify the repo")
        assert ImperativeMildIrritationFilter.should_demote(bucket) is False

    def test_ignores_assistant_message_trigger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self.patch_hostile(monkeypatch, hostile=False)
        bucket = ConversationBucket(
            session_id=SessionId("test"),
            bucket_index=BucketIndex(0),
            bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            messages=(
                make_message("assistant", "and again! I will retry"),
                make_message("user", "ok proceed"),
            ),
        )
        assert ImperativeMildIrritationFilter.should_demote(bucket) is False


class TestImperativeMildIrritationFilter:
    async def test_demotes_one_when_trigger_matches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ImperativeMildIrritationFilter,
            "has_hostile_lexicon",
            staticmethod(lambda _text: False),
        )
        stub = StubEngine(scores=[SentimentScore(1)])
        wrapper = ImperativeMildIrritationFilter(stub)
        scores = await wrapper.score(
            [make_bucket("and again! dont modify the repo, just do it in a python call")]
        )
        assert scores == [SentimentScore(2)]

    async def test_passes_through_non_one_scores(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ImperativeMildIrritationFilter,
            "has_hostile_lexicon",
            staticmethod(lambda _text: False),
        )
        stub = StubEngine(scores=[SentimentScore(3), SentimentScore(4), SentimentScore(5)])
        wrapper = ImperativeMildIrritationFilter(stub)
        buckets = [
            make_bucket("and again! dont modify the repo"),
            make_bucket("yet again, please use X"),
            make_bucket("once again, do this"),
        ]
        scores = await wrapper.score(buckets)
        assert scores == [SentimentScore(3), SentimentScore(4), SentimentScore(5)]

    async def test_keeps_one_when_hostile_lexicon_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ImperativeMildIrritationFilter,
            "has_hostile_lexicon",
            staticmethod(lambda _text: True),
        )
        stub = StubEngine(scores=[SentimentScore(1)])
        wrapper = ImperativeMildIrritationFilter(stub)
        scores = await wrapper.score([make_bucket("and again! this is broken garbage")])
        assert scores == [SentimentScore(1)]

    async def test_keeps_one_without_trigger(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            ImperativeMildIrritationFilter,
            "has_hostile_lexicon",
            staticmethod(lambda _text: False),
        )
        stub = StubEngine(scores=[SentimentScore(1)])
        wrapper = ImperativeMildIrritationFilter(stub)
        scores = await wrapper.score(
            [make_bucket("you are wrong, this approach won't work")]
        )
        assert scores == [SentimentScore(1)]

    async def test_close_and_peak_memory_delegate(self) -> None:
        stub = StubEngine(scores=[])
        wrapper = ImperativeMildIrritationFilter(stub)
        assert wrapper.peak_memory_gb() == 0.42
        await wrapper.close()
        assert stub.closed is True

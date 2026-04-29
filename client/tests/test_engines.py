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
    DEFAULT_FILTERS,
    NOOP_PROGRESS,
    ClaudeCLIEngine,
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeReady,
    ClaudeUnavailable,
    EngineFactory,
    FilteredEngine,
    FrustrationFilter,
    ImperativeMildIrritationFilter,
    PositiveClampFilter,
    ScoreFilter,
    SessionResumeFilter,
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
            assert isinstance(engine, FilteredEngine)
            assert engine.filters == DEFAULT_FILTERS
            assert len(engine.filters) == 4
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

    def test_default_swaps_to_claude_when_low_ram_and_claude_ready(self) -> None:
        with patch.object(EngineFactory, "default", return_value="mlx"), \
             patch("cc_sentiment.engines.factory.Hardware.read_free_memory_gb", return_value=2), \
             patch.object(ClaudeCLIEngine, "check_status", return_value=ClaudeReady()):
            assert EngineFactory.resolve(None) == "claude"

    def test_default_keeps_mlx_when_low_ram_but_claude_unavailable(self) -> None:
        with patch.object(EngineFactory, "default", return_value="mlx"), \
             patch("cc_sentiment.engines.factory.Hardware.read_free_memory_gb", return_value=2), \
             patch.object(ClaudeCLIEngine, "check_status", return_value=ClaudeNotInstalled(brew_available=True)):
            assert EngineFactory.resolve(None) == "mlx"

    def test_default_keeps_mlx_when_ram_above_threshold(self) -> None:
        with patch.object(EngineFactory, "default", return_value="mlx"), \
             patch("cc_sentiment.engines.factory.Hardware.read_free_memory_gb", return_value=16), \
             patch.object(ClaudeCLIEngine, "check_status") as check_status:
            assert EngineFactory.resolve(None) == "mlx"
            check_status.assert_not_called()

    def test_explicit_mlx_request_skips_swap_even_when_low_ram(self) -> None:
        with patch("cc_sentiment.engines.factory.Hardware.read_free_memory_gb", return_value=1), \
             patch.object(ClaudeCLIEngine, "check_status") as check_status:
            assert EngineFactory.resolve("mlx") == "mlx"
            check_status.assert_not_called()


class TestClaudeCLIEngine:
    async def test_score_parses_json_response(self) -> None:
        response = orjson.dumps({"type": "result", "is_error": False, "result": "4"})
        proc = MagicMock(returncode=0, communicate=AsyncMock(return_value=(response, b"")))

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            scores = await engine.score([make_bucket("please help me fix this")])

        assert scores == [SentimentScore(4)]

    async def test_score_raises_on_subprocess_failure(self) -> None:
        proc = MagicMock(returncode=2, communicate=AsyncMock(return_value=(b"", b"auth failed")))

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(RuntimeError, match="claude -p failed"):
            await engine.score([make_bucket("please help me fix this")])

    async def test_score_fires_on_progress_for_inference_path(self) -> None:
        response = orjson.dumps({"type": "result", "is_error": False, "result": "4"})
        proc = MagicMock(returncode=0, communicate=AsyncMock(return_value=(response, b"")))

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")

        calls: list[int] = []
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            await engine.score([make_bucket("please help me")], on_progress=calls.append)
        assert calls == [1]



class TestFrustrationFilterTrigger:
    def test_detects_wtf(self) -> None:
        assert FrustrationFilter().short_circuit(make_bucket("wtf is this")) == SentimentScore(1)

    def test_does_not_short_circuit_neutral(self) -> None:
        assert FrustrationFilter().short_circuit(make_bucket("please help me")) is None


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

    def test_demotes_when_trigger_no_hostile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.patch_hostile(monkeypatch, hostile=False)
        bucket = make_bucket("and again! dont modify the repo")
        assert ImperativeMildIrritationFilter.should_demote(bucket) is True

    def test_no_demote_when_trigger_with_hostile(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.patch_hostile(monkeypatch, hostile=True)
        bucket = make_bucket("and again! this is broken")
        assert ImperativeMildIrritationFilter.should_demote(bucket) is False

    def test_no_demote_without_trigger(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.patch_hostile(monkeypatch, hostile=False)
        bucket = make_bucket("dont modify the repo")
        assert ImperativeMildIrritationFilter.should_demote(bucket) is False

    def test_ignores_assistant_message_trigger(self, monkeypatch: pytest.MonkeyPatch) -> None:
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


class TestSessionResumeBare:
    @pytest.mark.parametrize(
        "text",
        [
            "continue",
            "Continue",
            "CONTINUE",
            "continue.",
            "Continue!",
            "continue please",
            "please continue",
            "resume",
            "go ahead",
            "Go ahead",
            "keep going",
            "carry on",
            "proceed",
            "go on",
            "ok continue",
            "okay continue",
            "Continue from where you left off",
            "continue where you left off",
            "[context restored] resume",
        ],
    )
    def test_recognizes_resume(self, text: str) -> None:
        assert SessionResumeFilter.is_bare_resume(text)

    @pytest.mark.parametrize(
        "text",
        [
            "amazing! continue",
            "great work, continue please",
            "continue with the refactor",
            "stop continuing this",
            "should I continue?",
            "lets continue tomorrow",
            "and continue from there",
            "",
            "status?",
        ],
    )
    def test_does_not_match_non_resume(self, text: str) -> None:
        assert not SessionResumeFilter.is_bare_resume(text)


class TestPositiveClampGate:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("status?", True),
            ("what now", True),
            ("hi", True),
            ("how are you", True),
            ("are we good?", True),
            ("are we doing well?", False),
            ("this is taking too long", False),
            ("", True),
        ],
    )
    def test_is_short(self, text: str, expected: bool) -> None:
        assert PositiveClampFilter.is_short(text) is expected


@pytest.fixture
def patch_imperative_hostile_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ImperativeMildIrritationFilter,
        "has_hostile_lexicon",
        staticmethod(lambda _text: False),
    )


@pytest.fixture
def patch_lexicon_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop() -> None:
        return None
    monkeypatch.setattr("cc_sentiment.nlp.NLP.ensure_ready", noop)
    monkeypatch.setattr("cc_sentiment.lexicon.Lexicon.ensure_ready", noop)


class TestSessionResumeFilter:
    async def test_clamps_four_to_three_for_bare_continue(self) -> None:
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(4)]), (SessionResumeFilter(),))
        assert await wrapper.score([make_bucket("Continue")]) == [SentimentScore(3)]

    async def test_clamps_one_to_three_for_bare_resume(self) -> None:
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(1)]), (SessionResumeFilter(),))
        assert await wrapper.score([make_bucket("resume")]) == [SentimentScore(3)]

    async def test_does_not_clamp_mixed_praise(self) -> None:
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(4)]), (SessionResumeFilter(),))
        assert await wrapper.score([make_bucket("amazing! continue")]) == [SentimentScore(4)]

    async def test_does_not_touch_non_resume_5(self) -> None:
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(5)]), (SessionResumeFilter(),))
        assert await wrapper.score([make_bucket("status?")]) == [SentimentScore(5)]

    async def test_clamps_long_form_continue_phrase(self) -> None:
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(4)]), (SessionResumeFilter(),))
        assert await wrapper.score([make_bucket("Continue from where you left off")]) == [SentimentScore(3)]

    async def test_mixed_buckets_clamp_only_resume(self) -> None:
        wrapper = FilteredEngine(
            StubEngine(scores=[SentimentScore(4), SentimentScore(5), SentimentScore(2)]),
            (SessionResumeFilter(),),
        )
        buckets = [make_bucket("Continue"), make_bucket("amazing!"), make_bucket("ugh, fix this")]
        assert await wrapper.score(buckets) == [SentimentScore(3), SentimentScore(5), SentimentScore(2)]


class TestImperativeMildIrritationFilter:
    async def test_demotes_one_when_trigger_matches(
        self,
        patch_imperative_hostile_false: None,
        patch_lexicon_noop: None,
    ) -> None:
        wrapper = FilteredEngine(
            StubEngine(scores=[SentimentScore(1)]), (ImperativeMildIrritationFilter(),),
        )
        scores = await wrapper.score(
            [make_bucket("and again! dont modify the repo, just do it in a python call")]
        )
        assert scores == [SentimentScore(2)]

    async def test_passes_through_non_one_scores(
        self,
        patch_imperative_hostile_false: None,
        patch_lexicon_noop: None,
    ) -> None:
        wrapper = FilteredEngine(
            StubEngine(scores=[SentimentScore(3), SentimentScore(4), SentimentScore(5)]),
            (ImperativeMildIrritationFilter(),),
        )
        buckets = [
            make_bucket("and again! dont modify the repo"),
            make_bucket("yet again, please use X"),
            make_bucket("once again, do this"),
        ]
        assert await wrapper.score(buckets) == [SentimentScore(3), SentimentScore(4), SentimentScore(5)]

    async def test_keeps_one_when_hostile_lexicon_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
        patch_lexicon_noop: None,
    ) -> None:
        monkeypatch.setattr(
            ImperativeMildIrritationFilter,
            "has_hostile_lexicon",
            staticmethod(lambda _text: True),
        )
        wrapper = FilteredEngine(
            StubEngine(scores=[SentimentScore(1)]), (ImperativeMildIrritationFilter(),),
        )
        assert await wrapper.score([make_bucket("and again! this is broken garbage")]) == [SentimentScore(1)]

    async def test_keeps_one_without_trigger(
        self,
        patch_imperative_hostile_false: None,
        patch_lexicon_noop: None,
    ) -> None:
        wrapper = FilteredEngine(
            StubEngine(scores=[SentimentScore(1)]), (ImperativeMildIrritationFilter(),),
        )
        assert await wrapper.score([make_bucket("you are wrong, this approach won't work")]) == [SentimentScore(1)]


class TestPositiveClampFilter:
    @staticmethod
    def patch_positive(monkeypatch: pytest.MonkeyPatch, present: bool) -> None:
        monkeypatch.setattr(
            PositiveClampFilter,
            "has_positive_lexicon",
            staticmethod(lambda _text: present),
        )

    async def test_clamps_5_to_3_when_short_no_positive(
        self, monkeypatch: pytest.MonkeyPatch, patch_lexicon_noop: None
    ) -> None:
        self.patch_positive(monkeypatch, present=False)
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(5)]), (PositiveClampFilter(),))
        assert await wrapper.score([make_bucket("status?")]) == [SentimentScore(3)]

    async def test_keeps_5_when_short_with_positive(
        self, monkeypatch: pytest.MonkeyPatch, patch_lexicon_noop: None
    ) -> None:
        self.patch_positive(monkeypatch, present=True)
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(5)]), (PositiveClampFilter(),))
        assert await wrapper.score([make_bucket("amazing!")]) == [SentimentScore(5)]

    async def test_keeps_5_when_long_message(
        self, monkeypatch: pytest.MonkeyPatch, patch_lexicon_noop: None
    ) -> None:
        self.patch_positive(monkeypatch, present=False)
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(5)]), (PositiveClampFilter(),))
        assert await wrapper.score([make_bucket("are we good or what")]) == [SentimentScore(5)]

    async def test_keeps_5_for_4_word_question(
        self, monkeypatch: pytest.MonkeyPatch, patch_lexicon_noop: None
    ) -> None:
        self.patch_positive(monkeypatch, present=False)
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(5)]), (PositiveClampFilter(),))
        assert await wrapper.score([make_bucket("are we doing well?")]) == [SentimentScore(5)]

    async def test_does_not_touch_score_4(
        self, monkeypatch: pytest.MonkeyPatch, patch_lexicon_noop: None
    ) -> None:
        self.patch_positive(monkeypatch, present=False)
        wrapper = FilteredEngine(StubEngine(scores=[SentimentScore(4)]), (PositiveClampFilter(),))
        assert await wrapper.score([make_bucket("status?")]) == [SentimentScore(4)]

    async def test_does_not_touch_lower_scores(
        self, monkeypatch: pytest.MonkeyPatch, patch_lexicon_noop: None
    ) -> None:
        self.patch_positive(monkeypatch, present=False)
        wrapper = FilteredEngine(
            StubEngine(scores=[SentimentScore(1), SentimentScore(2), SentimentScore(3)]),
            (PositiveClampFilter(),),
        )
        assert await wrapper.score(
            [make_bucket("wtf"), make_bucket("ugh"), make_bucket("status?")]
        ) == [SentimentScore(1), SentimentScore(2), SentimentScore(3)]


@dataclass
class StubFilter(ScoreFilter):
    name: str = "stub"
    short_value: SentimentScore | None = None
    post_delta: int = 0
    prepare_calls: list[str] = field(default_factory=list)

    async def prepare(self) -> None:
        self.prepare_calls.append(self.name)

    def short_circuit(self, bucket: ConversationBucket) -> SentimentScore | None:
        return self.short_value

    def post_process(
        self, bucket: ConversationBucket, score: SentimentScore
    ) -> SentimentScore:
        return SentimentScore(int(score) + self.post_delta) if self.post_delta else score


class TestFilteredEngine:
    async def test_no_filters_just_forwards(self) -> None:
        stub = StubEngine(scores=[SentimentScore(3), SentimentScore(4)])
        engine = FilteredEngine(stub, ())
        assert await engine.score([make_bucket("a"), make_bucket("b")]) == [SentimentScore(3), SentimentScore(4)]
        assert stub.received == [make_bucket("a"), make_bucket("b")]

    async def test_short_circuit_skips_inference(self) -> None:
        stub = StubEngine(scores=[])
        engine = FilteredEngine(stub, (StubFilter(short_value=SentimentScore(1)),))
        assert await engine.score([make_bucket("x"), make_bucket("y")]) == [SentimentScore(1), SentimentScore(1)]
        assert stub.received == []

    async def test_partial_short_circuit_scatters(self) -> None:
        stub = StubEngine(scores=[SentimentScore(3)])
        engine = FilteredEngine(
            stub,
            (FrustrationFilter(),),
        )
        buckets = [make_bucket("wtf"), make_bucket("please help"), make_bucket("fuck you")]
        assert await engine.score(buckets) == [SentimentScore(1), SentimentScore(3), SentimentScore(1)]
        assert stub.received == [buckets[1]]

    async def test_post_process_runs_in_order(self) -> None:
        stub = StubEngine(scores=[SentimentScore(2)])
        engine = FilteredEngine(
            stub,
            (StubFilter(name="a", post_delta=1), StubFilter(name="b", post_delta=2)),
        )
        assert await engine.score([make_bucket("anything")]) == [SentimentScore(5)]

    async def test_prepare_called_once_per_filter(self) -> None:
        recorder: list[str] = []
        f1 = StubFilter(name="alpha", prepare_calls=recorder)
        f2 = StubFilter(name="beta", prepare_calls=recorder)
        engine = FilteredEngine(StubEngine(scores=[SentimentScore(3)]), (f1, f2))
        await engine.score([make_bucket("anything")])
        assert sorted(recorder) == ["alpha", "beta"]

    async def test_on_progress_fires_pre_then_per_inferred(self) -> None:
        engine = FilteredEngine(
            StubEngine(scores=[SentimentScore(4)]),
            (FrustrationFilter(),),
        )
        buckets = [make_bucket("wtf"), make_bucket("please"), make_bucket("fuck you")]
        calls: list[int] = []
        await engine.score(buckets, on_progress=calls.append)
        assert calls == [2, 1]

    async def test_on_progress_skipped_when_all_inferred(self) -> None:
        engine = FilteredEngine(StubEngine(scores=[SentimentScore(3)]), (FrustrationFilter(),))
        calls: list[int] = []
        await engine.score([make_bucket("please help")], on_progress=calls.append)
        assert calls == [1]

    async def test_default_filters_chain(self, patch_lexicon_noop: None, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            ImperativeMildIrritationFilter,
            "has_hostile_lexicon",
            staticmethod(lambda _text: False),
        )
        monkeypatch.setattr(
            PositiveClampFilter,
            "has_positive_lexicon",
            staticmethod(lambda _text: False),
        )
        engine = FilteredEngine(StubEngine(scores=[SentimentScore(4), SentimentScore(5)]), DEFAULT_FILTERS)
        buckets = [make_bucket("Continue"), make_bucket("status?")]
        assert await engine.score(buckets) == [SentimentScore(3), SentimentScore(3)]

    async def test_close_and_peak_memory_delegate(self) -> None:
        stub = StubEngine(scores=[])
        engine = FilteredEngine(stub, ())
        assert engine.peak_memory_gb() == 0.42
        await engine.close()
        assert stub.closed is True

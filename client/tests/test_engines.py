from __future__ import annotations

import subprocess
import sys

import orjson
from datetime import datetime, timezone
from importlib.util import find_spec
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_sentiment.engines import (
    ClaudeCLIEngine,
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeReady,
    ClaudeUnavailable,
    EngineFactory,
    FilteredEngine,
    matched_user_message,
    matches_frustration,
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
from cc_sentiment.transcripts.filterspec import SENTIMENT_SCORE_SPEC

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


class TestFrustrationHelper:
    """cc-sentiment owns the frustration introspection used by snippets/highlighting.
    The exhaustive pattern battery lives in cc-transcript's test_scorespec."""

    @pytest.mark.parametrize("text", ["wtf is this", "this is fucking broken", "stop guessing", "I give up"])
    def test_matches(self, text: str) -> None:
        assert matches_frustration(text)

    @pytest.mark.parametrize("text", ["stop the server", "please fix the login form", "this is great, thanks!"])
    def test_does_not_match(self, text: str) -> None:
        assert not matches_frustration(text)

    def test_matched_user_message_returns_first_match(self) -> None:
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
        assert matched_user_message(bucket) == "wtf is this fucking broken"

    def test_matched_user_message_none_when_absent(self) -> None:
        assert matched_user_message(make_bucket("looks good")) is None

    def test_matched_user_message_ignores_assistant(self) -> None:
        bucket = ConversationBucket(
            session_id=SessionId("test"),
            bucket_index=BucketIndex(0),
            bucket_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            messages=(make_message("assistant", "wtf fuck you"),),
        )
        assert matched_user_message(bucket) is None


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
    async def test_build_wraps_with_score_spec(self) -> None:
        engine = await EngineFactory.build("mlx", DEFAULT_MODEL)
        try:
            assert isinstance(engine, FilteredEngine)
            assert engine.spec == SENTIMENT_SCORE_SPEC
            assert len(engine.spec.stages) == 4
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


class FakeStderr:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = list(lines)

    def __aiter__(self) -> "FakeStderr":
        return self

    async def __anext__(self) -> bytes:
        if not self._lines:
            raise StopAsyncIteration
        return self._lines.pop(0)


def fake_proc(stdout: bytes, stderr: bytes = b"", rc: int = 0) -> MagicMock:
    return MagicMock(
        returncode=rc,
        stdout=MagicMock(read=AsyncMock(return_value=stdout)),
        stderr=FakeStderr([stderr] if stderr else []),
        wait=AsyncMock(return_value=rc),
    )


class TestClaudeCLIEngine:
    async def test_score_parses_json_response(self) -> None:
        response = orjson.dumps({"type": "result", "is_error": False, "result": "4"})
        proc = fake_proc(stdout=response)

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            scores = await engine.score([make_bucket("please help me fix this")])

        assert scores == [SentimentScore(4)]

    async def test_score_raises_on_subprocess_failure(self) -> None:
        proc = fake_proc(stdout=b"", stderr=b"auth failed", rc=2)

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(subprocess.CalledProcessError) as excinfo:
            await engine.score([make_bucket("please help me fix this")])
        cpe = excinfo.value
        assert cpe.returncode == 2
        assert cpe.cmd[0] == "claude"
        assert cpe.stderr == b"auth failed"

    async def test_score_raises_on_is_error_json(self) -> None:
        response = orjson.dumps({"type": "result", "is_error": True, "result": "rate limit"})
        proc = fake_proc(stdout=response, rc=0)

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)), \
             pytest.raises(subprocess.CalledProcessError) as excinfo:
            await engine.score([make_bucket("please help me fix this")])
        cpe = excinfo.value
        assert cpe.returncode == 0
        assert cpe.output == response

    async def test_verbose_flag_added_when_constructor_sets_it(self) -> None:
        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            quiet = ClaudeCLIEngine(model="claude-haiku-4-5")
            loud = ClaudeCLIEngine(model="claude-haiku-4-5", verbose=True)
        msg = [{"role": "user", "content": "hello"}]
        assert "--verbose" not in quiet.argv(msg)
        assert "--verbose" in loud.argv(msg)

    async def test_score_fires_on_progress_for_inference_path(self) -> None:
        response = orjson.dumps({"type": "result", "is_error": False, "result": "4"})
        proc = fake_proc(stdout=response)

        with patch("cc_sentiment.engines.claude_cli.shutil.which", return_value="/usr/bin/claude"):
            engine = ClaudeCLIEngine(model="claude-haiku-4-5")

        calls: list[int] = []
        with patch("cc_sentiment.engines.claude_cli.asyncio.create_subprocess_exec", AsyncMock(return_value=proc)):
            await engine.score([make_bucket("please help me")], on_progress=calls.append)
        assert calls == [1]

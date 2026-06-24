from __future__ import annotations

import subprocess
from collections.abc import Callable

import anyio.to_thread

from spawnllm import (
    BackendNotAuthenticated,
    BackendNotInstalled,
    BackendReady,
    BackendStatus,
    ClaudeCliBackend,
    ClaudeConfig,
    RunSpec,
    run_sync,
)
from spawnllm.proc import map_concurrent

from cc_sentiment.debug_log import DebugLog
from cc_sentiment.engines.base import BaseEngine
from cc_sentiment.engines.protocol import SYSTEM_PROMPT

ClaudeReady = BackendReady
ClaudeNotInstalled = BackendNotInstalled
ClaudeNotAuthenticated = BackendNotAuthenticated
ClaudeStatus = BackendStatus


class ClaudeCLIEngine(BaseEngine):
    HAIKU_MODEL = "claude-haiku-4-5"
    CONCURRENCY = 4

    def __init__(self, model: str, *, verbose: bool = False) -> None:
        self.model = model
        self.verbose = verbose
        self._backend = ClaudeCliBackend()
        self._config = ClaudeConfig(
            system_prompt=SYSTEM_PROMPT,
            max_turns=1,
            tools="",
            disable_slash_commands=True,
            output_format="json",
            verbose=verbose,
        )

    @staticmethod
    def check_status() -> ClaudeStatus:
        return ClaudeCliBackend().check_status()

    @staticmethod
    def _last_user_content(messages: list[dict[str, str]]) -> str:
        return next(m["content"] for m in reversed(messages) if m["role"] == "user")

    def _spec(self, content: str) -> RunSpec:
        return RunSpec(prompt=content, model=self.model, provider_configs={"claude": self._config})

    def argv(self, messages: list[dict[str, str]]) -> list[str]:
        return self._backend.build_command(self._spec(self._last_user_content(messages)))

    async def _call(self, messages: list[dict[str, str]]) -> str:
        spec = self._spec(self._last_user_content(messages))
        resp = await anyio.to_thread.run_sync(lambda: run_sync(spec, backend=self._backend))
        if resp.error is not None:
            DebugLog.get().append("claude", resp.error.msg)
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=self._backend.build_command(spec),
                output=resp.output.raw.encode(),
                stderr=resp.error.msg.encode(),
            )
        return resp.result.raw

    async def score_messages(
        self,
        message_lists: list[list[dict[str, str]]],
        on_progress: Callable[[int], None],
    ) -> list[str]:
        return await map_concurrent(message_lists, self._call, limit=self.CONCURRENCY, on_done=on_progress)

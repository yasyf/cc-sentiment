from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable

from spawnllm import (
    ClaudeCliBackend,
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeReady,
    collect_process,
    map_concurrent,
)
from spawnllm import check_status as check_claude_status

from cc_sentiment.debug_log import DebugLog
from cc_sentiment.engines.base import BaseEngine
from cc_sentiment.engines.protocol import SYSTEM_PROMPT

ClaudeStatus = ClaudeReady | ClaudeNotInstalled | ClaudeNotAuthenticated


class ClaudeCLIEngine(BaseEngine):
    HAIKU_MODEL = "claude-haiku-4-5"
    CONCURRENCY = 4

    def __init__(self, model: str, *, verbose: bool = False) -> None:
        self.model = model
        self.verbose = verbose
        self._backend = ClaudeCliBackend.cc_sentiment(system_prompt=SYSTEM_PROMPT, verbose=verbose)

    @staticmethod
    def check_status() -> ClaudeStatus:
        return check_claude_status()

    @staticmethod
    def _last_user_content(messages: list[dict[str, str]]) -> str:
        return next(m["content"] for m in reversed(messages) if m["role"] == "user")

    def argv(self, messages: list[dict[str, str]]) -> list[str]:
        return self._backend.build_argv(self._last_user_content(messages), model=self.model)

    @staticmethod
    async def collect(proc: asyncio.subprocess.Process) -> tuple[bytes, bytes, int]:
        log = DebugLog.get()
        return await collect_process(
            proc, stderr_tee=lambda raw: log.append("claude", raw.decode(errors="replace"))
        )

    async def _call(self, messages: list[dict[str, str]]) -> str:
        argv = self.argv(messages)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr, rc = await self.collect(proc)
        if rc != 0:
            raise subprocess.CalledProcessError(
                returncode=rc, cmd=argv, output=stdout, stderr=stderr,
            )
        return self._backend.parse_result_envelope(stdout, argv=argv, stderr=stderr)

    async def score_messages(
        self,
        message_lists: list[list[dict[str, str]]],
        on_progress: Callable[[int], None],
    ) -> list[str]:
        return await map_concurrent(message_lists, self._call, limit=self.CONCURRENCY, on_done=on_progress)

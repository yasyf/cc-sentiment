from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import orjson

from cc_sentiment.debug_log import DebugLog
from cc_sentiment.engines.base import BaseEngine
from cc_sentiment.engines.protocol import SYSTEM_PROMPT


@dataclass(frozen=True)
class ClaudeReady:
    pass


@dataclass(frozen=True)
class ClaudeNotInstalled:
    brew_available: bool


@dataclass(frozen=True)
class ClaudeNotAuthenticated:
    pass


ClaudeStatus = ClaudeReady | ClaudeNotInstalled | ClaudeNotAuthenticated


class ClaudeCLIEngine(BaseEngine):
    HAIKU_MODEL = "claude-haiku-4-5"
    CONCURRENCY = 4

    def __init__(self, model: str, *, verbose: bool = False) -> None:
        self.model = model
        self.verbose = verbose

    @staticmethod
    def check_status() -> ClaudeStatus:
        if not shutil.which("claude"):
            return ClaudeNotInstalled(brew_available=bool(shutil.which("brew")))
        result = subprocess.run(
            ["claude", "auth", "status"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode == 0:
            return ClaudeReady()
        return ClaudeNotAuthenticated()

    @staticmethod
    def _last_user_content(messages: list[dict[str, str]]) -> str:
        return next(m["content"] for m in reversed(messages) if m["role"] == "user")

    def argv(self, messages: list[dict[str, str]]) -> list[str]:
        argv = [
            "claude", "-p", self._last_user_content(messages),
            "--model", self.model,
            "--system-prompt", SYSTEM_PROMPT,
            "--output-format", "json",
            "--max-turns", "1",
            "--tools", "",
            "--disable-slash-commands",
        ]
        if self.verbose:
            argv.append("--verbose")
        return argv

    async def _call(self, messages: list[dict[str, str]]) -> str:
        argv = self.argv(messages)
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr, rc = await self._collect(proc)
        if rc != 0:
            raise subprocess.CalledProcessError(
                returncode=rc, cmd=argv, output=stdout, stderr=stderr,
            )
        data = orjson.loads(stdout)
        if data["is_error"]:
            raise subprocess.CalledProcessError(
                returncode=0, cmd=argv, output=stdout, stderr=stderr,
            )
        return data["result"]

    async def _collect(
        self, proc: asyncio.subprocess.Process,
    ) -> tuple[bytes, bytes, int]:
        assert proc.stderr is not None, "create_subprocess_exec was called with stderr=PIPE"
        assert proc.stdout is not None, "create_subprocess_exec was called with stdout=PIPE"
        stderr_buf: bytearray = bytearray()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._tee_stderr(proc.stderr, stderr_buf))
            stdout_task = tg.create_task(proc.stdout.read())
            rc_task = tg.create_task(proc.wait())
        return stdout_task.result(), bytes(stderr_buf), rc_task.result()

    @staticmethod
    async def _tee_stderr(stream: asyncio.StreamReader, buf: bytearray) -> None:
        log = DebugLog.get()
        async for raw in stream:
            buf.extend(raw)
            log.append("claude", raw.decode(errors="replace"))

    async def score_messages(
        self,
        message_lists: list[list[dict[str, str]]],
        on_progress: Callable[[int], None],
    ) -> list[str]:
        sem = asyncio.Semaphore(self.CONCURRENCY)

        async def one(messages: list[dict[str, str]]) -> str:
            async with sem:
                response = await self._call(messages)
            on_progress(1)
            return response

        return list(await asyncio.gather(*(one(m) for m in message_lists)))

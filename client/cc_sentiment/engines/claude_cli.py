from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

import orjson

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
    HAIKU_INPUT_USD_PER_MTOK = 1.0
    HAIKU_OUTPUT_USD_PER_MTOK = 5.0
    EST_INPUT_TOKENS_PER_BUCKET = 2650
    EST_OUTPUT_TOKENS_PER_BUCKET = 1
    CONCURRENCY = 4

    def __init__(self, model: str) -> None:
        self.model = model
        self.total_cost_usd = 0.0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

    @classmethod
    def estimate_cost_usd(cls, bucket_count: int) -> float:
        return bucket_count * (
            cls.EST_INPUT_TOKENS_PER_BUCKET * cls.HAIKU_INPUT_USD_PER_MTOK
            + cls.EST_OUTPUT_TOKENS_PER_BUCKET * cls.HAIKU_OUTPUT_USD_PER_MTOK
        ) / 1_000_000

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

    async def _call(self, messages: list[dict[str, str]]) -> str:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", self._last_user_content(messages),
            "--model", self.model,
            "--system-prompt", SYSTEM_PROMPT,
            "--output-format", "json",
            "--max-turns", "1",
            "--tools", "",
            "--disable-slash-commands",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"claude -p failed ({proc.returncode}): {stderr.decode()[:500]}")
        data = orjson.loads(stdout)
        if data["is_error"]:
            raise RuntimeError(f"claude -p error: {data['result']}")
        usage = data["usage"]
        self.total_cost_usd += data["total_cost_usd"]
        self.total_input_tokens += usage["input_tokens"]
        self.total_output_tokens += usage["output_tokens"]
        return data["result"]

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

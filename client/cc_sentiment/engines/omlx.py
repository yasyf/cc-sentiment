from __future__ import annotations

import asyncio
import contextlib
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import anyio.to_thread
import httpx

from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.text import extract_score, format_conversation

from cc_sentiment.engines.protocol import (
    DEFAULT_MODEL,
    NOOP_PROGRESS,
    STRUCTURED_OUTPUTS_CHOICE,
    SYSTEM_PROMPT,
)


class StallDetected(Exception):
    pass


OMLX_UVX_SPEC = "omlx[grammar] @ git+https://github.com/jundot/omlx.git"
SILENT_LOG: Callable[[str], None] = lambda _: None


@dataclass(frozen=True)
class WarmServer:
    process: subprocess.Popen
    port: int

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}"


class OMLXEngine:
    STALL_TIMEOUT = 10.0
    RESTART_THRESHOLD = 350
    CONCURRENCY = 8

    def __init__(
        self,
        model_repo: str | None = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        HF_MODEL_DIR = Path.home() / ".cache" / "huggingface" / "hub"

        self.repo = model_repo or DEFAULT_MODEL
        self.omlx_dir = self._ensure_model_dir(self.repo, HF_MODEL_DIR)
        self.process: subprocess.Popen | None = None
        self.client: httpx.AsyncClient | None = None
        self.model_name: str | None = None
        self._next_server: WarmServer | None = None
        self._next_warm_task: asyncio.Task[None] | None = None
        self.on_log: Callable[[str], None] = on_log or SILENT_LOG
        self._start_server()

    @staticmethod
    def find_free_port() -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return s.getsockname()[1]

    def _spawn_server(self, port: int, capture_log: bool) -> subprocess.Popen:
        proc = subprocess.Popen(
            [
                "uvx", "--from", OMLX_UVX_SPEC,
                "omlx", "serve",
                "--port", str(port),
                "--model-dir", str(self.omlx_dir),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE if capture_log else subprocess.DEVNULL,
        )
        if capture_log:
            threading.Thread(target=self._drain, args=(proc,), daemon=True).start()
        return proc

    def _drain(self, proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for raw in proc.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                self.on_log(line)

    def _start_server(self) -> None:
        self.port = self.find_free_port()
        self.base_url = f"http://localhost:{self.port}"
        self.process = self._spawn_server(self.port, capture_log=True)
        self.model_name = None
        self._wait_for_ready()
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=300.0)

    def _start_next_server_background(self) -> None:
        port = self.find_free_port()
        proc = self._spawn_server(port, capture_log=False)
        self._next_server = WarmServer(process=proc, port=port)
        self._next_warm_task = asyncio.create_task(
            self._warm_next_server(self._next_server.base_url)
        )

    async def _warm_next_server(self, base: str) -> None:
        async with httpx.AsyncClient(base_url=base, timeout=60.0) as client:
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                with contextlib.suppress(httpx.HTTPError):
                    if (await client.get("/v1/models", timeout=2.0)).status_code == 200:
                        break
                await asyncio.sleep(1.0)
            else:
                return
            with contextlib.suppress(httpx.HTTPError):
                await client.post("/v1/chat/completions", json=self._make_body("warmup"))

    async def _stop_current(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)

    async def _cold_restart_server(self) -> None:
        await self._stop_current()
        await anyio.to_thread.run_sync(self._start_server)

    async def _switch_to_next_server(self) -> None:
        await self._stop_current()
        assert self._next_server is not None
        next_server = self._next_server
        self._next_server = None
        self.process = next_server.process
        self.port = next_server.port
        self.base_url = next_server.base_url
        self.model_name = None
        await anyio.to_thread.run_sync(self._wait_for_ready)
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=300.0)

    @staticmethod
    def _ensure_model_dir(model_repo: str, hf_model_dir: Path) -> Path:
        model_name = model_repo.split("/")[-1]
        model_dir = Path.home() / ".omlx" / "models" / model_name
        model_dir.mkdir(parents=True, exist_ok=True)
        link_path = model_dir / model_name
        if not link_path.exists() and not link_path.is_symlink():
            slug = f"models--{model_repo.replace('/', '--')}"
            snapshots = hf_model_dir / slug / "snapshots"
            if snapshots.exists() and (children := sorted(snapshots.iterdir())):
                link_path.symlink_to(children[-1])
        return model_dir

    def _wait_for_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"{self.base_url}/v1/models", timeout=2.0)
                if resp.status_code == 200:
                    if models := resp.json().get("data", []):
                        self.model_name = models[0]["id"]
                    self.on_log = SILENT_LOG
                    return
            except httpx.ConnectError:
                pass
            time.sleep(1.0)
        self._shutdown()
        raise TimeoutError("omlx server did not start within timeout")

    def peak_memory_gb(self) -> float:
        if self.process and self.process.poll() is None:
            try:
                raw = subprocess.run(
                    ["ps", "-o", "rss=", "-p", str(self.process.pid)],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                return int(raw) / (1024 ** 2) if raw else 0.0
            except (subprocess.TimeoutExpired, ValueError):
                pass
        return 0.0

    async def warm_system_prompt(self) -> None:
        assert self.client is not None
        with contextlib.suppress(httpx.HTTPError):
            await self.client.post("/v1/chat/completions", json=self._make_body("warmup"))

    def _make_body(self, user_content: str) -> dict:
        return {
            **({"model": self.model_name} if self.model_name else {}),
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"CONVERSATION:\n{user_content}"},
            ],
            "max_tokens": 1,
            "temperature": 0.0,
            "structured_outputs": {"choice": STRUCTURED_OUTPUTS_CHOICE},
            "chat_template_kwargs": {"enable_thinking": False},
        }

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        scores: list[SentimentScore] = [SentimentScore(0)] * len(buckets)
        indexed = list(enumerate(buckets))

        for chunk_start in range(0, len(indexed), self.RESTART_THRESHOLD):
            if chunk_start > 0:
                await self._rotate_server()
            if chunk_start + self.RESTART_THRESHOLD < len(indexed):
                self._start_next_server_background()

            remaining = indexed[chunk_start : chunk_start + self.RESTART_THRESHOLD]
            while remaining:
                sem = asyncio.Semaphore(self.CONCURRENCY)
                stalled = False

                async def bounded(idx: int, b: ConversationBucket) -> tuple[int, SentimentScore | None]:
                    nonlocal stalled
                    async with sem:
                        if stalled:
                            return idx, None
                        try:
                            result = await self._score_one(b)
                        except StallDetected:
                            stalled = True
                            return idx, None
                        on_progress(1)
                        return idx, result

                results = await asyncio.gather(*(bounded(i, b) for i, b in remaining))
                for i, s in results:
                    if s is not None:
                        scores[i] = s
                remaining = [(i, b) for (i, b), (_, s) in zip(remaining, results) if s is None]
                if remaining:
                    await self._rotate_server()

        return scores

    async def _rotate_server(self) -> None:
        if self._next_server:
            await self._switch_to_next_server()
        else:
            await self._cold_restart_server()

    async def _score_one(self, bucket: ConversationBucket) -> SentimentScore:
        assert self.client is not None
        body = self._make_body(format_conversation(bucket))
        try:
            resp = await asyncio.wait_for(
                self.client.post("/v1/chat/completions", json=body),
                timeout=self.STALL_TIMEOUT,
            )
        except (TimeoutError, asyncio.TimeoutError):
            raise StallDetected
        resp.raise_for_status()
        return extract_score(resp.json()["choices"][0]["message"]["content"])

    async def close(self) -> None:
        if (task := self._next_warm_task) and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self.client:
            await self.client.aclose()
        self._shutdown()

    def _shutdown(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
        if self._next_server:
            self._next_server.process.terminate()
            try:
                self._next_server.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._next_server.process.kill()
            self._next_server = None

    def __del__(self) -> None:
        self._shutdown()

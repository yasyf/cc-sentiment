from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import json
import platform
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import anyio.to_thread
import httpx

from cc_sentiment.models import ConversationBucket, SentimentScore

DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"
MAX_CONVERSATION_CHARS = 8192

FRUSTRATION_PATTERN = re.compile(
    r"\b("
    r"wtf|wth|ffs|omfg|"
    r"shit(?:ty|tiest)?|dumbass|horrible|awful|"
    r"piss(?:ed|ing)?off|piece\s*of\s*(?:shit|crap|junk)|"
    r"what\s*the\s*(?:fuck|hell)|"
    r"fuck(?:ing?)?\s*(?:broken|useless|terrible|awful|horrible)|"
    r"fuck\s*you|screw\s*(?:this|you)|"
    r"so\s*frustrating|this\s*sucks|damnit|damn\s*it|"
    r"no,?\s*that'?s\s*wrong|not\s*what\s*i\s*asked|"
    r"you\s*misunderstood|that'?s\s*not\s*right|"
    r"undo\s*that|why\s*did\s*you|try\s*again|"
    r"useless|this\s*is\s*terrible|completely\s*wrong|"
    r"i\s*give\s*up|giving\s*up"
    r")\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """Rate the developer's sentiment in this developer-AI conversation. Reply with ONLY a single digit 1-5.

1 - Frustrated, angry, giving up
2 - Annoyed, pointing out mistakes
3 - Neutral, just giving instructions
4 - Satisfied, says it works well
5 - Enthusiastic praise, amazement, strong positive emotion

Key rule: if the developer uses strong positive language like "incredible", "amazing", "love it", "blown away", or multiple exclamation marks, that is 5, not 4. Simple approval like "good" or "works" is 4.

Score ONLY the developer's messages."""

STRUCTURED_OUTPUTS_CHOICE = ["1", "2", "3", "4", "5"]


NOOP_PROGRESS: Callable[[int], None] = lambda _: None
NOOP_SNIPPET: Callable[[str, int], None] = lambda _s, _i: None


class InferenceEngine(Protocol):
    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]: ...
    def peak_memory_gb(self) -> float: ...
    async def close(self) -> None: ...


def check_frustration(bucket: ConversationBucket) -> bool:
    return any(
        msg.role == "user" and FRUSTRATION_PATTERN.search(msg.content)
        for msg in bucket.messages
    )


class FrustrationFilter:
    def __init__(self, inner: InferenceEngine) -> None:
        self.inner = inner

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        flags = [check_frustration(b) for b in buckets]
        to_infer = [(i, b) for i, (b, f) in enumerate(zip(buckets, flags)) if not f]
        if pre := sum(flags):
            on_progress(pre)
        inferred = await self.inner.score([b for _, b in to_infer], on_progress)
        scores = [SentimentScore(1) if f else SentimentScore(0) for f in flags]
        for (idx, _), s in zip(to_infer, inferred):
            scores[idx] = s
        return scores

    def peak_memory_gb(self) -> float:
        return self.inner.peak_memory_gb()

    async def close(self) -> None:
        await self.inner.close()


def format_conversation(bucket: ConversationBucket) -> str:
    full = "\n".join(
        f"{'DEVELOPER' if msg.role == 'user' else 'AI'}: {msg.content}"
        for msg in bucket.messages
    )
    if len(full) > MAX_CONVERSATION_CHARS:
        return full[:MAX_CONVERSATION_CHARS] + "\n[... truncated]"
    return full


def extract_score(response: str) -> SentimentScore:
    cleaned = response.replace("<pad>", "").strip()
    if cleaned in "12345":
        return SentimentScore(int(cleaned))
    match = re.search(r"[1-5]", cleaned)
    if match:
        return SentimentScore(int(match.group()))
    raise ValueError(f"Could not extract score from: {cleaned!r}")


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class StallDetected(Exception):
    pass


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

    def _spawn_server(self, port: int, capture_log: bool) -> subprocess.Popen:
        proc = subprocess.Popen(
            [
                "uvx", "--from", "omlx[grammar] @ git+https://github.com/jundot/omlx.git",
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
        self.port = find_free_port()
        self.base_url = f"http://localhost:{self.port}"
        self.process = self._spawn_server(self.port, capture_log=True)
        self.model_name = None
        self._wait_for_ready()
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=300.0)

    def _start_next_server_background(self) -> None:
        port = find_free_port()
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
        self._start_server()

    async def _switch_to_next_server(self) -> None:
        await self._stop_current()
        assert self._next_server is not None
        next_server = self._next_server
        self._next_server = None
        self.process = next_server.process
        self.port = next_server.port
        self.base_url = next_server.base_url
        self.model_name = None
        self._wait_for_ready()
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
        try:
            await self.client.post("/v1/chat/completions", json=self._make_body("warmup"))
        except httpx.HTTPError:
            pass

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


def default_engine() -> str:
    match (sys.platform, platform.machine()):
        case ("darwin", "arm64"):
            return "omlx"
        case _:
            return "claude"


def resolve_engine(requested: str | None) -> str:
    engine = requested or default_engine()
    if engine != "claude" or ClaudeCLIEngine.is_available():
        return engine
    raise RuntimeError(
        "Can't run sentiment analysis on this platform.\n"
        "cc-sentiment needs Apple Silicon for local inference, "
        "or the `claude` CLI as a fallback.\n\n"
        "Install Claude Code from https://claude.com/claude-code, "
        "then run `claude auth login` and try again."
    )


class ClaudeCLIEngine:
    HAIKU_MODEL = "claude-haiku-4-5"
    HAIKU_INPUT_USD_PER_MTOK = 1.0
    HAIKU_OUTPUT_USD_PER_MTOK = 5.0
    EST_INPUT_TOKENS_PER_BUCKET = 2650
    EST_OUTPUT_TOKENS_PER_BUCKET = 1
    CONCURRENCY = 4

    def __init__(self, model: str) -> None:
        if not shutil.which("claude"):
            raise RuntimeError(
                "`claude` CLI not found. Install Claude Code from https://claude.com/claude-code"
            )
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
    def is_available() -> bool:
        if not shutil.which("claude"):
            return False
        try:
            result = subprocess.run(
                ["claude", "auth", "status"],
                capture_output=True, text=True, timeout=10, check=False,
            )
        except subprocess.TimeoutExpired:
            return False
        return result.returncode == 0

    async def score_one(self, bucket: ConversationBucket) -> SentimentScore:
        proc = await asyncio.create_subprocess_exec(
            "claude", "-p", f"CONVERSATION:\n{format_conversation(bucket)}",
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
        data = json.loads(stdout)
        if data["is_error"]:
            raise RuntimeError(f"claude -p error: {data['result']}")
        usage = data["usage"]
        self.total_cost_usd += data["total_cost_usd"]
        self.total_input_tokens += usage["input_tokens"]
        self.total_output_tokens += usage["output_tokens"]
        return extract_score(data["result"])

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        sem = asyncio.Semaphore(self.CONCURRENCY)

        async def one(bucket: ConversationBucket) -> SentimentScore:
            async with sem:
                score = await self.score_one(bucket)
            on_progress(1)
            return score

        return list(await asyncio.gather(*(one(b) for b in buckets)))

    def peak_memory_gb(self) -> float:
        return 0.0

    async def close(self) -> None:
        pass


async def build_engine(
    kind: str,
    model_repo: str | None = None,
    on_engine_log: Callable[[str], None] | None = None,
) -> InferenceEngine:
    match kind:
        case "mlx":
            if importlib.util.find_spec("mlx_lm") is None:
                raise RuntimeError(
                    "The local mlx engine needs the `mlx` extra. "
                    "Install with `uvx 'cc-sentiment[mlx]'` (Apple Silicon only), "
                    "or use the default engine instead."
                )
            from cc_sentiment.sentiment import SentimentClassifier
            inner: InferenceEngine = await anyio.to_thread.run_sync(
                SentimentClassifier, model_repo or DEFAULT_MODEL
            )
        case "omlx":
            omlx = await anyio.to_thread.run_sync(OMLXEngine, model_repo, on_engine_log)
            await omlx.warm_system_prompt()
            inner = omlx
        case "claude":
            inner = ClaudeCLIEngine(model=model_repo or ClaudeCLIEngine.HAIKU_MODEL)
        case _:
            raise ValueError(f"Unknown engine: {kind}")
    return FrustrationFilter(inner)

from __future__ import annotations

import asyncio
import re
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import httpx

from cc_sentiment.models import ConversationBucket, SentimentScore

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


class InferenceEngine(Protocol):
    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] | None = None,
    ) -> list[SentimentScore]: ...
    def peak_memory_gb(self) -> float: ...
    async def close(self) -> None: ...


def check_frustration(bucket: ConversationBucket) -> bool:
    return any(
        msg.role == "user" and FRUSTRATION_PATTERN.search(msg.content)
        for msg in bucket.messages
    )


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


STALL_TIMEOUT = 10.0
SERVER_RESTART_THRESHOLD = 350


class StallDetected(Exception):
    pass


class OMLXEngine:
    def __init__(self, model_repo: str | None = None) -> None:
        from cc_sentiment.models import DEFAULT_MODEL_REPO

        HF_MODEL_DIR = Path.home() / ".cache" / "huggingface" / "hub"

        self.repo = model_repo or DEFAULT_MODEL_REPO
        self.omlx_dir = self._ensure_model_dir(self.repo, HF_MODEL_DIR)
        self.process: subprocess.Popen | None = None
        self.client: httpx.AsyncClient | None = None
        self.model_name: str | None = None
        self._next_server: tuple[subprocess.Popen, int] | None = None
        self._start_server()

    def _start_server(self) -> None:
        self.port = find_free_port()
        self.base_url = f"http://localhost:{self.port}"
        self.process = subprocess.Popen(
            [
                "omlx", "serve",
                "--port", str(self.port),
                "--model-dir", str(self.omlx_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.model_name = None
        self._wait_for_ready()
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=300.0)

    def _start_next_server_background(self) -> None:
        import threading

        port = find_free_port()
        proc = subprocess.Popen(
            [
                "omlx", "serve",
                "--port", str(port),
                "--model-dir", str(self.omlx_dir),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._next_server = (proc, port)

        def warm() -> None:
            base = f"http://localhost:{port}"
            deadline = time.monotonic() + 60.0
            while time.monotonic() < deadline:
                try:
                    if httpx.get(f"{base}/v1/models", timeout=2.0).status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(1.0)
            else:
                return
            try:
                httpx.post(
                    f"{base}/v1/chat/completions",
                    json=self._make_body("warmup"),
                    timeout=60.0,
                )
            except httpx.HTTPError:
                pass

        threading.Thread(target=warm, daemon=True).start()

    async def _stop_current(self) -> None:
        if self.client:
            await self.client.aclose()
            self.client = None
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=10)

    async def _cold_restart_server(self) -> None:
        await self._stop_current()
        self._start_server()

    async def _switch_to_next_server(self) -> None:
        await self._stop_current()
        assert self._next_server is not None
        proc, port = self._next_server
        self._next_server = None
        self.process = proc
        self.port = port
        self.base_url = f"http://localhost:{port}"
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
        on_progress: Callable[[int], None] | None = None,
    ) -> list[SentimentScore]:
        scores: list[SentimentScore] = [SentimentScore(0)] * len(buckets)
        to_infer: list[tuple[int, ConversationBucket]] = []

        for i, bucket in enumerate(buckets):
            if check_frustration(bucket):
                scores[i] = SentimentScore(1)
            else:
                to_infer.append((i, bucket))

        for chunk_start in range(0, len(to_infer), SERVER_RESTART_THRESHOLD):
            if chunk_start > 0:
                if self._next_server:
                    await self._switch_to_next_server()
                else:
                    await self._cold_restart_server()

            if chunk_start + SERVER_RESTART_THRESHOLD < len(to_infer):
                self._start_next_server_background()

            chunk = to_infer[chunk_start : chunk_start + SERVER_RESTART_THRESHOLD]
            remaining = list(chunk)

            while remaining:
                sem = asyncio.Semaphore(8)
                stalled = False

                async def bounded(idx: int, b: ConversationBucket) -> tuple[int, SentimentScore | None]:
                    nonlocal stalled
                    async with sem:
                        if stalled:
                            return idx, None
                        try:
                            result = await self._score_one(b)
                            if on_progress:
                                on_progress(1)
                            return idx, result
                        except StallDetected:
                            stalled = True
                            return idx, None

                results = await asyncio.gather(*(bounded(i, b) for i, b in remaining))
                by_idx = dict(remaining)
                retry: list[tuple[int, ConversationBucket]] = []
                for i, s in results:
                    if s is not None:
                        scores[i] = s
                    else:
                        retry.append((i, by_idx[i]))

                if retry:
                    if self._next_server:
                        await self._switch_to_next_server()
                    else:
                        await self._cold_restart_server()
                    remaining = retry
                else:
                    break

        return scores

    async def _score_one(self, bucket: ConversationBucket) -> SentimentScore:
        assert self.client is not None
        body = self._make_body(format_conversation(bucket))
        try:
            resp = await asyncio.wait_for(
                self.client.post("/v1/chat/completions", json=body),
                timeout=STALL_TIMEOUT,
            )
        except (TimeoutError, asyncio.TimeoutError):
            raise StallDetected
        resp.raise_for_status()
        return extract_score(resp.json()["choices"][0]["message"]["content"])

    async def close(self) -> None:
        if self.client:
            await self.client.aclose()
        self._shutdown()

    def _shutdown(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            self.process.wait(timeout=10)
        if self._next_server:
            self._next_server[0].terminate()
            try:
                self._next_server[0].wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._next_server[0].kill()
            self._next_server = None

    def __del__(self) -> None:
        self._shutdown()

from __future__ import annotations

import asyncio
import re
import socket
import subprocess
import time
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

SYSTEM_PROMPT = """Rate the developer's sentiment in this developer-AI conversation on a 1-5 scale. Reply with ONLY a single digit.

1 - Deeply frustrated, angry, giving up
2 - Annoyed, things aren't working
3 - Neutral, transactional
4 - Satisfied, productive
5 - Delighted, impressed, flow state

Focus ONLY on the developer's messages. Reply with a single digit 1-5, nothing else."""

LOGIT_BIAS = {
    "236770": 100, "236778": 100, "236800": 100,
    "236812": 100, "236810": 100,
}

HF_MODEL_DIR = Path.home() / ".cache" / "huggingface" / "hub"


class InferenceEngine(Protocol):
    def score_buckets(self, buckets: list[ConversationBucket]) -> list[SentimentScore]: ...


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


def resolve_hf_model_path(model_repo: str) -> Path | None:
    slug = f"models--{model_repo.replace('/', '--')}"
    snapshots = HF_MODEL_DIR / slug / "snapshots"
    if not snapshots.exists():
        return None
    children = sorted(snapshots.iterdir())
    return children[-1] if children else None


def ensure_omlx_model_symlink(model_repo: str) -> Path:
    omlx_dir = Path.home() / ".omlx" / "models"
    omlx_dir.mkdir(parents=True, exist_ok=True)
    link_path = omlx_dir / model_repo.split("/")[-1]

    if not link_path.exists() and not link_path.is_symlink():
        if hf_path := resolve_hf_model_path(model_repo):
            link_path.symlink_to(hf_path)

    return omlx_dir


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


class OMLXEngine:
    def __init__(self, model_repo: str | None = None) -> None:
        from cc_sentiment.models import DEFAULT_MODEL_REPO
        repo = model_repo or DEFAULT_MODEL_REPO
        omlx_dir = ensure_omlx_model_symlink(repo)

        self.port = find_free_port()
        self.process = subprocess.Popen(
            ["omlx", "serve", "--port", str(self.port), "--model-dir", str(omlx_dir)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.model_name: str | None = None
        self.wait_for_ready()
        self.warm_system_prompt()

    def wait_for_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                resp = httpx.get(f"http://localhost:{self.port}/v1/models", timeout=2.0)
                if resp.status_code == 200:
                    if models := resp.json().get("data", []):
                        self.model_name = models[0]["id"]
                    return
            except httpx.ConnectError:
                pass
            time.sleep(1.0)
        self.shutdown()
        raise TimeoutError("omlx server did not start within timeout")

    def warm_system_prompt(self) -> None:
        body = self._make_body("warmup")
        try:
            httpx.post(
                f"http://localhost:{self.port}/v1/chat/completions",
                json=body, timeout=30.0,
            )
        except httpx.HTTPError:
            pass

    def _make_body(self, user_content: str) -> dict:
        body: dict = {
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"CONVERSATION:\n{user_content}"},
            ],
            "max_tokens": 1,
            "temperature": 0.0,
            "chat_template_kwargs": {"enable_thinking": False},
            "logit_bias": LOGIT_BIAS,
        }
        if self.model_name:
            body["model"] = self.model_name
        return body

    def score_buckets(self, buckets: list[ConversationBucket]) -> list[SentimentScore]:
        scores: list[SentimentScore] = [SentimentScore(0)] * len(buckets)
        to_infer: list[tuple[int, ConversationBucket]] = []

        for i, bucket in enumerate(buckets):
            if check_frustration(bucket):
                scores[i] = SentimentScore(1)
            else:
                to_infer.append((i, bucket))

        if to_infer:
            inferred = asyncio.run(self.score_async([b for _, b in to_infer]))
            for (i, _), score in zip(to_infer, inferred):
                scores[i] = score

        return scores

    async def score_async(self, buckets: list[ConversationBucket]) -> list[SentimentScore]:
        sem = asyncio.Semaphore(8)

        async def bounded(b: ConversationBucket) -> SentimentScore:
            async with sem:
                return await self.score_one(client, b)

        async with httpx.AsyncClient(timeout=300.0) as client:
            return await asyncio.gather(*(bounded(b) for b in buckets))

    async def score_one(self, client: httpx.AsyncClient, bucket: ConversationBucket) -> SentimentScore:
        body = self._make_body(format_conversation(bucket))
        resp = await client.post(
            f"http://localhost:{self.port}/v1/chat/completions",
            json=body,
        )
        resp.raise_for_status()
        return extract_score(resp.json()["choices"][0]["message"]["content"])

    def shutdown(self) -> None:
        self.process.terminate()
        self.process.wait(timeout=10)

    def __del__(self) -> None:
        if hasattr(self, "process") and self.process.poll() is None:
            self.shutdown()

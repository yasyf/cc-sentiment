from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from hashlib import sha1
from pathlib import Path

from anyio import to_thread

from cc_sentiment.engines import DEFAULT_MODEL, NOOP_PROGRESS, SYSTEM_PROMPT
from cc_sentiment.models import (
    ConversationBucket,
    SentimentScore,
)
from cc_sentiment.patches import apply_kv_cache_patch
from cc_sentiment.text import extract_score, format_conversation

if sys.platform != "darwin" or platform.machine() != "arm64":
    raise RuntimeError(
        "The local mlx engine requires macOS on Apple Silicon. "
        "Use the default engine on this platform."
    )

__all__ = ["SentimentClassifier"]

CACHE_DIR = Path.home() / ".cc-sentiment"


class SentimentClassifier:
    @staticmethod
    def compute_score_token_ids(tokenizer) -> list[int]:
        return [
            tokenizer.encode(str(n), add_special_tokens=False)[-1]
            for n in range(1, 6)
        ]

    @staticmethod
    def make_score_logit_processor(score_token_ids: list[int]) -> Callable:
        import mlx.core as mx

        allowed = mx.array(score_token_ids)

        def processor(input_ids: mx.array, logits: mx.array) -> mx.array:
            mask = mx.full(logits.shape, -1e9)
            mask[..., allowed] = logits[..., allowed]
            return mask

        return processor

    @staticmethod
    def cache_file_for(model_repo: str) -> Path:
        key = sha1(model_repo.encode()).hexdigest()[:12]
        return CACHE_DIR / f"prompt_cache_{key}.safetensors"

    def __init__(self, model_repo: str = DEFAULT_MODEL) -> None:
        apply_kv_cache_patch()

        from mlx_lm import load

        self.model, self.tokenizer = load(model_repo)
        self.system_prompt = SYSTEM_PROMPT
        self.score_token_ids = self.compute_score_token_ids(self.tokenizer)
        self.logit_processor = self.make_score_logit_processor(self.score_token_ids)
        self.system_tokens = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": self.system_prompt}],
            tokenize=True,
            add_generation_prompt=False,
        )
        self.prompt_cache_file = self.cache_file_for(model_repo)
        self._ensure_prompt_cache()

    def _ensure_prompt_cache(self) -> None:
        if self.prompt_cache_file.exists():
            return

        from mlx_lm import batch_generate
        from mlx_lm.models.cache import make_prompt_cache, save_prompt_cache, trim_prompt_cache

        cache = make_prompt_cache(self.model)

        dummy_user = self.tokenizer.apply_chat_template(
            [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": "test"},
            ],
            tokenize=True,
            add_generation_prompt=True,
        )
        batch_generate(
            self.model, self.tokenizer, [dummy_user],
            prompt_caches=[cache],
            max_tokens=1,
        )

        trimmed = trim_prompt_cache(cache, len(self.system_tokens))
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        save_prompt_cache(str(self.prompt_cache_file), trimmed)

    def _load_prompt_caches(self, n: int) -> list:
        from mlx_lm.models.cache import load_prompt_cache
        return [load_prompt_cache(str(self.prompt_cache_file)) for _ in range(n)]

    def _score_chunk(self, buckets: list[ConversationBucket]) -> list[SentimentScore]:
        from mlx_lm import batch_generate

        prompts = [
            self.tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"CONVERSATION:\n{format_conversation(b)}"},
                ],
                tokenize=True,
                add_generation_prompt=True,
            )
            for b in buckets
        ]
        caches = self._load_prompt_caches(len(buckets))
        result = batch_generate(
            self.model,
            self.tokenizer,
            prompts,
            prompt_caches=caches,
            max_tokens=1,
            logits_processors=[self.logit_processor],
        )
        return [extract_score(text) for text in result.texts]

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
        batch_size: int = 8,
    ) -> list[SentimentScore]:
        scores: list[SentimentScore] = []
        for chunk_start in range(0, len(buckets), batch_size):
            chunk = buckets[chunk_start : chunk_start + batch_size]
            scores.extend(await to_thread.run_sync(self._score_chunk, chunk))
            on_progress(len(chunk))
        return scores

    def peak_memory_gb(self) -> float:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 3)

    async def close(self) -> None:
        pass

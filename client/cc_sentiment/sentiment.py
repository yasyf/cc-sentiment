from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Callable

from cc_sentiment.engines import (
    SYSTEM_PROMPT,
    check_frustration,
    extract_score,
    format_conversation,
)
from cc_sentiment.models import (
    ConversationBucket,
    SentimentScore,
    DEFAULT_MODEL_REPO,
)
from cc_sentiment.patches import apply_kv_cache_patch

if sys.platform != "darwin" or platform.machine() != "arm64":
    raise RuntimeError(
        "cc-sentiment requires macOS on Apple Silicon (MLX is not available on this platform)"
    )

__all__ = ["SentimentClassifier"]

SCORE_TOKEN_IDS = [236770, 236778, 236800, 236812, 236810]
CACHE_DIR = Path.home() / ".cc-sentiment"
PROMPT_CACHE_FILE = CACHE_DIR / "prompt_cache.safetensors"


def make_score_logit_processor() -> Callable:
    import mlx.core as mx

    allowed = mx.array(SCORE_TOKEN_IDS)

    def processor(input_ids: mx.array, logits: mx.array) -> mx.array:
        mask = mx.full(logits.shape, -1e9)
        mask[..., allowed] = logits[..., allowed]
        return mask

    return processor


class SentimentClassifier:
    def __init__(self, model_repo: str = DEFAULT_MODEL_REPO) -> None:
        apply_kv_cache_patch()

        from mlx_lm import load

        self.model, self.tokenizer = load(model_repo)
        self.logit_processor = make_score_logit_processor()
        self._system_tokens = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": SYSTEM_PROMPT}],
            tokenize=True,
            add_generation_prompt=False,
            enable_thinking=False,
        )
        self._ensure_prompt_cache()

    def _ensure_prompt_cache(self) -> None:
        if PROMPT_CACHE_FILE.exists():
            return

        from mlx_lm import batch_generate
        from mlx_lm.models.cache import make_prompt_cache, save_prompt_cache, trim_prompt_cache

        cache = make_prompt_cache(self.model)

        # Process system prompt through model by generating 1 dummy token
        dummy_user = self.tokenizer.apply_chat_template(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": "test"},
            ],
            tokenize=True,
            add_generation_prompt=True,
            enable_thinking=False,
        )
        batch_generate(
            self.model, self.tokenizer, [dummy_user],
            prompt_caches=[cache],
            max_tokens=1,
        )

        trimmed = trim_prompt_cache(cache, len(self._system_tokens))
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        save_prompt_cache(str(PROMPT_CACHE_FILE), trimmed)

    def _load_prompt_caches(self, n: int) -> list:
        from mlx_lm.models.cache import load_prompt_cache
        return [load_prompt_cache(str(PROMPT_CACHE_FILE)) for _ in range(n)]

    def score_buckets(
        self, buckets: list[ConversationBucket], batch_size: int = 8,
    ) -> list[SentimentScore]:
        from mlx_lm import batch_generate

        scores: list[SentimentScore] = [SentimentScore(0)] * len(buckets)
        to_infer: list[tuple[int, ConversationBucket]] = []

        for i, bucket in enumerate(buckets):
            if check_frustration(bucket):
                scores[i] = SentimentScore(1)
            else:
                to_infer.append((i, bucket))

        for chunk_start in range(0, len(to_infer), batch_size):
            chunk = to_infer[chunk_start : chunk_start + batch_size]
            prompts = [
                self.tokenizer.apply_chat_template(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"CONVERSATION:\n{format_conversation(b)}"},
                    ],
                    tokenize=True,
                    add_generation_prompt=True,
                    enable_thinking=False,
                )
                for _, b in chunk
            ]
            caches = self._load_prompt_caches(len(chunk))
            result = batch_generate(
                self.model,
                self.tokenizer,
                prompts,
                prompt_caches=caches,
                max_tokens=1,
                logits_processors=[self.logit_processor],
            )
            for (idx, _), text in zip(chunk, result.texts):
                scores[idx] = extract_score(text)

        return scores

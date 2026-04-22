from __future__ import annotations

import copy
import platform
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

from anyio import to_thread

from cc_sentiment.adapter import AdapterCodec
from cc_sentiment.engines import DEFAULT_MODEL, NOOP_PROGRESS, SYSTEM_PROMPT
from cc_sentiment.engines.protocol import DEMOS
from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.patches import apply_kv_cache_patch
from cc_sentiment.text import extract_score, format_conversation

if sys.platform != "darwin" or platform.machine() != "arm64":
    raise RuntimeError(
        "The local mlx engine requires macOS on Apple Silicon. "
        "Use the default engine on this platform."
    )

__all__ = ["SentimentClassifier"]


class AdapterFuser:
    @classmethod
    def ensure_fused(cls, model_repo: str) -> Path:
        from huggingface_hub.constants import HF_HUB_CACHE

        digest = AdapterCodec.digest()
        repo_dir = Path(HF_HUB_CACHE) / f"models--cc-sentiment--fused-{digest}"
        fused_dir = repo_dir / "snapshots" / digest
        if (fused_dir / "config.json").exists():
            return fused_dir

        from mlx.utils import tree_unflatten
        from mlx_lm.utils import load, save

        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / "adapter_config.json").write_bytes(AdapterCodec.CONFIG.read_bytes())
            AdapterCodec.decode(staging / "adapters.safetensors")
            model, tokenizer, config = load(
                model_repo, adapter_path=str(staging), return_config=True
            )
            model.update_modules(tree_unflatten(
                [(n, m.fuse()) for n, m in model.named_modules() if hasattr(m, "fuse")]
            ))
            fused_dir.mkdir(parents=True, exist_ok=True)
            save(fused_dir, model_repo, model, tokenizer, config, donate_model=True)

        (refs := repo_dir / "refs").mkdir(parents=True, exist_ok=True)
        (refs / "main").write_text(digest)
        return fused_dir


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

    def __init__(self, model_repo: str = DEFAULT_MODEL) -> None:
        apply_kv_cache_patch()

        from mlx_lm import batch_generate, load

        self.model, self.tokenizer = load(str(AdapterFuser.ensure_fused(model_repo)))
        self.system_prompt = SYSTEM_PROMPT
        self.score_token_ids = self.compute_score_token_ids(self.tokenizer)
        self.logit_processor = self.make_score_logit_processor(self.score_token_ids)

        self.prefix_messages: list[dict[str, str]] = [
            {"role": "system", "content": self.system_prompt}
        ]
        for demo_msg, demo_score in DEMOS:
            self.prefix_messages.append(
                {"role": "user", "content": f"CONVERSATION:\nDEVELOPER: {demo_msg}"}
            )
            self.prefix_messages.append({"role": "assistant", "content": demo_score})
        self.prefix_tokens = self.tokenizer.apply_chat_template(
            self.prefix_messages,
            tokenize=True,
            add_generation_prompt=False,
        )
        self.base_cache = batch_generate(
            self.model,
            self.tokenizer,
            [self.prefix_tokens],
            max_tokens=1,
            logits_processors=[self.logit_processor],
            return_prompt_caches=True,
        ).caches[0]

    def _score_chunk(self, buckets: list[ConversationBucket]) -> list[SentimentScore]:
        from mlx_lm import batch_generate

        suffixes = [
            self.tokenizer.apply_chat_template(
                [
                    *self.prefix_messages,
                    {"role": "user", "content": f"CONVERSATION:\n{format_conversation(b)}"},
                ],
                tokenize=True,
                add_generation_prompt=True,
            )[len(self.prefix_tokens):]
            for b in buckets
        ]
        result = batch_generate(
            self.model,
            self.tokenizer,
            suffixes,
            max_tokens=1,
            logits_processors=[self.logit_processor],
            prompt_caches=[copy.deepcopy(self.base_cache) for _ in suffixes],
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

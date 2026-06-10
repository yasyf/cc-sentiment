from __future__ import annotations

import platform
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from spawnllm.mlx import AdapterFuser as MlxAdapterFuser
from spawnllm.mlx import MlxEngine

from cc_sentiment.adapter import AdapterCodec
from cc_sentiment.engines.base import BaseEngine
from cc_sentiment.text import build_prefix_messages

if TYPE_CHECKING:
    from mlx import nn
    from mlx_lm.tokenizer_utils import TokenizerWrapper

if sys.platform != "darwin" or platform.machine() != "arm64":
    raise RuntimeError(
        "The local mlx engine requires macOS on Apple Silicon. "
        "Use the default engine on this platform."
    )


def score_only_processor(tokenizer: TokenizerWrapper) -> Callable:
    import mlx.core as mx

    allowed = mx.array([
        tokenizer.encode(str(n), add_special_tokens=False)[-1]
        for n in range(1, 6)
    ])

    def processor(input_ids: mx.array, logits: mx.array) -> mx.array:
        mask = mx.full(logits.shape, -1e9)
        mask[..., allowed] = logits[..., allowed]
        return mask

    return processor


class AdapterFuser:
    @classmethod
    def ensure_fused(cls, model_repo: str, tqdm_class: type | None = None) -> Path:
        return MlxAdapterFuser.ensure_fused(
            model_repo,
            codec=AdapterCodec(),
            cache_namespace="cc-sentiment--fused",
            tqdm_class=tqdm_class,
        )


class SentimentClassifier(BaseEngine):
    BATCH_SIZE = 2

    def __init__(self, fused_dir: Path) -> None:
        self._engine = MlxEngine(
            fused_dir,
            logits_processor_factory=score_only_processor,
            prefix_messages=build_prefix_messages(),
            batch_size=self.BATCH_SIZE,
            worker_name="cc-mlx",
        )

    @property
    def model(self) -> nn.Module:
        return self._engine.model

    @property
    def tokenizer(self) -> TokenizerWrapper:
        return self._engine.tokenizer

    @property
    def logit_processor(self) -> Callable:
        return self._engine.logit_processor

    @property
    def prefix_tokens(self) -> list[int]:
        return self._engine.prefix_tokens

    @property
    def base_cache(self) -> list[Any]:
        return self._engine.base_cache

    async def ensure_loaded(self) -> None:
        await self._engine.ensure_loaded()

    async def score_messages(
        self,
        message_lists: list[list[dict[str, str]]],
        on_progress: Callable[[int], None],
    ) -> list[str]:
        return await self._engine.generate(message_lists, on_progress)

    def peak_memory_gb(self) -> float:
        return self._engine.peak_memory_gb()

    async def close(self) -> None:
        await self._engine.close()

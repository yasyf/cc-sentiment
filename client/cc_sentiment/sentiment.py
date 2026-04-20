from __future__ import annotations

import copy
import hashlib
import platform
import struct
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

import orjson
from anyio import to_thread

from cc_sentiment.engines import DEFAULT_MODEL, NOOP_PROGRESS, SYSTEM_PROMPT
from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.patches import apply_kv_cache_patch
from cc_sentiment.text import extract_score, format_conversation

if sys.platform != "darwin" or platform.machine() != "arm64":
    raise RuntimeError(
        "The local mlx engine requires macOS on Apple Silicon. "
        "Use the default engine on this platform."
    )

__all__ = ["SentimentClassifier"]

ADAPTER_DIR = Path(__file__).parent / "adapter"
ADAPTER_ZST = ADAPTER_DIR / "adapters.safetensors.zst"
ADAPTER_CONFIG = ADAPTER_DIR / "adapter_config.json"
F32_TYPESIZE = 4


class AdapterFuser:
    @classmethod
    def repo_dir(cls, digest: str) -> Path:
        from huggingface_hub.constants import HF_HUB_CACHE

        return Path(HF_HUB_CACHE) / f"models--cc-sentiment--fused-{digest}"

    @classmethod
    def fused_dir_for(cls, digest: str) -> Path:
        return cls.repo_dir(digest) / "snapshots" / digest

    @classmethod
    def adapter_digest(cls) -> str:
        return hashlib.sha256(ADAPTER_ZST.read_bytes()).hexdigest()[:16]

    @classmethod
    def ensure_fused(cls, model_repo: str) -> Path:
        digest = cls.adapter_digest()
        fused_dir = cls.fused_dir_for(digest)
        if (fused_dir / "config.json").exists():
            return fused_dir

        from mlx.utils import tree_unflatten
        from mlx_lm.utils import load, save

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "adapter_config.json").write_bytes(ADAPTER_CONFIG.read_bytes())
            cls._decompress_adapter(ADAPTER_ZST, tmp_path / "adapters.safetensors")

            model, tokenizer, config = load(
                model_repo, adapter_path=str(tmp_path), return_config=True
            )
            model.update_modules(tree_unflatten(
                [(n, m.fuse()) for n, m in model.named_modules() if hasattr(m, "fuse")]
            ))
            fused_dir.mkdir(parents=True, exist_ok=True)
            save(fused_dir, model_repo, model, tokenizer, config, donate_model=True)

        repo_dir = cls.repo_dir(digest)
        (repo_dir / "refs").mkdir(parents=True, exist_ok=True)
        (repo_dir / "refs" / "main").write_text(digest)
        return fused_dir

    @staticmethod
    def _decompress_adapter(src_zst: Path, dst: Path) -> None:
        import numpy as np
        import zstandard as zstd

        raw = zstd.ZstdDecompressor().decompress(src_zst.read_bytes())
        hlen = struct.unpack("<Q", raw[:8])[0]
        header = orjson.loads(raw[8 : 8 + hlen])
        body = raw[8 + hlen :]
        out = bytearray(raw[: 8 + hlen])
        tensors = sorted(
            ((k, v) for k, v in header.items() if k != "__metadata__"),
            key=lambda kv: kv[1]["data_offsets"][0],
        )
        cursor = 0
        for name, meta in tensors:
            off_s, off_e = meta["data_offsets"]
            nbytes = off_e - off_s
            shuffled = np.frombuffer(body[cursor : cursor + nbytes], dtype=np.uint8)
            cursor += nbytes
            out.extend(shuffled.reshape(F32_TYPESIZE, -1).T.tobytes())
        dst.write_bytes(bytes(out))


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

        fused_dir = AdapterFuser.ensure_fused(model_repo)
        self.model, self.tokenizer = load(str(fused_dir))
        self.system_prompt = SYSTEM_PROMPT
        self.score_token_ids = self.compute_score_token_ids(self.tokenizer)
        self.logit_processor = self.make_score_logit_processor(self.score_token_ids)

        self.system_tokens = self.tokenizer.apply_chat_template(
            [{"role": "system", "content": self.system_prompt}],
            tokenize=True,
            add_generation_prompt=False,
        )
        self.base_cache = batch_generate(
            self.model,
            self.tokenizer,
            [self.system_tokens],
            max_tokens=1,
            logits_processors=[self.logit_processor],
            return_prompt_caches=True,
        ).caches[0]

    def _score_chunk(self, buckets: list[ConversationBucket]) -> list[SentimentScore]:
        from mlx_lm import batch_generate

        suffixes = [
            self.tokenizer.apply_chat_template(
                [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": f"CONVERSATION:\n{format_conversation(b)}"},
                ],
                tokenize=True,
                add_generation_prompt=True,
            )[len(self.system_tokens):]
            for b in buckets
        ]
        caches = [copy.deepcopy(self.base_cache) for _ in suffixes]
        result = batch_generate(
            self.model,
            self.tokenizer,
            suffixes,
            max_tokens=1,
            logits_processors=[self.logit_processor],
            prompt_caches=caches,
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

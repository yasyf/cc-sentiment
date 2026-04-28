from __future__ import annotations

import asyncio
import copy
import platform
import queue
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import anyio.to_thread

from cc_sentiment.adapter import AdapterCodec
from cc_sentiment.engines.base import BaseEngine
from cc_sentiment.patches import MLXPatches
from cc_sentiment.text import build_prefix_messages

if sys.platform != "darwin" or platform.machine() != "arm64":
    raise RuntimeError(
        "The local mlx engine requires macOS on Apple Silicon. "
        "Use the default engine on this platform."
    )

__all__ = ["AdapterFuser", "SentimentClassifier"]


WORKER_STOP = object()


class AdapterFuser:
    @classmethod
    def ensure_fused(cls, model_repo: str) -> Path:
        from huggingface_hub.constants import HF_HUB_CACHE

        digest = AdapterCodec.digest()
        repo_dir = Path(HF_HUB_CACHE) / f"models--cc-sentiment--fused-{digest}"
        fused_dir = repo_dir / "snapshots" / digest
        if (fused_dir / "config.json").exists():
            return fused_dir

        from huggingface_hub import snapshot_download
        from mlx.utils import tree_unflatten
        from mlx_lm.utils import load_adapters, load_model, load_tokenizer, save

        src_path = Path(snapshot_download(model_repo))
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            (staging / "adapter_config.json").write_bytes(AdapterCodec.CONFIG.read_bytes())
            AdapterCodec.decode(staging / "adapters.safetensors")
            model, config = load_model(src_path, lazy=False, strict=False)
            model = load_adapters(model, str(staging))
            model.eval()
            tokenizer = load_tokenizer(src_path, eos_token_ids=config.get("eos_token_id"))
            model.update_modules(tree_unflatten(
                [(n, m.fuse()) for n, m in model.named_modules() if hasattr(m, "fuse")]
            ))
            fused_dir.mkdir(parents=True, exist_ok=True)
            save(fused_dir, src_path, model, tokenizer, config, donate_model=True)

        (refs := repo_dir / "refs").mkdir(parents=True, exist_ok=True)
        (refs / "main").write_text(digest)
        return fused_dir


class SentimentClassifier(BaseEngine):
    BATCH_SIZE = 2

    def __init__(self, fused_dir: Path) -> None:
        self._fused_dir = fused_dir
        self._inbox: queue.SimpleQueue = queue.SimpleQueue()
        self._loaded = threading.Event()
        self._init_error: BaseException | None = None
        self._thread = threading.Thread(target=self._worker, daemon=True, name="cc-mlx")
        self._thread.start()

    def _worker(self) -> None:
        try:
            MLXPatches.apply()
            from mlx_lm import batch_generate, load

            self.model, self.tokenizer = load(str(self._fused_dir))
            self.logit_processor = self._score_only_logit_processor()
            self.prefix_messages = build_prefix_messages()
            self.prefix_tokens = self.tokenizer.apply_chat_template(
                self.prefix_messages, tokenize=True, add_generation_prompt=False,
            )
            self.base_cache = batch_generate(
                self.model, self.tokenizer, [self.prefix_tokens],
                max_tokens=1, logits_processors=[self.logit_processor],
                return_prompt_caches=True,
            ).caches[0]
        except BaseException as exc:
            self._init_error = exc
            self._loaded.set()
            return
        self._loaded.set()
        while True:
            job = self._inbox.get()
            if job is WORKER_STOP:
                return
            fn, args, on_result, on_error = job
            try:
                on_result(fn(*args))
            except BaseException as exc:
                on_error(exc)

    async def ensure_loaded(self) -> None:
        await anyio.to_thread.run_sync(self._loaded.wait)
        if self._init_error is not None:
            raise self._init_error

    async def _submit(self, fn: Callable, *args):
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._inbox.put((
            fn, args,
            lambda value: loop.call_soon_threadsafe(fut.set_result, value),
            lambda exc: loop.call_soon_threadsafe(fut.set_exception, exc),
        ))
        return await fut

    def _score_only_logit_processor(self) -> Callable:
        import mlx.core as mx

        allowed = mx.array([
            self.tokenizer.encode(str(n), add_special_tokens=False)[-1]
            for n in range(1, 6)
        ])

        def processor(input_ids: mx.array, logits: mx.array) -> mx.array:
            mask = mx.full(logits.shape, -1e9)
            mask[..., allowed] = logits[..., allowed]
            return mask

        return processor

    def _generate_chunk(self, chunk: list[list[dict[str, str]]]) -> list[str]:
        from mlx_lm import batch_generate

        suffixes = [
            self.tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True,
            )[len(self.prefix_tokens):]
            for messages in chunk
        ]
        return batch_generate(
            self.model, self.tokenizer, suffixes, max_tokens=1,
            logits_processors=[self.logit_processor],
            prompt_caches=[copy.deepcopy(self.base_cache) for _ in suffixes],
        ).texts

    async def score_messages(
        self,
        message_lists: list[list[dict[str, str]]],
        on_progress: Callable[[int], None],
    ) -> list[str]:
        order = sorted(
            range(len(message_lists)),
            key=lambda i: len(message_lists[i][-1]["content"]),
        )
        responses: list[str] = [""] * len(message_lists)
        for start in range(0, len(order), self.BATCH_SIZE):
            slice_ = order[start:start + self.BATCH_SIZE]
            chunk = [message_lists[i] for i in slice_]
            chunk_responses = await self._submit(self._generate_chunk, chunk)
            for i, r in zip(slice_, chunk_responses):
                responses[i] = r
            on_progress(len(chunk))
        return responses

    def peak_memory_gb(self) -> float:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 3)

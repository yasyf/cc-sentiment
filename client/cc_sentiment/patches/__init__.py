from __future__ import annotations

import contextlib
import importlib.util
import subprocess
import time
from importlib.resources import files as pkg_files
from pathlib import Path


class MLXPatches:
    applied: bool = False

    @classmethod
    def apply(cls) -> None:
        if cls.applied:
            return
        cls.applied = True
        cls._apply_sliding_window()
        cls._apply_batchstats_zerodiv_guard()

    @staticmethod
    def _apply_sliding_window() -> None:
        spec = importlib.util.find_spec("mlx_lm")
        if spec is None or spec.origin is None:
            return
        subprocess.run(
            ["patch", "-p1", "--forward", "-i",
             str(pkg_files("cc_sentiment.patches").joinpath("pr999.patch"))],
            cwd=str(Path(spec.origin).parent.parent),
            capture_output=True,
            timeout=10,
        )

    @staticmethod
    def _apply_batchstats_zerodiv_guard() -> None:
        import mlx.core as mx
        from mlx_lm.generate import BatchGenerator, BatchStats

        @contextlib.contextmanager
        def stats(self, stats: BatchStats | None = None):
            stats = stats or BatchStats()
            self._prompt_tokens_counter = 0
            self._prompt_time_counter = 0
            self._gen_tokens_counter = 0
            tic = time.perf_counter()
            try:
                yield stats
            finally:
                total_time = time.perf_counter() - tic
                stats.prompt_tokens += self._prompt_tokens_counter
                stats.prompt_time += self._prompt_time_counter
                stats.prompt_tps = stats.prompt_tokens / max(stats.prompt_time, 1e-9)
                stats.generation_tokens += self._gen_tokens_counter
                stats.generation_time += total_time - self._prompt_time_counter
                stats.generation_tps = stats.generation_tokens / max(stats.generation_time, 1e-9)
                stats.peak_memory = max(stats.peak_memory, mx.get_peak_memory() / 1e9)

        BatchGenerator.stats = stats

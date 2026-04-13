from __future__ import annotations

import importlib.util
import subprocess
from importlib.resources import files as pkg_files
from pathlib import Path


def apply_kv_cache_patch() -> None:
    # Gemma 4 sliding window fix (PR #999) -- not yet merged upstream.
    # The --forward flag makes `patch` skip already-applied hunks.
    patch_file = pkg_files("cc_sentiment.patches").joinpath("pr999.patch")
    spec = importlib.util.find_spec("mlx_lm")
    if spec is None or spec.origin is None:
        return
    mlx_lm_dir = Path(spec.origin).parent.parent

    subprocess.run(
        ["patch", "-p1", "--forward", "-i", str(patch_file)],
        cwd=str(mlx_lm_dir),
        capture_output=True,
        timeout=10,
    )

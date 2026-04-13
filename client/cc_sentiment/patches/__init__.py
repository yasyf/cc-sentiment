from __future__ import annotations

import importlib.metadata
import importlib.util
import subprocess
from importlib.resources import files as pkg_files
from pathlib import Path


def apply_kv_cache_patch() -> None:
    version = importlib.metadata.version("mlx-lm")
    major, minor, *_ = (int(x) for x in version.split("."))

    # TODO: remove once mlx-lm ships the Gemma 4 sliding window fix (PR #999)
    if major > 0 or minor >= 22:
        return

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

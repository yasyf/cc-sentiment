from __future__ import annotations

import importlib.metadata


def apply_kv_cache_patch() -> None:
    version = importlib.metadata.version("mlx-lm")
    major, minor, *_ = (int(x) for x in version.split("."))

    # TODO: remove once mlx-lm ships the Gemma 4 sliding window fix (PR #999)
    if major == 0 and minor < 22:
        _patch_rotating_kv_cache()


def _patch_rotating_kv_cache() -> None:
    from mlx_lm.models.cache import RotatingKVCache

    original_update = RotatingKVCache.update_and_fetch

    def patched_update(self, keys, values):
        if not hasattr(self, "_window_patched"):
            self._window_patched = True
            if hasattr(self, "max_size") and self.max_size is not None:
                self.max_size = max(self.max_size, keys.shape[2] + 256)
        return original_update(self, keys, values)

    RotatingKVCache.update_and_fetch = patched_update

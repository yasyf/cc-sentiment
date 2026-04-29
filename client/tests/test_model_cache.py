from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from cc_sentiment.model_cache import ModelCache, ModelLoadProgress

pytestmark = pytest.mark.real_model_cache


class FakeClassifier:
    def __init__(self, fused_dir: Path) -> None:
        self.fused_dir = fused_dir
        self.loaded = False

    async def ensure_loaded(self) -> None:
        self.loaded = True


def patch_load_internals(byte_total: int = 0):
    fake_path = Path("/fake/fused")
    return [
        patch("cc_sentiment.sentiment.AdapterFuser.ensure_fused", return_value=fake_path),
        patch("cc_sentiment.sentiment.SentimentClassifier", FakeClassifier),
        patch.object(ModelCache, "_prefetch_total_bytes", lambda self, _: setattr(self.progress, "bytes_total", byte_total)),
    ]


def apply_patches(patches):
    return [p.start() for p in patches], lambda: [p.stop() for p in patches]


async def test_get_returns_loaded_classifier() -> None:
    cache = ModelCache()
    patches = patch_load_internals()
    _, stop = apply_patches(patches)
    try:
        await cache.ensure_started("repo")
        classifier = await cache.get()
    finally:
        stop()
    assert isinstance(classifier, FakeClassifier)
    assert classifier.loaded is True
    assert cache.progress.state == "ready"


async def test_ensure_started_is_idempotent() -> None:
    cache = ModelCache()
    patches = patch_load_internals()
    started, stop = apply_patches(patches)
    ensure_fused_mock = started[0]
    try:
        await cache.ensure_started("repo")
        await cache.ensure_started("repo")
        await cache.get()
    finally:
        stop()
    assert ensure_fused_mock.call_count == 1


async def test_state_transitions_through_downloading_then_loading() -> None:
    states: list[str] = []
    cache = ModelCache()

    def listener(p: ModelLoadProgress) -> None:
        states.append(p.state)

    cache.subscribe(listener)
    patches = patch_load_internals()
    _, stop = apply_patches(patches)
    try:
        await cache.ensure_started("repo")
        await cache.get()
    finally:
        stop()

    assert "downloading" in states
    assert "loading" in states
    assert states[-1] == "ready"


async def test_failure_propagates_and_marks_failed() -> None:
    cache = ModelCache()
    patches = [
        patch(
            "cc_sentiment.sentiment.AdapterFuser.ensure_fused",
            side_effect=OSError("disk full"),
        ),
        patch.object(ModelCache, "_prefetch_total_bytes", lambda self, _: None),
    ]
    _, stop = apply_patches(patches)
    try:
        await cache.ensure_started("repo")
        with pytest.raises(OSError, match="disk full"):
            await cache.get()
    finally:
        stop()
    assert cache.progress.state == "failed"
    assert cache.progress.error is not None and "disk full" in cache.progress.error


async def test_invalidate_clears_classifier_and_allows_reload() -> None:
    cache = ModelCache()
    patches = patch_load_internals()
    started, stop = apply_patches(patches)
    ensure_fused_mock = started[0]
    try:
        await cache.ensure_started("repo")
        await cache.get()
        cache.invalidate()
        assert cache.classifier is None
        assert cache.progress.state == "idle"

        await cache.ensure_started("repo")
        await cache.get()
    finally:
        stop()
    assert ensure_fused_mock.call_count == 2


async def test_get_before_ensure_started_raises() -> None:
    cache = ModelCache()
    with pytest.raises(RuntimeError, match="ensure_started"):
        await cache.get()


async def test_subscribe_emits_initial_then_progress_updates() -> None:
    cache = ModelCache()
    received: list[ModelLoadProgress] = []
    cache.subscribe(received.append)
    assert len(received) == 1
    assert received[0].state == "idle"

    patches = patch_load_internals(byte_total=1000)
    _, stop = apply_patches(patches)
    try:
        await cache.ensure_started("repo")
        await cache.get()
    finally:
        stop()

    states = [p.state for p in received]
    assert "downloading" in states
    assert "ready" in states


def test_bytes_tqdm_invokes_callback_on_update() -> None:
    deltas: list[int] = []
    cls = ModelCache.make_bytes_tqdm(deltas.append)
    bar = cls(total=1000)
    bar.update(100)
    bar.update(250)
    bar.close()
    assert deltas == [100, 250]


def test_bytes_tqdm_swallows_zero_updates() -> None:
    deltas: list[int] = []
    cls = ModelCache.make_bytes_tqdm(deltas.append)
    bar = cls(total=1000)
    bar.update(0)
    bar.close()
    assert deltas == []

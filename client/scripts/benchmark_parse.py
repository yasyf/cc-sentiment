from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

import anyio

from cc_sentiment.transcripts import (
    CLAUDE_PROJECTS_DIR,
    TranscriptDiscovery,
    TranscriptParser,
)
from cc_sentiment.transcripts.parser import PythonBackend


class Timing(NamedTuple):
    label: str
    wall: float
    total_bytes: int
    file_count: int

    @property
    def mb_per_sec(self) -> float:
        return (self.total_bytes / 1e6) / self.wall if self.wall > 0 else 0.0

    @property
    def ms_per_file(self) -> float:
        return (self.wall / self.file_count) * 1000 if self.file_count > 0 else 0.0


def print_row(t: Timing, floor_wall: float | None = None) -> None:
    floor_frac = f" ({t.wall / floor_wall:5.1f}x I/O floor)" if floor_wall else ""
    print(
        f"  {t.label:<44} {t.wall:7.2f}s  "
        f"{t.ms_per_file:6.2f} ms/file  "
        f"{t.mb_per_sec:7.1f} MB/s{floor_frac}"
    )


def time_read_only(paths: list[Path]) -> Timing:
    total = 0
    t0 = time.perf_counter()
    for p in paths:
        try:
            total += len(p.read_bytes())
        except OSError:
            pass
    wall = time.perf_counter() - t0
    return Timing("read_bytes (serial I/O floor)", wall, total, len(paths))


async def time_stream(
    label: str,
    paths: list[tuple[Path, float]],
    total_bytes: int,
    *,
    prefetch: int,
) -> Timing:
    t0 = time.perf_counter()
    count = 0
    async for _ in TranscriptParser.stream_transcripts(paths, prefetch=prefetch):
        count += 1
    wall = time.perf_counter() - t0
    return Timing(label, wall, total_bytes, count)


async def time_pipeline_simulation(
    label: str,
    paths: list[tuple[Path, float]],
    total_bytes: int,
    *,
    prefetch: int,
    score_latency_s: float = 0.010,
) -> Timing:
    t0 = time.perf_counter()
    count = 0
    async for parsed in TranscriptParser.stream_transcripts(paths, prefetch=prefetch):
        for _ in parsed.bucket_keys:
            await anyio.sleep(score_latency_s)
        count += 1
    wall = time.perf_counter() - t0
    return Timing(label, wall, total_bytes, count)


def with_backend(name: str, op: Callable[[], Timing]) -> Timing:
    prev_disable = os.environ.get("CC_SENTIMENT_DISABLE_RUST")
    prev_backend = TranscriptParser.BACKEND
    if name == "python":
        os.environ["CC_SENTIMENT_DISABLE_RUST"] = "1"
        TranscriptParser.BACKEND = PythonBackend()
    else:
        os.environ.pop("CC_SENTIMENT_DISABLE_RUST", None)
        try:
            from cc_sentiment.transcripts.rust import RustBackend
        except ImportError:
            print(f"  (rust backend unavailable; skipping {name} row)")
            return Timing(f"rust unavailable", 0.0, 0, 0)
        TranscriptParser.BACKEND = RustBackend()
    try:
        return op()
    finally:
        if prev_disable is None:
            os.environ.pop("CC_SENTIMENT_DISABLE_RUST", None)
        else:
            os.environ["CC_SENTIMENT_DISABLE_RUST"] = prev_disable
        TranscriptParser.BACKEND = prev_backend


def main() -> None:
    paths = TranscriptDiscovery.find_transcripts()
    total_bytes = sum(p.stat().st_size for p in paths)
    path_mtimes: list[tuple[Path, float]] = [
        (p, p.stat().st_mtime) for p in paths
    ]
    workers = os.cpu_count() or 4

    print(
        f"Found {len(paths)} JSONL files, "
        f"{total_bytes / 1e6:.1f} MB total, "
        f"cpu_count={workers}\n"
    )

    print("=== Baseline I/O ===")
    read = time_read_only(paths)
    print_row(read)

    print("\n=== stream_transcripts (rust, parallelism in Rust) ===")
    rust_8 = with_backend(
        "rust",
        lambda: anyio.run(
            lambda: time_stream(
                "stream_transcripts (rust, prefetch=8)",
                path_mtimes,
                total_bytes,
                prefetch=8,
            )
        ),
    )
    print_row(rust_8, read.wall)

    print("\n=== stream_transcripts (python, process pool) ===")
    py_8 = with_backend(
        "python",
        lambda: anyio.run(
            lambda: time_stream(
                "stream_transcripts (python, prefetch=8)",
                path_mtimes,
                total_bytes,
                prefetch=8,
            )
        ),
    )
    print_row(py_8, read.wall)

    py_1 = with_backend(
        "python",
        lambda: anyio.run(
            lambda: time_stream(
                "stream_transcripts (python, prefetch=1)",
                path_mtimes,
                total_bytes,
                prefetch=1,
            )
        ),
    )
    print_row(py_1, read.wall)

    print("\n=== Rust-native batch scan (metadata-only) ===")
    t0 = time.perf_counter()
    scan_keys = TranscriptParser.scan_bucket_keys(CLAUDE_PROJECTS_DIR)
    wall = time.perf_counter() - t0
    print_row(
        Timing("scan_bucket_keys", wall, total_bytes, len(scan_keys)),
        read.wall,
    )

    print("\n=== Pipeline simulation (10 ms/bucket LLM cost) ===")
    sim_8 = with_backend(
        "rust",
        lambda: anyio.run(
            lambda: time_pipeline_simulation(
                "pipeline (rust, prefetch=8)",
                path_mtimes,
                total_bytes,
                prefetch=8,
            )
        ),
    )
    print_row(sim_8, read.wall)

    sim_1 = with_backend(
        "rust",
        lambda: anyio.run(
            lambda: time_pipeline_simulation(
                "pipeline (rust, prefetch=1)",
                path_mtimes,
                total_bytes,
                prefetch=1,
            )
        ),
    )
    print_row(sim_1, read.wall)

    print("\n=== Acceptance gates ===")
    if py_1.wall > 0:
        ratio = py_1.wall / py_8.wall if py_8.wall > 0 else 0.0
        print(f"  python prefetch=8 vs prefetch=1 speedup: {ratio:.2f}x (target >= 2.0x)")
    if sim_1.wall > 0 and sim_8.wall > 0:
        ratio = sim_1.wall / sim_8.wall
        print(f"  pipeline prefetch=8 vs prefetch=1 speedup: {ratio:.2f}x (target >= 1.3x)")


if __name__ == "__main__":
    main()

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from statistics import mean, median

from cc_sentiment.transcripts import (
    CLAUDE_PROJECTS_DIR,
    ConversationBucketer,
    TranscriptDiscovery,
    TranscriptParser,
)


@dataclass(frozen=True)
class Timing:
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


class Benchmark:
    @staticmethod
    def time_serial(
        label: str, paths: list[Path], op: Callable[[Path], int]
    ) -> Timing:
        total_bytes = 0
        t0 = time.perf_counter()
        for p in paths:
            total_bytes += op(p)
        wall = time.perf_counter() - t0
        return Timing(label, wall, total_bytes, len(paths))

    @staticmethod
    def time_parallel(
        label: str, paths: list[Path], op: Callable[[Path], int], workers: int
    ) -> Timing:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            total_bytes = sum(pool.map(op, paths))
        wall = time.perf_counter() - t0
        return Timing(label, wall, total_bytes, len(paths))

    @staticmethod
    def read_only(path: Path) -> int:
        try:
            return len(path.read_bytes())
        except OSError:
            return 0

    @staticmethod
    def parse_file(path: Path) -> int:
        try:
            return len(TranscriptParser.parse_file(path)) or 0
        except OSError:
            return 0

    @staticmethod
    def bucket_keys(path: Path) -> int:
        try:
            return len(TranscriptParser.bucket_keys_for(path))
        except OSError:
            return 0

    @staticmethod
    def parse_and_bucket(path: Path) -> int:
        try:
            messages = TranscriptParser.parse_file(path)
        except OSError:
            return 0
        return len(ConversationBucketer.bucket_messages(messages)) if messages else 0


def print_row(t: Timing, floor_wall: float | None = None) -> None:
    floor_frac = f" ({t.wall / floor_wall:5.1f}x I/O floor)" if floor_wall else ""
    print(
        f"  {t.label:<26} {t.wall:7.2f}s  "
        f"{t.ms_per_file:6.2f} ms/file  "
        f"{t.mb_per_sec:7.1f} MB/s{floor_frac}"
    )


def main() -> None:
    paths = TranscriptDiscovery.find_transcripts()
    total_bytes = sum(p.stat().st_size for p in paths)
    workers = os.cpu_count() or 4

    print(
        f"Found {len(paths)} JSONL files, "
        f"{total_bytes / 1e6:.1f} MB total, "
        f"workers={workers}\n"
    )

    print("=== Serial (single thread) ===")
    read = Benchmark.time_serial("read_bytes only", paths, Benchmark.read_only)
    print_row(read)
    parse = Benchmark.time_serial("parse_file", paths, Benchmark.parse_file)
    print_row(parse, read.wall)
    keys = Benchmark.time_serial("bucket_keys_for", paths, Benchmark.bucket_keys)
    print_row(keys, read.wall)
    parse_bucket = Benchmark.time_serial(
        "parse_file+bucket", paths, Benchmark.parse_and_bucket
    )
    print_row(parse_bucket, read.wall)

    print(f"\n=== Parallel ({workers} threads) ===")
    read_p = Benchmark.time_parallel("read_bytes only", paths, Benchmark.read_only, workers)
    print_row(read_p)
    parse_p = Benchmark.time_parallel("parse_file", paths, Benchmark.parse_file, workers)
    print_row(parse_p, read_p.wall)
    keys_p = Benchmark.time_parallel(
        "bucket_keys_for", paths, Benchmark.bucket_keys, workers
    )
    print_row(keys_p, read_p.wall)

    print("\n=== Rust-native batch scan (parallelism in Rust) ===")
    t0 = time.perf_counter()
    scan_keys = TranscriptParser.scan_bucket_keys(CLAUDE_PROJECTS_DIR)
    wall = time.perf_counter() - t0
    print_row(
        Timing("scan_bucket_keys", wall, total_bytes, len(scan_keys)),
        read_p.wall,
    )
    t0 = time.perf_counter()
    scan_parsed = TranscriptParser.scan_parse_files(CLAUDE_PROJECTS_DIR)
    wall = time.perf_counter() - t0
    print_row(
        Timing("scan_parse_files", wall, total_bytes, len(scan_parsed)),
        read_p.wall,
    )

    print("\n=== Per-file parse_file distribution (serial, ms) ===")
    per_file: list[float] = []
    for p in paths:
        t0 = time.perf_counter()
        TranscriptParser.parse_file(p)
        per_file.append((time.perf_counter() - t0) * 1000)
    per_file.sort()
    p50 = median(per_file)
    p90 = per_file[int(len(per_file) * 0.9)]
    p99 = per_file[int(len(per_file) * 0.99)]
    print(
        f"  count={len(per_file)}  mean={mean(per_file):.2f}  "
        f"p50={p50:.2f}  p90={p90:.2f}  p99={p99:.2f}  max={max(per_file):.2f}"
    )

    print("\n=== Summary ===")
    parse_over_io = parse.wall / read.wall if read.wall > 0 else 0
    keys_over_io = keys.wall / read.wall if read.wall > 0 else 0
    cpu_fraction_parse = max(0.0, (parse.wall - read.wall) / parse.wall) if parse.wall > 0 else 0
    cpu_fraction_keys = max(0.0, (keys.wall - read.wall) / keys.wall) if keys.wall > 0 else 0
    print(f"  parse_file cost: {parse_over_io:.2f}x raw read I/O")
    print(f"  bucket_keys_for cost: {keys_over_io:.2f}x raw read I/O")
    print(f"  parse_file CPU fraction (wall - I/O floor) / wall: {cpu_fraction_parse:.1%}")
    print(f"  bucket_keys_for CPU fraction: {cpu_fraction_keys:.1%}")
    print(
        "\n  Best case Rust recovers ~the CPU fraction (minus FFI overhead).\n"
        "  If CPU fraction is small (<20%), even a perfect Rust port barely moves wall-clock."
    )


if __name__ == "__main__":
    main()

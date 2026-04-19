from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median, stdev

import anyio
import click

from cc_sentiment.engines import DEFAULT_MODEL, EngineFactory, InferenceEngine
from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.transcripts import (
    ConversationBucketer,
    TranscriptDiscovery,
    TranscriptParser,
)

SCORE_COLORS: dict[int, str] = {1: "red", 2: "red", 3: "yellow", 4: "green", 5: "green"}


@dataclass
class BucketResult:
    session_id: str
    bucket_index: int
    score: int


@dataclass
class EngineResult:
    engine: str
    load_time: float
    wall_time: float
    buckets_scored: int
    peak_memory_gb: float
    bucket_results: list[BucketResult] = field(default_factory=list)

    @property
    def buckets_per_sec(self) -> float:
        return self.buckets_scored / self.wall_time if self.wall_time > 0 else 0.0


class BenchmarkRunner:
    @staticmethod
    def collect_buckets(max_transcripts: int) -> list[ConversationBucket]:
        transcripts = TranscriptDiscovery.find_transcripts()[:max_transcripts]
        paths: list[tuple[Path, float]] = [
            (p, TranscriptDiscovery.stat_mtime(p) or 0.0) for p in transcripts
        ]

        async def collect() -> list[ConversationBucket]:
            buckets: list[ConversationBucket] = []
            with click.progressbar(length=len(paths), label="Parsing transcripts") as bar:
                async for parsed in TranscriptParser.stream_transcripts(paths):
                    buckets.extend(ConversationBucketer.bucket_messages(list(parsed.messages)))
                    bar.update(1)
            return buckets

        return anyio.run(collect)

    @staticmethod
    def predownload_model(model_repo: str | None = None) -> None:
        repo = model_repo or DEFAULT_MODEL
        click.echo(f"Ensuring model is downloaded: {repo}")
        from huggingface_hub import snapshot_download
        snapshot_download(repo)

    @staticmethod
    async def create_engine(
        engine_name: str,
        model_repo: str | None = None,
    ) -> InferenceEngine:
        return await EngineFactory.build(engine_name, model_repo)

    @classmethod
    async def run_engine(
        cls,
        engine_name: str,
        buckets: list[ConversationBucket],
        runs: int,
        model_repo: str | None = None,
    ) -> EngineResult:
        load_start = time.monotonic()
        engine = await cls.create_engine(engine_name, model_repo)
        load_time = time.monotonic() - load_start

        click.echo("  Warmup run...")
        await engine.score(buckets[:min(3, len(buckets))])

        total_wall = 0.0
        last_scores: list[SentimentScore] = []

        for run_idx in range(runs):
            scored = 0
            run_start = time.monotonic()

            def on_progress(n: int) -> None:
                nonlocal scored
                scored += n
                elapsed = time.monotonic() - run_start
                rate = scored / elapsed if elapsed > 0 else 0
                click.echo(
                    f"\r  Run {run_idx + 1}/{runs}: "
                    f"{scored}/{len(buckets)} buckets "
                    f"({rate:.1f} b/s)",
                    nl=False,
                )

            wall_start = time.monotonic()
            last_scores = await engine.score(buckets, on_progress=on_progress)
            total_wall += time.monotonic() - wall_start
            click.echo()

        peak_memory_gb = engine.peak_memory_gb()

        await engine.close()

        return EngineResult(
            engine=engine_name,
            load_time=load_time,
            wall_time=total_wall / runs,
            buckets_scored=len(buckets),
            peak_memory_gb=peak_memory_gb,
            bucket_results=[
                BucketResult(session_id=b.session_id, bucket_index=b.bucket_index, score=int(s))
                for b, s in zip(buckets, last_scores)
            ],
        )

    @staticmethod
    def print_per_bucket(results: dict[str, EngineResult], buckets: list[ConversationBucket]) -> None:
        engines = list(results.keys())
        click.echo(f"\n{'Bucket':<40} | " + " | ".join(f"{e:>5}" for e in engines) + " | Agree?")
        click.echo("-" * (45 + 9 * len(engines)))

        agreements = 0
        for i in range(len(buckets)):
            sid = buckets[i].session_id[:16] + "..."
            label = f"{sid} #{buckets[i].bucket_index}"

            scores = [results[eng].bucket_results[i].score for eng in engines]
            score_strs = [
                click.style(f"{s:>5}", fg=SCORE_COLORS[s]) for s in scores
            ]

            agree = len(set(scores)) == 1
            agreements += agree
            agree_str = click.style("yes", fg="green") if agree else click.style("NO", fg="red", bold=True)
            click.echo(f"{label:<40} | " + " | ".join(score_strs) + f" | {agree_str}")

        total = len(buckets)
        click.echo(f"\nAgreement: {agreements}/{total} ({100 * agreements / total:.0f}%)")

        for eng in engines:
            scores = [r.score for r in results[eng].bucket_results]
            s = stdev(scores) if len(scores) > 1 else 0.0
            click.echo(f"  {eng}: mean={mean(scores):.2f} median={median(scores):.1f} std={s:.2f}")

    @staticmethod
    def print_performance(results: dict[str, EngineResult]) -> None:
        click.echo("\n=== Performance ===")
        header = f"{'Engine':<10} | {'Load':>6} | {'Wall':>8} | {'b/s':>6} | {'Peak RAM':>8}"
        click.echo(header)
        click.echo("-" * len(header))
        for r in results.values():
            click.echo(
                f"{r.engine:<10} | {r.load_time:>5.1f}s | {r.wall_time:>7.1f}s | "
                f"{r.buckets_per_sec:>5.1f} | {r.peak_memory_gb:>6.2f}GB"
            )

    @classmethod
    def run_benchmark(
        cls,
        max_transcripts: int,
        runs: int,
        engines: list[str],
        model_repo: str | None = None,
        scaling_test: bool = False,
    ) -> None:
        click.echo(f"Collecting buckets from up to {max_transcripts} transcripts...")
        buckets = cls.collect_buckets(max_transcripts)

        if not buckets:
            click.echo("No transcript buckets found.")
            return

        click.echo(f"Found {len(buckets)} buckets. Running {runs} timed run(s) per engine (+ warmup).\n")
        cls.predownload_model(model_repo)

        if scaling_test:
            cls.run_scaling_test(buckets, engines[0], model_repo)
            return

        async def run_all() -> dict[str, EngineResult]:
            results: dict[str, EngineResult] = {}
            for engine_name in engines:
                click.echo(f"Benchmarking {engine_name}...")
                result = await cls.run_engine(engine_name, buckets, runs, model_repo)
                results[engine_name] = result
                click.echo(f"  Done: {result.wall_time:.1f}s wall, {result.buckets_per_sec:.1f} buckets/s\n")
            return results

        results = anyio.run(run_all)

        if len(results) > 1:
            click.echo(f"\n=== Quality Comparison ({max_transcripts} transcripts, {len(buckets)} buckets) ===")
            cls.print_per_bucket(results, buckets)

        cls.print_performance(results)

    @classmethod
    def run_scaling_test(
        cls,
        buckets: list[ConversationBucket],
        engine_name: str,
        model_repo: str | None = None,
    ) -> None:
        sizes = [10, 50, 100, 200, 250, 300, 350, 400, 500]
        sizes = [s for s in sizes if s <= len(buckets)]
        if len(buckets) not in sizes:
            sizes.append(len(buckets))

        click.echo(f"=== Scaling test: {engine_name}, sizes {sizes} ===\n")

        async def run() -> None:
            click.echo(f"\n{'N':>6} | {'Wall':>8} | {'b/s':>8} | {'per-bucket':>10}")
            click.echo("-" * 42)

            for n in sizes:
                engine = await cls.create_engine(engine_name, model_repo)
                subset = buckets[:n]
                scored = 0
                t0 = time.monotonic()

                def on_progress(count: int) -> None:
                    nonlocal scored
                    scored += count
                    elapsed = time.monotonic() - t0
                    rate = scored / elapsed if elapsed > 0 else 0
                    click.echo(f"\r  {scored}/{n} ({rate:.1f} b/s)", nl=False)

                scores = await engine.score(subset, on_progress=on_progress)
                wall = time.monotonic() - t0
                rate = n / wall if wall > 0 else 0
                per = wall / n * 1000 if n > 0 else 0

                sample = [int(s) for s in scores[:5]]
                click.echo(f"\r{n:>6} | {wall:>7.2f}s | {rate:>7.1f} | {per:>8.1f}ms | sample: {sample}")
                await engine.close()

        anyio.run(run)

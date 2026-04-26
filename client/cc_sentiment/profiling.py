from __future__ import annotations

import copy
import statistics
import time
from dataclasses import dataclass
from typing import ClassVar

import anyio
import click

from cc_sentiment.models import ConversationBucket
from cc_sentiment.sentiment import AdapterFuser, SentimentClassifier
from cc_sentiment.text import build_bucket_messages
from cc_sentiment.transcripts import (
    ConversationBucketer,
    TranscriptDiscovery,
    TranscriptParser,
)


@dataclass(frozen=True)
class PhaseTimings:
    tokenize_ms: list[float]
    deepcopy_ms: list[float]
    generate_ms: list[float]
    chunk_total_ms: list[float]
    n_buckets: int

    @property
    def per_bucket_ms(self) -> float:
        return sum(self.chunk_total_ms) / self.n_buckets

    @property
    def per_chunk_ms(self) -> float:
        return statistics.mean(self.chunk_total_ms)


class Profiler:
    BATCH_SIZES: ClassVar[tuple[int, ...]] = (1, 2, 4, 8, 16, 32)

    @staticmethod
    def collect_buckets(target: int) -> list[ConversationBucket]:
        transcripts = TranscriptDiscovery.find_transcripts()
        click.echo(f"  Found {len(transcripts)} transcripts; parsing until {target} buckets…")

        async def collect() -> list[ConversationBucket]:
            paths = [(p, TranscriptDiscovery.stat_mtime(p) or 0.0) for p in transcripts]
            buckets: list[ConversationBucket] = []
            async for parsed in TranscriptParser.stream_transcripts(paths):
                buckets.extend(ConversationBucketer.bucket_messages(list(parsed.messages)))
                if len(buckets) >= target:
                    break
            return buckets[:target]

        return anyio.run(collect)

    @staticmethod
    def build_classifier(model_repo: str) -> SentimentClassifier:
        click.echo(f"  Loading {model_repo} + fusing adapter…")
        t0 = time.monotonic()
        fused_dir = AdapterFuser.ensure_fused(model_repo)
        classifier = SentimentClassifier(fused_dir)
        classifier.ensure_base_cache()
        click.echo(f"  Ready in {time.monotonic() - t0:.1f}s")
        return classifier

    @staticmethod
    def time_deepcopy(classifier: SentimentClassifier, n: int) -> list[float]:
        timings: list[float] = []
        for _ in range(n):
            t0 = time.perf_counter()
            copy.deepcopy(classifier.base_cache)
            timings.append((time.perf_counter() - t0) * 1000)
        return timings

    @classmethod
    def run_chunk_with_breakdown(
        cls,
        classifier: SentimentClassifier,
        buckets: list[ConversationBucket],
        batch_size: int,
        use_cache: bool,
        sort_by_length: bool = False,
    ) -> PhaseTimings:
        from mlx_lm import batch_generate

        message_lists = [build_bucket_messages(b) for b in buckets]
        all_suffixes = [
            classifier.tokenizer.apply_chat_template(
                messages, tokenize=True, add_generation_prompt=True,
            )[len(classifier.prefix_tokens):]
            for messages in message_lists
        ]
        if sort_by_length:
            all_suffixes = sorted(all_suffixes, key=len)

        tokenize_ms: list[float] = [0.0]
        deepcopy_ms: list[float] = []
        generate_ms: list[float] = []
        chunk_total_ms: list[float] = []

        for start in range(0, len(all_suffixes), batch_size):
            suffixes = all_suffixes[start:start + batch_size]
            chunk_t0 = time.perf_counter()

            dc_t0 = time.perf_counter()
            caches = (
                [copy.deepcopy(classifier.base_cache) for _ in suffixes]
                if use_cache else None
            )
            deepcopy_ms.append((time.perf_counter() - dc_t0) * 1000)

            gen_t0 = time.perf_counter()
            kwargs = dict(
                max_tokens=1,
                logits_processors=[classifier.logit_processor],
            )
            if caches is not None:
                kwargs["prompt_caches"] = caches
            batch_generate(classifier.model, classifier.tokenizer, suffixes, **kwargs)
            generate_ms.append((time.perf_counter() - gen_t0) * 1000)

            chunk_total_ms.append((time.perf_counter() - chunk_t0) * 1000)

        return PhaseTimings(
            tokenize_ms=tokenize_ms,
            deepcopy_ms=deepcopy_ms,
            generate_ms=generate_ms,
            chunk_total_ms=chunk_total_ms,
            n_buckets=len(buckets),
        )

    @staticmethod
    def fmt_stats(values: list[float]) -> str:
        if not values:
            return "—"
        if len(values) == 1:
            return f"{values[0]:.1f}"
        return (
            f"mean={statistics.mean(values):>5.1f} "
            f"median={statistics.median(values):>5.1f} "
            f"min={min(values):>5.1f} "
            f"max={max(values):>5.1f}"
        )

    @classmethod
    def report_phase_breakdown(cls, label: str, t: PhaseTimings) -> None:
        click.echo(f"\n=== {label} (n_buckets={t.n_buckets}, n_chunks={len(t.chunk_total_ms)}) ===")
        click.echo(f"  tokenize_ms     {cls.fmt_stats(t.tokenize_ms)}")
        click.echo(f"  deepcopy_ms     {cls.fmt_stats(t.deepcopy_ms)}")
        click.echo(f"  generate_ms     {cls.fmt_stats(t.generate_ms)}")
        click.echo(f"  chunk_total_ms  {cls.fmt_stats(t.chunk_total_ms)}")
        click.echo(
            f"  → {t.per_bucket_ms:.1f} ms/bucket  "
            f"({1000 / t.per_bucket_ms:.1f} buckets/sec)"
        )

    @classmethod
    def run_batch_size_sweep(
        cls,
        classifier: SentimentClassifier,
        buckets: list[ConversationBucket],
        trials: int = 3,
    ) -> None:
        click.echo(f"\n=== BATCH_SIZE sweep (n_buckets={len(buckets)}, {trials} trials each) ===")
        click.echo(f"{'batch':>6} | {'ms/bucket trials':>30} | {'mean':>6} | {'min':>5} | {'max':>5} | {'b/sec_mean':>10}")
        click.echo("-" * 78)
        for bs in cls.BATCH_SIZES:
            if bs > len(buckets):
                continue
            per_bucket: list[float] = []
            for _ in range(trials):
                t = cls.run_chunk_with_breakdown(classifier, buckets, batch_size=bs, use_cache=True)
                per_bucket.append(t.per_bucket_ms)
            trial_str = " ".join(f"{v:>5.1f}" for v in per_bucket)
            mean_v = statistics.mean(per_bucket)
            click.echo(
                f"{bs:>6} | {trial_str:>30} | {mean_v:>5.1f} | "
                f"{min(per_bucket):>5.1f} | {max(per_bucket):>5.1f} | "
                f"{1000 / mean_v:>10.1f}"
            )

    @classmethod
    def run_sort_comparison(
        cls,
        classifier: SentimentClassifier,
        buckets: list[ConversationBucket],
        trials: int = 3,
    ) -> None:
        click.echo(f"\n=== Length-sort sweep (n_buckets={len(buckets)}, {trials} trials) ===")
        click.echo(f"{'batch':>6} | {'sorted':>7} | {'ms/bucket':>10} | {'b/sec':>6}")
        click.echo("-" * 45)
        for bs in (2, 4, 8, 16):
            for sort_flag in (False, True):
                rates: list[float] = []
                for _ in range(trials):
                    t = cls.run_chunk_with_breakdown(
                        classifier, buckets, batch_size=bs,
                        use_cache=True, sort_by_length=sort_flag,
                    )
                    rates.append(t.per_bucket_ms)
                mean_v = statistics.mean(rates)
                click.echo(
                    f"{bs:>6} | {str(sort_flag):>7} | {mean_v:>9.1f}  | {1000 / mean_v:>5.1f}"
                )

    @classmethod
    def run_full_profile(
        cls,
        n_buckets: int,
        model_repo: str,
    ) -> None:
        click.echo(f"\n[1/4] Collecting {n_buckets} buckets…")
        buckets = cls.collect_buckets(n_buckets)
        if len(buckets) < n_buckets:
            click.echo(f"  Only got {len(buckets)} buckets (asked for {n_buckets})")
        if not buckets:
            click.echo("No buckets — bail.")
            return

        click.echo(f"\n[2/4] Building SentimentClassifier ({model_repo})…")
        classifier = cls.build_classifier(model_repo)

        click.echo("\n[3/4] Warming up (first call always slow)…")
        cls.run_chunk_with_breakdown(classifier, buckets[:8], batch_size=8, use_cache=True)

        click.echo("\n[4/4] Profiling…")
        click.echo("\n--- copy.deepcopy(base_cache) in isolation ---")
        dc_solo = cls.time_deepcopy(classifier, n=20)
        click.echo(f"  {cls.fmt_stats(dc_solo)} ms (over 20 trials)")

        with_cache = cls.run_chunk_with_breakdown(classifier, buckets, batch_size=8, use_cache=True)
        cls.report_phase_breakdown("BATCH_SIZE=8 with deepcopy(prompt_caches)", with_cache)

        no_cache = cls.run_chunk_with_breakdown(classifier, buckets, batch_size=8, use_cache=False)
        cls.report_phase_breakdown("BATCH_SIZE=8 with prompt_caches=None", no_cache)

        cls.run_batch_size_sweep(classifier, buckets)
        cls.run_sort_comparison(classifier, buckets)

        click.echo("\nDone.")

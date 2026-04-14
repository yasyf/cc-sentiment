from __future__ import annotations

import click

from cc_sentiment.models import AppState


@click.group(invoke_without_command=True)
@click.option("--model", "model_repo", default=None, help="Model repo (HF for mlx/omlx) or name (claude)")
@click.pass_context
def main(ctx: click.Context, model_repo: str | None) -> None:
    if ctx.invoked_subcommand is not None:
        return

    from cc_sentiment.tui import CCSentimentApp
    CCSentimentApp(state=AppState.load(), model_repo=model_repo).run()


@main.command(hidden=True)
@click.option("--transcripts", default=10, help="Max transcripts to benchmark")
@click.option("--runs", default=1, help="Timed runs per engine")
@click.option("--engines", default="mlx", help="Comma-separated engines")
@click.option("--model", "model_repo", default=None)
@click.option("--scaling", is_flag=True, help="Run scaling test across bucket sizes")
@click.option("--accuracy", is_flag=True, help="Run accuracy test against labeled dataset")
def benchmark(
    transcripts: int, runs: int, engines: str,
    model_repo: str | None, scaling: bool, accuracy: bool,
) -> None:
    from cc_sentiment.benchmark import run_accuracy_test, run_benchmark

    if accuracy:
        run_accuracy_test(engines.split(",")[0].strip(), model_repo)
        return

    run_benchmark(
        max_transcripts=transcripts,
        runs=runs,
        engines=[e.strip() for e in engines.split(",")],
        model_repo=model_repo,
        scaling_test=scaling,
    )

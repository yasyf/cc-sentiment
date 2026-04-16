from __future__ import annotations

import sys

import anyio
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


@main.command()
def setup() -> None:
    from cc_sentiment.tui import CCSentimentApp
    CCSentimentApp(state=AppState.load(), setup_only=True).run()


@main.command()
def install() -> None:
    from cc_sentiment.daemon import LaunchAgent

    LaunchAgent.install()
    click.echo(
        "Scheduled daily. Your transcripts will be scored and uploaded in the background. "
        "Undo with `cc-sentiment uninstall`."
    )


@main.command()
def uninstall() -> None:
    from cc_sentiment.daemon import LaunchAgent

    if not LaunchAgent.is_installed():
        click.echo("Not scheduled — nothing to remove.")
        return
    LaunchAgent.uninstall()
    click.echo("Removed the daily schedule.")


@main.command()
def run() -> None:
    from cc_sentiment.headless import (
        HeadlessAuthError,
        HeadlessClaudeEngineBlocked,
        HeadlessNotConfigured,
        HeadlessNothingToDo,
        HeadlessOk,
        HeadlessRunner,
        HeadlessUploadError,
    )
    from cc_sentiment.repo import Repository

    state = AppState.load()
    repo = Repository.open(Repository.default_path())
    try:
        outcome = anyio.run(HeadlessRunner.run, state, repo)
    finally:
        repo.close()

    match outcome:
        case HeadlessOk(scored=s, uploaded=u):
            click.echo(f"Scored {s}, uploaded {u}.")
        case HeadlessNothingToDo():
            click.echo("Nothing new to score.")
        case HeadlessNotConfigured():
            click.echo(
                "Not configured yet. Run `cc-sentiment setup` first.", err=True
            )
            sys.exit(2)
        case HeadlessClaudeEngineBlocked():
            click.echo(
                "The claude engine needs interactive cost confirmation. "
                "Run `cc-sentiment` instead.",
                err=True,
            )
            sys.exit(2)
        case HeadlessAuthError(detail=d):
            click.echo(d, err=True)
            sys.exit(3)
        case HeadlessUploadError(detail=d):
            click.echo(d, err=True)
            sys.exit(4)


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

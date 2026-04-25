from __future__ import annotations

import contextlib
import os
import subprocess
import sys

os.environ.setdefault("PYTHONNODEBUGRANGES", "1")

import anyio
import click
import httpx
from rich.console import Console

from cc_sentiment.models import AppState

DAEMON_PING_ERRORS = (httpx.HTTPError, subprocess.CalledProcessError, OSError, TimeoutError)


@click.group(invoke_without_command=True)
@click.option("--model", "model_repo", default=None, help="Model repo (HF for mlx) or name (claude)")
@click.option(
    "--debug",
    is_flag=True,
    default=lambda: os.environ.get("DEBUG") == "1",
    help="Verbose diagnostics (also: DEBUG=1)",
)
@click.pass_context
def main(ctx: click.Context, model_repo: str | None, debug: bool) -> None:
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug

    from cc_sentiment.updater import SelfUpdater
    SelfUpdater.maybe_upgrade()

    if ctx.invoked_subcommand is not None:
        return

    with Console().status("[dim]Starting cc-sentiment…[/]"):
        from cc_sentiment.tui import CCSentimentApp
        app = CCSentimentApp(state=AppState.load(), model_repo=model_repo, debug=debug)
    app.run()


@main.command()
@click.pass_context
def setup(ctx: click.Context) -> None:
    with Console().status("[dim]Starting cc-sentiment…[/]"):
        from cc_sentiment.tui import CCSentimentApp
        app = CCSentimentApp(state=AppState.load(), setup_only=True, debug=ctx.obj["debug"])
    app.run()


@main.command()
def install() -> None:
    from cc_sentiment.daemon import LaunchAgent
    from cc_sentiment.upload import Uploader

    LaunchAgent.install()
    with contextlib.suppress(*DAEMON_PING_ERRORS):
        anyio.run(Uploader.ping_daemon_event, "install")
    click.echo(
        "Scheduled daily. Your transcripts will be scored and uploaded in the background. "
        "Undo with `cc-sentiment uninstall`."
    )


@main.command()
def uninstall() -> None:
    from cc_sentiment.daemon import LaunchAgent
    from cc_sentiment.upload import Uploader

    if not LaunchAgent.is_installed():
        click.echo("Not scheduled — nothing to remove.")
        return
    LaunchAgent.uninstall()
    with contextlib.suppress(*DAEMON_PING_ERRORS):
        anyio.run(Uploader.ping_daemon_event, "uninstall")
    click.echo("Removed the daily schedule.")


@main.command()
@click.pass_context
def run(ctx: click.Context) -> None:
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

    debug = ctx.obj["debug"]
    state = AppState.load()
    repo = Repository.open(Repository.default_path())
    try:
        outcome = anyio.run(HeadlessRunner.run, state, repo, debug)
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
def benchmark(
    transcripts: int, runs: int, engines: str,
    model_repo: str | None, scaling: bool,
) -> None:
    from cc_sentiment.benchmark import BenchmarkRunner

    BenchmarkRunner.run_benchmark(
        max_transcripts=transcripts,
        runs=runs,
        engines=[e.strip() for e in engines.split(",")],
        model_repo=model_repo,
        scaling_test=scaling,
    )


@main.command(hidden=True)
@click.option("--buckets", default=100, help="Buckets to profile")
@click.option("--model", "model_repo", default=None)
def profile(buckets: int, model_repo: str | None) -> None:
    from cc_sentiment.engines.protocol import DEFAULT_MODEL
    from cc_sentiment.profiling import Profiler

    Profiler.run_full_profile(
        n_buckets=buckets,
        model_repo=model_repo or DEFAULT_MODEL,
    )

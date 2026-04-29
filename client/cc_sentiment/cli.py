from __future__ import annotations

import contextlib
import os
import subprocess
from typing import Annotated

os.environ.setdefault("PYTHONNODEBUGRANGES", "1")

import anyio
import httpx
import typer
from rich.console import Console

from cc_sentiment.models import AppState
from cc_sentiment.onboarding.capabilities import Capabilities

DAEMON_PING_ERRORS = (httpx.HTTPError, subprocess.CalledProcessError, OSError, TimeoutError)

app = typer.Typer(
    invoke_without_command=True,
    no_args_is_help=False,
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    model_repo: Annotated[
        str | None,
        typer.Option("--model", help="Model repo (HF for mlx) or name (claude)"),
    ] = None,
    debug: Annotated[
        bool,
        typer.Option("--debug", help="Verbose diagnostics (also: DEBUG=1)"),
    ] = False,
) -> None:
    debug = debug or os.environ.get("DEBUG") == "1"
    ctx.ensure_object(dict)
    ctx.obj["debug"] = debug

    from cc_sentiment.updater import SelfUpdater
    SelfUpdater.maybe_upgrade()

    if ctx.invoked_subcommand is not None:
        return

    with Console().status("[dim]Starting cc-sentiment…[/]"):
        from cc_sentiment.tui import CCSentimentApp
        tui_app = CCSentimentApp(state=AppState.load(), model_repo=model_repo, debug=debug)
    tui_app.run()


@app.command()
def setup(
    ctx: typer.Context,
    no_gh: Annotated[bool, typer.Option("--no-gh", hidden=True)] = False,
    no_gh_auth: Annotated[bool, typer.Option("--no-gh-auth", hidden=True)] = False,
    no_gpg: Annotated[bool, typer.Option("--no-gpg", hidden=True)] = False,
    no_ssh_keygen: Annotated[bool, typer.Option("--no-ssh-keygen", hidden=True)] = False,
    no_brew: Annotated[bool, typer.Option("--no-brew", hidden=True)] = False,
) -> None:
    if overrides := {
        cap: False
        for flag, caps in (
            (no_gh, ("has_gh", "gh_authenticated")),
            (no_gh_auth, ("gh_authenticated",)),
            (no_gpg, ("has_gpg",)),
            (no_ssh_keygen, ("has_ssh_keygen",)),
            (no_brew, ("has_brew",)),
        )
        if flag
        for cap in caps
    }:
        Capabilities.reset()
        Capabilities.seed(**overrides)

    with Console().status("[dim]Starting cc-sentiment…[/]"):
        from cc_sentiment.tui import CCSentimentApp
        tui_app = CCSentimentApp(state=AppState(), setup_only=True, debug=ctx.obj["debug"])
    tui_app.run()


@app.command()
def install() -> None:
    from cc_sentiment.daemon import LaunchAgent
    from cc_sentiment.upload import Uploader

    LaunchAgent.install()
    with contextlib.suppress(*DAEMON_PING_ERRORS):
        anyio.run(Uploader.ping_daemon_event, "install")
    typer.echo(
        "Scheduled daily. Your transcripts will be scored and uploaded in the background. "
        "Undo with `cc-sentiment uninstall`."
    )


@app.command()
def uninstall() -> None:
    from cc_sentiment.daemon import LaunchAgent
    from cc_sentiment.upload import Uploader

    if not LaunchAgent.is_installed():
        typer.echo("Not scheduled — nothing to remove.")
        return
    LaunchAgent.uninstall()
    with contextlib.suppress(*DAEMON_PING_ERRORS):
        anyio.run(Uploader.ping_daemon_event, "uninstall")
    typer.echo("Removed the daily schedule.")


@app.command()
def run(ctx: typer.Context) -> None:
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
    with Repository.open(Repository.default_path()) as repo:
        outcome = anyio.run(HeadlessRunner.run, state, repo, debug)

    match outcome:
        case HeadlessOk(scored=s, uploaded=u):
            typer.echo(f"Scored {s}, uploaded {u}.")
        case HeadlessNothingToDo():
            typer.echo("Nothing new to score.")
        case HeadlessNotConfigured():
            typer.echo("Not configured yet. Run `cc-sentiment setup` first.", err=True)
            raise typer.Exit(2)
        case HeadlessClaudeEngineBlocked():
            typer.echo(
                "Claude scoring needs confirmation. Run `cc-sentiment` instead.",
                err=True,
            )
            raise typer.Exit(2)
        case HeadlessAuthError(detail=d):
            typer.echo(d, err=True)
            raise typer.Exit(3)
        case HeadlessUploadError(detail=d):
            typer.echo(d, err=True)
            raise typer.Exit(4)


@app.command(hidden=True)
def lookup(
    bucket_hash: Annotated[str, typer.Argument(help="8-char bucket hash from --debug TUI")],
) -> None:
    from cc_sentiment.debug import BucketLookup
    from cc_sentiment.repo import Repository

    with Repository.open(Repository.default_path()) as repo:
        result = anyio.run(BucketLookup.find, repo, bucket_hash)
    if result is None:
        typer.echo(f"No bucket found for hash {bucket_hash!r}.", err=True)
        raise typer.Exit(1)
    typer.echo(BucketLookup.format(result))


@app.command(hidden=True)
def benchmark(
    transcripts: Annotated[
        int, typer.Option("--transcripts", help="Max transcripts to benchmark")
    ] = 10,
    runs: Annotated[int, typer.Option("--runs", help="Timed runs per engine")] = 1,
    engines: Annotated[
        str, typer.Option("--engines", help="Comma-separated engines")
    ] = "mlx",
    model_repo: Annotated[str | None, typer.Option("--model")] = None,
    scaling: Annotated[
        bool, typer.Option("--scaling", help="Run scaling test across bucket sizes")
    ] = False,
) -> None:
    from cc_sentiment.benchmark import BenchmarkRunner

    BenchmarkRunner.run_benchmark(
        max_transcripts=transcripts,
        runs=runs,
        engines=[e.strip() for e in engines.split(",")],
        model_repo=model_repo,
        scaling_test=scaling,
    )


@app.command(hidden=True)
def profile(
    buckets: Annotated[int, typer.Option("--buckets", help="Buckets to profile")] = 100,
    model_repo: Annotated[str | None, typer.Option("--model")] = None,
) -> None:
    from cc_sentiment.engines.protocol import DEFAULT_MODEL
    from cc_sentiment.profiling import Profiler

    Profiler.run_full_profile(
        n_buckets=buckets,
        model_repo=model_repo or DEFAULT_MODEL,
    )


def entrypoint() -> None:
    from cc_sentiment.observability import Crash
    Crash.init()
    app()

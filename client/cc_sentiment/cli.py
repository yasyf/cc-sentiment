from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from statistics import mean, median

import click
from rich.console import Console
from rich.table import Table

from cc_sentiment.models import AppState, GPGConfig, SentimentRecord, SSHConfig
from cc_sentiment.signing import GPGBackend, KeyDiscovery, SSHBackend

SCORE_COLORS: dict[int, str] = {1: "red", 2: "red", 3: "yellow", 4: "green", 5: "green"}
SCORE_LABELS: dict[int, str] = {
    1: "frustrated", 2: "annoyed", 3: "neutral", 4: "satisfied", 5: "delighted",
}

console = Console()


def detect_git_username() -> str | None:
    for cmd in (
        ["gh", "api", "user", "--jq", ".login"],
        ["git", "config", "github.user"],
    ):
        if not shutil.which(cmd[0]):
            continue
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def auto_setup(state: AppState) -> bool:
    username = detect_git_username()

    if username:
        if backend := KeyDiscovery.match_ssh_key(username):
            state.config = SSHConfig(github_username=username, key_path=backend.private_key_path)
            state.save()
            console.print(f"[green]Auto-configured:[/] {username} with SSH key {backend.private_key_path}")
            return True

        if backend := KeyDiscovery.match_gpg_key(username):
            state.config = GPGConfig(github_username=username, fpr=backend.fpr)
            state.save()
            console.print(f"[green]Auto-configured:[/] {username} with GPG key {backend.fpr[-8:]}")
            return True

    for info in KeyDiscovery.find_gpg_keys():
        if KeyDiscovery.fetch_openpgp_key(info.fpr):
            identity = username or info.fpr
            state.config = GPGConfig(github_username=identity, fpr=info.fpr)
            state.save()
            label = username or f"GPG {info.fpr[-8:]}"
            console.print(f"[green]Auto-configured:[/] {label} with GPG key on keys.openpgp.org")
            return True

    return False


def ensure_config(state: AppState) -> None:
    if state.config is not None:
        return

    if auto_setup(state):
        return

    from cc_sentiment.tui import SetupApp
    SetupApp().run()
    state.config = AppState.load().config
    assert state.config is not None, "Setup was not completed."


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    if ctx.invoked_subcommand is not None:
        return

    state = AppState.load()
    ensure_config(state)

    from cc_sentiment.tui import ScanApp
    ScanApp(state=state, engine="omlx", model_repo=None, limit=None, do_upload=True).run()


@main.command()
def setup() -> None:
    from cc_sentiment.tui import SetupApp
    SetupApp().run()


@main.command()
@click.option("--upload", "do_upload", is_flag=True, help="Upload results after scan")
@click.option("--engine", type=click.Choice(["mlx", "omlx"]), default="omlx")
@click.option("--model", "model_repo", default=None, help="HuggingFace model repo")
@click.option("--limit", default=None, type=int, help="Max transcripts to process")
def scan(do_upload: bool, engine: str, model_repo: str | None, limit: int | None) -> None:
    state = AppState.load()

    if do_upload:
        ensure_config(state)

    from cc_sentiment.tui import ScanApp
    ScanApp(state=state, engine=engine, model_repo=model_repo, limit=limit, do_upload=do_upload).run()


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


@main.command()
def upload() -> None:
    from cc_sentiment.upload import Uploader

    state = AppState.load()
    ensure_config(state)

    records = Uploader.records_from_state(state)
    if not records:
        console.print("No pending records to upload.")
        return

    from cc_sentiment.tui import ScanApp
    ScanApp(state=state, engine="omlx", model_repo=None, limit=0, do_upload=True).run()


@main.command()
@click.option("--engine", type=click.Choice(["mlx", "omlx"]), default="omlx")
@click.option("--model", "model_repo", default=None)
def rescan(engine: str, model_repo: str | None) -> None:
    from cc_sentiment.pipeline import Pipeline

    import anyio

    state = AppState.load()
    prev_sessions = len(state.sessions)
    state.processed_files.clear()
    state.sessions.clear()
    state.save()

    console.print(f"Cleared {prev_sessions} sessions. Re-running full scan...\n")

    async def do_rescan() -> list[SentimentRecord]:
        return await Pipeline.run(state, engine, model_repo)

    all_records = anyio.run(do_rescan)

    if not all_records:
        console.print("No records produced during rescan.")
        return

    print_summary(all_records)


def print_summary(records: list[SentimentRecord]) -> None:
    scores = [int(r.sentiment_score) for r in records]
    counts = Counter(scores)
    total = len(scores)
    sessions = len({r.conversation_id for r in records})

    console.print()
    table = Table(title="Score Distribution", show_header=False, box=None, padding=(0, 1))
    table.add_column(justify="right", width=3)
    table.add_column(width=12)
    table.add_column(width=25)
    table.add_column(justify="right", width=6)
    table.add_column(justify="right", width=5)

    for s in range(1, 6):
        n = counts.get(s, 0)
        pct = 100 * n / total
        bar_len = int(20 * n / max(counts.values())) if counts else 0
        color = SCORE_COLORS[s]
        table.add_row(
            f"[{color}]{s}[/]",
            f"({SCORE_LABELS[s]})",
            f"[{color}]{'█' * bar_len}[/]",
            f"{pct:.0f}%",
            f"({n})",
        )

    console.print(table)
    console.print(
        f"  mean={mean(scores):.1f}  median={median(scores):.0f}  "
        f"{total} buckets from {sessions} sessions"
    )

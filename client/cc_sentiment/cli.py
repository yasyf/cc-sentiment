from __future__ import annotations

import subprocess
import time
from collections import Counter
from statistics import mean, median

import anyio
import click
import httpx
from rich.console import Console
from rich.live import Live
from rich.table import Table

from cc_sentiment.models import AppState, ClientConfig, SentimentRecord

SCORE_COLORS: dict[int, str] = {1: "red", 2: "red", 3: "yellow", 4: "green", 5: "green"}
SCORE_LABELS: dict[int, str] = {
    1: "frustrated", 2: "annoyed", 3: "neutral", 4: "satisfied", 5: "delighted",
}

CHIP_SPEED: dict[str, float] = {
    "M1": 1.5, "M2": 2.0, "M3": 2.5, "M4": 2.8, "M5": 3.2,
}
DEFAULT_SPEED = 1.5

console = Console()


def get_chip_family() -> str | None:
    try:
        brand = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        for chip in ("M5", "M4", "M3", "M2", "M1"):
            if chip in brand:
                return chip
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return None


def get_memory_gb() -> int:
    try:
        raw = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        return int(raw) // (1024 ** 3)
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return 0


def estimate_speed() -> float:
    chip = get_chip_family()
    return CHIP_SPEED.get(chip, DEFAULT_SPEED) if chip else DEFAULT_SPEED


class LiveStats:
    def __init__(self, total_transcripts: int) -> None:
        self.total = total_transcripts
        self.scored = 0
        self.uploaded = 0
        self.records: list[SentimentRecord] = []
        self.start = time.monotonic()
        self.estimated_speed = estimate_speed()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.start

    @property
    def rate(self) -> float:
        return self.scored / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def eta_seconds(self) -> float | None:
        remaining = self.total - self.scored
        if remaining <= 0:
            return 0.0
        speed = self.rate if self.scored >= 3 else self.estimated_speed
        return remaining / speed if speed > 0 else None

    def format_eta(self) -> str:
        eta = self.eta_seconds
        if eta is None:
            return "calculating..."
        if eta < 60:
            return f"~{eta:.0f}s"
        if eta < 3600:
            return f"~{eta / 60:.0f}m"
        return f"~{eta / 3600:.1f}h"

    def render(self) -> Table:
        grid = Table.grid(padding=(0, 2))
        grid.add_column(justify="right", style="bold")
        grid.add_column()

        elapsed_s = f"{self.elapsed:.0f}s"
        rate_str = f"{self.rate:.1f} files/s" if self.scored > 0 else "starting..."
        grid.add_row("Transcripts", f"{self.scored}/{self.total}  ({elapsed_s}, {rate_str})")
        grid.add_row("ETA", self.format_eta())
        grid.add_row("Buckets", str(len(self.records)))
        grid.add_row("Uploaded", str(self.uploaded))

        if self.records:
            counts = Counter(int(r.sentiment_score) for r in self.records)
            total = len(self.records)
            bars = []
            for s in range(1, 6):
                n = counts.get(s, 0)
                pct = 100 * n / total
                bar_len = int(20 * n / max(counts.values())) if counts else 0
                color = SCORE_COLORS[s]
                bars.append(f"  [{color}]{s}[/] {'█' * bar_len} {pct:.0f}% ({n})")
            grid.add_row("Scores", "\n".join(bars))

            scores = [int(r.sentiment_score) for r in self.records]
            grid.add_row("Stats", f"mean={mean(scores):.1f}  median={median(scores):.0f}")

        return grid


def detect_git_username() -> str | None:
    for cmd in (
        ["git", "config", "github.user"],
        ["gh", "api", "user", "--jq", ".login"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    return None


def auto_setup(state: AppState) -> bool:
    from cc_sentiment.signing import KeyDiscovery, NoGitHubKeysError

    if not (username := detect_git_username()):
        return False
    try:
        key_path = KeyDiscovery.match_github_key(username)
    except NoGitHubKeysError:
        return False
    except (ValueError, FileNotFoundError):
        return False

    state.config = ClientConfig(github_username=username, key_path=key_path)
    state.save()
    console.print(f"[green]Auto-configured:[/] {username} with key {key_path}")
    return True


def ensure_config(state: AppState) -> None:
    if state.config is not None:
        return

    console.print("No configuration found. Attempting auto-setup...")
    if auto_setup(state):
        return

    console.print("Auto-setup failed. Running interactive setup...\n")
    run_interactive_setup(state)


def run_interactive_setup(state: AppState) -> None:
    from cc_sentiment.signing import KeyDiscovery, NoGitHubKeysError

    username = click.prompt("GitHub username", default=detect_git_username())
    console.print(f"Fetching SSH keys for {username}...")

    try:
        key_path = KeyDiscovery.match_github_key(username)
    except NoGitHubKeysError as e:
        console.print(f"[yellow]Warning:[/] {e}")
        local_pub = KeyDiscovery.read_public_key(e.local_key_path)
        console.print(f"  Local key: [dim]{local_pub[:60]}...[/]")
        console.print(f"  Upload it at: [link]https://github.com/settings/ssh/new[/link]")
        if not click.confirm("Proceed with local key anyway? (upload will fail until key is on GitHub)"):
            raise SystemExit(1)
        key_path = e.local_key_path

    state.config = ClientConfig(github_username=username, key_path=key_path)
    state.save()
    console.print(f"[green]Configuration saved.[/] Using key: {key_path}")


def verify_credentials(state: AppState) -> None:
    from cc_sentiment.upload import Uploader

    assert state.config is not None
    console.print("Verifying upload credentials...", end=" ")
    uploader = Uploader()
    try:
        anyio.run(uploader.verify_credentials, state.config)
        console.print("[green]OK[/]")
    except (httpx.HTTPStatusError, httpx.ConnectError) as e:
        console.print(f"[red]FAILED[/]\n\nServer rejected credentials: {e}")
        raise SystemExit(1)


def show_welcome() -> None:
    chip = get_chip_family() or "Apple Silicon"
    mem = get_memory_gb()
    mem_str = f" with {mem}GB RAM" if mem else ""

    console.print(
        f"\n[bold cyan]Welcome to cc-sentiment![/]\n\n"
        f"This tool analyzes your Claude Code sessions to track developer sentiment\n"
        f"over time. [bold]Everything runs locally[/] on your machine -- your transcripts\n"
        f"never leave your computer during analysis.\n\n"
        f"  [dim]How it works:[/]\n"
        f"  1. Discovers Claude Code transcripts in ~/.claude/projects/\n"
        f"  2. Scores each conversation segment using a local ML model ({chip}{mem_str})\n"
        f"  3. Optionally uploads anonymous scores to the dashboard\n\n"
        f"  [dim]The model runs entirely via Apple Silicon GPU -- no API calls, no cloud.[/]\n"
    )


@click.group()
def main() -> None:
    pass


@main.command()
def setup() -> None:
    state = AppState.load()
    run_interactive_setup(state)


@main.command()
@click.option("--upload", "do_upload", is_flag=True, help="Upload results after scan")
@click.option("--engine", type=click.Choice(["mlx", "omlx"]), default="omlx")
@click.option("--model", "model_repo", default=None, help="HuggingFace model repo")
@click.option("--limit", default=None, type=int, help="Max transcripts to process")
def scan(do_upload: bool, engine: str, model_repo: str | None, limit: int | None) -> None:
    from cc_sentiment.pipeline import Pipeline
    from cc_sentiment.upload import Uploader

    state = AppState.load()

    first_run = not state.processed_files and not state.sessions
    if first_run:
        show_welcome()

    if do_upload:
        ensure_config(state)
        verify_credentials(state)

    new_transcripts = Pipeline.discover_new_transcripts(state)
    if limit is not None:
        new_transcripts = new_transcripts[:limit]
    if not new_transcripts:
        if first_run:
            console.print(
                "[yellow]No Claude Code transcripts found.[/]\n"
                "Make sure you've used Claude Code at least once. Transcripts live in\n"
                "~/.claude/projects/\n"
            )
        else:
            console.print("No new transcripts found. Everything is up to date.")
        return

    already = len(state.processed_files)
    if already:
        console.print(
            f"[dim]Resuming:[/] {already} transcripts already cached, "
            f"{len(new_transcripts)} new/modified to process."
        )
    else:
        console.print(f"Found {len(new_transcripts)} transcripts to process.")

    console.print(f"Loading [bold]{engine}[/] engine (local ML inference)...\n")

    uploader = Uploader() if do_upload else None
    stats = LiveStats(len(new_transcripts))

    async def do_scan_live() -> list[SentimentRecord]:
        def on_records(records: list[SentimentRecord]) -> None:
            stats.records.extend(records)
            stats.scored += 1
            live.update(stats.render())

        return await Pipeline.run(
            state, engine, model_repo=model_repo,
            new_transcripts=new_transcripts, on_records=on_records,
        )

    with Live(stats.render(), console=console, refresh_per_second=4) as live:
        all_records = anyio.run(do_scan_live)

    if uploader and all_records:
        try:
            anyio.run(uploader.upload, all_records, state)
            stats.uploaded += len(all_records)
        except Exception as e:
            console.print(f"[yellow]Upload failed: {e}[/]")

    if not all_records:
        console.print("No buckets scored.")
        return

    print_summary(all_records)

    if uploader:
        console.print(f"\n[green]Done.[/] {stats.uploaded} records uploaded.")


@main.command()
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
    verify_credentials(state)

    records = Uploader.records_from_state(state)
    if not records:
        console.print("No pending records to upload.")
        return

    uploader = Uploader()
    console.print(f"Uploading {len(records)} records...")
    anyio.run(uploader.upload, records, state)
    console.print("[green]Upload complete.[/]")


@main.command()
@click.option("--engine", type=click.Choice(["mlx", "omlx"]), default="omlx")
@click.option("--model", "model_repo", default=None)
def rescan(engine: str, model_repo: str | None) -> None:
    from cc_sentiment.pipeline import Pipeline

    state = AppState.load()
    prev_sessions = len(state.sessions)
    state.processed_files.clear()
    state.sessions.clear()
    state.save()

    console.print(f"Cleared {prev_sessions} sessions. Re-running full scan...\n")

    all_records = anyio.run(Pipeline.run, state, engine, model_repo)

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

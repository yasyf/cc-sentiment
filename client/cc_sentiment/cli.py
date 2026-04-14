from __future__ import annotations

import shutil
import subprocess
from collections import Counter
from statistics import mean, median

import click
from rich.console import Console
from rich.table import Table

from cc_sentiment.models import AppState, ContributorId, GPGConfig, SentimentRecord, SSHConfig
from cc_sentiment.signing import GPGKeyInfo, KeyDiscovery, SSHKeyInfo
from cc_sentiment.upload import Uploader

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


ALMOST_THERE_DETAIL = (
    "We found a signing key on your machine ({key_display}), "
    "but it's not linked to your GitHub account yet.\n\n"
    "We can add it for you — this just lets the dashboard verify "
    "that uploads came from you. It won't change anything else "
    "about your GitHub account."
)

ONE_TIME_SETUP_DETAIL = (
    "To verify your uploads, we need a signing key. "
    "We can create one for you automatically — it's a small file "
    "that lives on your machine and proves your identity.\n\n"
    "We'll also link it to your GitHub account so the "
    "dashboard can verify your data."
)


def try_config(state: AppState, config: SSHConfig | GPGConfig, label: str) -> bool:
    state.config = config
    state.save()
    if Uploader.verify_config(config):
        console.print(f"[green]All set![/] Verified as {label}.")
        return True
    console.print("[dim]Hmm, the dashboard couldn't verify that config. Trying another approach...[/]")
    state.config = None
    state.save()
    return False


def confirm_action(title: str, detail: str, confirm_label: str) -> bool:
    from cc_sentiment.tui import ConfirmActionApp
    return ConfirmActionApp(title=title, detail=detail, confirm_label=confirm_label).run()


def try_link_local_key(
    state: AppState, username: str, key: SSHKeyInfo | GPGKeyInfo,
) -> bool:
    match key:
        case SSHKeyInfo(path=p):
            key_display, upload, config = (
                str(p),
                lambda: KeyDiscovery.upload_github_ssh_key(key),
                SSHConfig(contributor_id=ContributorId(username), key_path=p),
            )
        case GPGKeyInfo(fpr=f):
            key_display, upload, config = (
                f"GPG {f[-8:]}",
                lambda: KeyDiscovery.upload_github_gpg_key(key),
                GPGConfig(contributor_type="github", contributor_id=ContributorId(username), fpr=f),
            )
    if not confirm_action(
        title="Almost there",
        detail=ALMOST_THERE_DETAIL.format(key_display=key_display),
        confirm_label="Link key to GitHub",
    ):
        return False
    if not upload():
        return False
    return try_config(state, config, username)


def auto_setup(state: AppState) -> bool:
    console.print("[dim]Checking GitHub username...[/]", end=" ")
    username = detect_git_username()
    console.print(f"found: {username}" if username else "not found")

    if username:
        console.print("[dim]Looking for signing keys...[/]", end=" ")
        if backend := KeyDiscovery.match_ssh_key(username):
            console.print("found SSH key, already on GitHub")
            if try_config(
                state,
                SSHConfig(contributor_id=ContributorId(username), key_path=backend.private_key_path),
                username,
            ):
                return True

        if backend := KeyDiscovery.match_gpg_key(username):
            console.print("found GPG key, already on GitHub")
            if try_config(
                state,
                GPGConfig(contributor_type="github", contributor_id=ContributorId(username), fpr=backend.fpr),
                username,
            ):
                return True

        if shutil.which("gh"):
            for key in (*KeyDiscovery.find_ssh_keys(), *KeyDiscovery.find_gpg_keys()):
                match key:
                    case SSHKeyInfo(path=p):
                        console.print(f"found SSH key ({p.name}), not on GitHub yet")
                    case GPGKeyInfo(fpr=f):
                        console.print(f"found GPG key ({f[-8:]}), not on GitHub yet")
                if try_link_local_key(state, username, key):
                    return True

    for info in KeyDiscovery.find_gpg_keys():
        if not KeyDiscovery.fetch_openpgp_key(info.fpr):
            continue
        config, label = (
            (GPGConfig(contributor_type="github", contributor_id=ContributorId(username), fpr=info.fpr), username)
            if username
            else (GPGConfig(contributor_type="gpg", contributor_id=ContributorId(info.fpr), fpr=info.fpr), f"GPG {info.fpr[-8:]}")
        )
        if try_config(state, config, label):
            return True

    if username and shutil.which("gh") and confirm_action(
        title="One-time setup",
        detail=ONE_TIME_SETUP_DETAIL,
        confirm_label="Set up automatically",
    ):
        new_key = KeyDiscovery.generate_ssh_key()
        if KeyDiscovery.upload_github_ssh_key(new_key) and try_config(
            state,
            SSHConfig(contributor_id=ContributorId(username), key_path=new_key.path),
            username,
        ):
            return True

    return False


def ensure_config(state: AppState) -> None:
    if state.config is not None:
        match state.config:
            case SSHConfig(key_path=p) if not p.exists():
                console.print(
                    f"[yellow]Your signing key ({p}) seems to have moved or been deleted. "
                    f"Let's set up a new one.[/]"
                )
                state.config = None
                state.save()
            case _:
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
    state = AppState.load()
    ensure_config(state)

    if not Uploader.records_from_state(state):
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

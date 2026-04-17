from __future__ import annotations

import asyncio
import random
import shutil
import subprocess
import tempfile
import time
import webbrowser
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean
from typing import ClassVar

import anyio
import anyio.to_thread
import httpx
from urllib.parse import urlencode
from rich.spinner import Spinner
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Digits,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    Rule,
    Static,
)

from cc_sentiment.daemon import LaunchAgent
from cc_sentiment.engines import (
    ClaudeCLIEngine,
    default_engine,
    resolve_engine,
)
from cc_sentiment.hardware import Hardware
from cc_sentiment.models import (
    CLIENT_VERSION,
    AppState,
    ContributorId,
    GistConfig,
    GPGConfig,
    MyStat,
    SentimentRecord,
    SSHConfig,
)
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import TranscriptDiscovery
from cc_sentiment.signing import (
    GPGBackend,
    GPGKeyInfo,
    KeyDiscovery,
    SSHBackend,
    SSHKeyInfo,
)

@dataclass(frozen=True)
class Stage:
    pass


@dataclass(frozen=True)
class Booting(Stage):
    pass


@dataclass(frozen=True)
class Authenticating(Stage):
    pass


@dataclass(frozen=True)
class Discovering(Stage):
    pass


@dataclass(frozen=True)
class Scoring(Stage):
    total: int
    engine: str


@dataclass(frozen=True)
class Uploading(Stage):
    pass


@dataclass(frozen=True)
class IdleEmpty(Stage):
    pass


@dataclass(frozen=True)
class IdleCaughtUp(Stage):
    total_buckets: int
    total_sessions: int
    total_files: int


@dataclass(frozen=True)
class IdleAfterUpload(Stage):
    total_buckets: int
    total_sessions: int
    total_files: int


@dataclass(frozen=True)
class Error(Stage):
    message: str


@dataclass(frozen=True)
class RescanConfirm(Stage):
    prev: Stage


@dataclass(frozen=True)
class StatFetchOk:
    stat: MyStat


@dataclass(frozen=True)
class StatFetchEmpty:
    pass


@dataclass(frozen=True)
class StatFetchHttpError:
    detail: str


StatFetchResult = StatFetchOk | StatFetchEmpty | StatFetchHttpError


def format_duration(seconds: float) -> str:
    if seconds < 30:
        return "a few seconds"
    if seconds < 3600:
        return f"~{max(1, round(seconds / 60))} min"
    hours = max(1, round(seconds / 3600))
    return f"~{hours} hour" if hours == 1 else f"~{hours} hours"


def format_hms(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def format_hour(hour: int) -> str:
    match hour:
        case 0:
            return "12am"
        case h if h < 12:
            return f"{h}am"
        case 12:
            return "12pm"
        case h:
            return f"{h - 12}pm"


def append_line(widget: Static | Label, addition: str) -> None:
    existing = str(widget.render())
    widget.update(f"{existing}\n{addition}".strip())


@dataclass(frozen=True)
class Phase:
    emitter: StatusEmitter
    idx: int

    def ok(self, label: str) -> None:
        self.emitter.replace(self.idx, "[green]✓[/]", label)

    def skip(self, label: str) -> None:
        self.emitter.replace(self.idx, "[yellow]—[/]", label)

    def unreachable(self, label: str) -> None:
        self.emitter.replace(self.idx, "[yellow]?[/]", label)


@dataclass
class StatusEmitter:
    widget: Static
    lines: list[str] = field(default_factory=list)

    def begin(self, label: str) -> Phase:
        self.lines.append(f"  [dim]...[/] [dim]{label}[/]")
        self.widget.update("\n".join(self.lines))
        return Phase(self, len(self.lines) - 1)

    def replace(self, idx: int, marker: str, label: str) -> None:
        self.lines[idx] = f"  {marker} [dim]{label}[/]"
        self.widget.update("\n".join(self.lines))


@dataclass(frozen=True)
class AutoSetup:
    state: AppState
    emit: StatusEmitter

    async def run(self) -> tuple[bool, str | None]:
        username = await self.detect_username()
        if username:
            if (c := await self.try_github_ssh(username)) and await self.probe_and_save(c):
                return True, username
            if (c := await self.try_github_gpg(username)) and await self.probe_and_save(c):
                return True, username
            if (c := await self.try_existing_gist(username)) and await self.probe_and_save(c):
                return True, username
        for info in await self.find_local_gpg():
            if (c := await self.try_openpgp(info, username)) and await self.probe_and_save(c):
                return True, username
        return False, username

    @staticmethod
    def find_git_username() -> str | None:
        for cmd in (
            ["gh", "api", "user", "--jq", ".login"],
            ["git", "config", "github.user"],
        ):
            if not shutil.which(cmd[0]):
                continue
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        return None

    async def detect_username(self) -> str | None:
        phase = self.emit.begin("Looking for your GitHub username")
        username = await anyio.to_thread.run_sync(self.find_git_username)
        if not username:
            phase.skip("No GitHub username on this machine")
            return None
        phase.ok(f"Found @{username}")
        return username

    async def try_github_ssh(self, username: str) -> SSHConfig | None:
        phase = self.emit.begin(f"Checking SSH keys on github.com/{username}.keys")
        try:
            backend = await anyio.to_thread.run_sync(KeyDiscovery.match_ssh_key, username)
        except httpx.HTTPError:
            phase.unreachable("Couldn't reach GitHub")
            return None
        if not backend:
            phase.skip("No SSH key on GitHub matches a local one")
            return None
        phase.ok(f"Matched {backend.private_key_path.name}")
        return SSHConfig(
            contributor_id=ContributorId(username),
            key_path=backend.private_key_path,
        )

    async def try_github_gpg(self, username: str) -> GPGConfig | None:
        phase = self.emit.begin(f"Checking GPG keys on github.com/{username}.gpg")
        try:
            backend = await anyio.to_thread.run_sync(KeyDiscovery.match_gpg_key, username)
        except httpx.HTTPError:
            phase.unreachable("Couldn't reach GitHub")
            return None
        if not backend:
            phase.skip("No GPG key on GitHub matches a local one")
            return None
        phase.ok(f"Matched GPG {backend.fpr[-8:]}")
        return GPGConfig(
            contributor_type="github",
            contributor_id=ContributorId(username),
            fpr=backend.fpr,
        )

    async def try_existing_gist(self, username: str) -> GistConfig | None:
        phase = self.emit.begin("Checking for a cc-sentiment gist")
        key_path = await anyio.to_thread.run_sync(KeyDiscovery.find_gist_keypair)
        if key_path is None:
            phase.skip("No local cc-sentiment keypair")
            return None
        if not await anyio.to_thread.run_sync(KeyDiscovery.gh_authenticated):
            phase.skip("gh CLI not authenticated")
            return None
        gist_id = await anyio.to_thread.run_sync(KeyDiscovery.find_cc_sentiment_gist_id)
        if gist_id is None:
            phase.skip("No cc-sentiment gist on this account")
            return None
        phase.ok(f"Found gist {gist_id[:7]}")
        return GistConfig(
            contributor_id=ContributorId(username),
            key_path=key_path,
            gist_id=gist_id,
        )

    async def find_local_gpg(self) -> tuple[GPGKeyInfo, ...]:
        phase = self.emit.begin("Scanning local GPG keyring")
        keys = await anyio.to_thread.run_sync(KeyDiscovery.find_gpg_keys)
        if not keys:
            phase.skip("No local GPG keys")
            return ()
        plural = "s" if len(keys) != 1 else ""
        phase.ok(f"Found {len(keys)} GPG key{plural}")
        return keys

    async def try_openpgp(
        self, info: GPGKeyInfo, username: str | None
    ) -> GPGConfig | None:
        phase = self.emit.begin(f"Checking keys.openpgp.org for {info.fpr[-8:]}")
        try:
            armored = await anyio.to_thread.run_sync(
                KeyDiscovery.fetch_openpgp_key, info.fpr
            )
        except httpx.HTTPError:
            phase.unreachable("Couldn't reach keys.openpgp.org")
            return None
        if not armored:
            phase.skip("Not on keys.openpgp.org yet")
            return None
        phase.ok("Published on keys.openpgp.org")
        return (
            GPGConfig(contributor_type="github", contributor_id=ContributorId(username), fpr=info.fpr)
            if username
            else GPGConfig(contributor_type="gpg", contributor_id=ContributorId(info.fpr), fpr=info.fpr)
        )

    async def probe_and_save(self, config: SSHConfig | GPGConfig | GistConfig) -> bool:
        from cc_sentiment.upload import AuthOk, Uploader
        phase = self.emit.begin("Checking your key with the server")
        result = await Uploader().probe_credentials(config)
        if not isinstance(result, AuthOk):
            phase.skip("Dashboard couldn't verify yet")
            return False
        phase.ok("Verified")
        self.state.config = config
        await anyio.to_thread.run_sync(self.state.save)
        return True


class SpinnerLine(Static):
    DEFAULT_CSS = "SpinnerLine { height: 1; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spinner = Spinner("dots", style="bold")

    def on_mount(self) -> None:
        self.set_interval(1 / 12, self.refresh)

    def render(self) -> Spinner:
        return self.spinner


@dataclass
class EngineBootView:
    MAX_SNIPPET_CHARS: ClassVar[int] = 60
    SNIPPET_RATE_LIMIT: ClassVar[float] = 0.5
    WITTY_COMMENTS: ClassVar[dict[int, tuple[str, ...]]] = {
        1: ("oof", "yikes", "time to take a walk", "we've all been there", "send help", "cursed"),
        2: ("mood", "same energy", "try again later", "nope nope nope", "sigh", "bargaining stage"),
        3: ("just business", "getting it done", "ok then", "fine", "the work continues", "transactional"),
        4: ("nice", "smooth", "working as intended", "as you were", "on track", "we're cooking"),
        5: ("vibes", "flow state", "chef's kiss", "absolute unit", "sparkles", "heck yeah"),
    }

    app: App
    section: Widget
    status: SpinnerLine
    log: Static
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=8))
    last_snippet_at: float = 0.0
    snippet_started: bool = False

    def show(self, engine: str) -> None:
        self.status.spinner.text = f"Loading {engine} engine"
        self.status.display = True
        self.lines.clear()
        self.log.update("")
        self.last_snippet_at = 0.0
        self.snippet_started = False
        self.section.add_class("active")

    def hide(self) -> None:
        self.section.remove_class("active")

    def write_from_thread(self, line: str) -> None:
        self.lines.append(f"[dim]{line}[/]")
        self.app.call_from_thread(self.log.update, "\n".join(self.lines))

    def add_snippet(self, snippet: str, score: int) -> None:
        now = time.monotonic()
        if now - self.last_snippet_at < self.SNIPPET_RATE_LIMIT:
            return
        self.last_snippet_at = now
        if not self.snippet_started:
            self.snippet_started = True
            self.lines.clear()
            self.status.display = False
        comment = random.choice(self.WITTY_COMMENTS[score])
        truncated = snippet if len(snippet) <= self.MAX_SNIPPET_CHARS else snippet[:self.MAX_SNIPPET_CHARS - 1] + "…"
        self.lines.append(f'{ScoreBar.ICONS[score]} {score}  "{truncated}"  [dim]{comment}[/]')
        self.log.update("\n".join(self.lines))


class ScoreBar(Static):
    DEFAULT_CSS = """
    ScoreBar { height: 1; }
    """

    COLORS: ClassVar[dict[int, str]] = {1: "red", 2: "red", 3: "yellow", 4: "green", 5: "green"}
    LABELS: ClassVar[dict[int, str]] = {1: "frustrated", 2: "annoyed", 3: "neutral", 4: "satisfied", 5: "delighted"}
    ICONS: ClassVar[dict[int, str]] = {1: "😤", 2: "😒", 3: "😐", 4: "😊", 5: "🤩"}

    def __init__(self, score: int) -> None:
        super().__init__()
        self.score = score

    def render_bar(self, count: int, total: int, max_count: int) -> str:
        pct = 100 * count / total if total else 0
        bar_width = 20
        bar_len = int(bar_width * count / max_count) if max_count else 0
        color = self.COLORS[self.score]
        icon = self.ICONS[self.score]
        label = self.LABELS[self.score]
        bar = "━" * bar_len + "╺" + "─" * (bar_width - bar_len)
        return f" {icon} {self.score} [{color}]{label:>11}[/]  [{color}]{bar}[/]  {pct:4.1f}%  ({count})"


class HourlyChart(Static):
    DEFAULT_CSS = """
    HourlyChart { height: 7; }
    """

    COLORS: ClassVar[dict[int, str]] = {1: "red", 2: "dark_orange", 3: "yellow", 4: "green", 5: "cyan"}
    Y_TICKS: ClassVar[dict[int, str]] = {5: "😄", 4: "🙂", 3: "😐", 2: "😕", 1: "😡"}
    X_LABELS: ClassVar[dict[int, str]] = {0: "12a", 6: "6a", 12: "12p", 18: "6p", 23: "11p"}

    def update_chart(self, records: list[SentimentRecord]) -> None:
        sums = [0.0] * 24
        counts = [0] * 24
        for r in records:
            h = r.time.astimezone().hour
            sums[h] += int(r.sentiment_score)
            counts[h] += 1
        avgs: list[float | None] = [
            sums[h] / counts[h] if counts[h] else None for h in range(24)
        ]

        if all(a is None for a in avgs):
            self.update("[dim]no data yet[/]")
            return

        rows: list[str] = []
        for row_score in range(5, 0, -1):
            tick = self.Y_TICKS[row_score]
            cells: list[str] = []
            for h in range(24):
                avg = avgs[h]
                if avg is not None and round(avg) == row_score:
                    cells.append(f"[{self.COLORS[row_score]}]●[/]")
                elif self._on_line_segment(h, row_score, avgs):
                    cells.append("[dim]│[/]")
                else:
                    cells.append(" ")
            rows.append(f"{tick} " + "".join(cells))

        rows.append("   " + "─" * 24)
        buf = list(" " * 27)
        for h, lbl in self.X_LABELS.items():
            for i, ch in enumerate(lbl):
                buf[h + i] = ch
        rows.append("   " + "".join(buf).rstrip())

        self.update("\n".join(rows))

    @staticmethod
    def _on_line_segment(h: int, row_score: int, avgs: list[float | None]) -> bool:
        if avgs[h] is not None:
            return False
        prev_h = next((i for i in range(h - 1, -1, -1) if avgs[i] is not None), None)
        next_h = next((i for i in range(h + 1, 24) if avgs[i] is not None), None)
        if prev_h is None or next_h is None:
            return False
        prev_avg = avgs[prev_h]
        next_avg = avgs[next_h]
        assert prev_avg is not None and next_avg is not None
        lo, hi = sorted((round(prev_avg), round(next_avg)))
        return lo < row_score < hi


class BootingScreen(Screen[None]):
    DEFAULT_CSS = """
    BootingScreen { align: center middle; background: $surface; }
    #boot-card { width: 60; height: auto; border: heavy $accent; padding: 1 2; }
    #boot-title { text-align: center; text-style: bold; color: $text; }
    #boot-version { text-align: center; color: $text-muted; margin: 0 0 1 0; }
    #boot-spinner-row { height: 1; align-horizontal: center; margin: 1 0 0 0; }
    #boot-spinner { width: 3; }
    #boot-status { text-align: center; color: $text-muted; height: 1; }
    #boot-detail { text-align: center; color: $text-muted; height: auto; max-height: 8; margin: 1 0 0 0; }
    """

    status: reactive[str] = reactive("Starting up...")

    def compose(self) -> ComposeResult:
        with Vertical(id="boot-card"):
            yield Static("cc-sentiment", id="boot-title")
            yield Static(f"v{CLIENT_VERSION}", id="boot-version")
            with Horizontal(id="boot-spinner-row"):
                yield SpinnerLine(id="boot-spinner")
            yield Static("Starting up...", id="boot-status")
            yield Static("", id="boot-detail")

    def watch_status(self, value: str) -> None:
        self.query_one("#boot-status", Static).update(value)

    def append_detail(self, line: str) -> None:
        append_line(self.query_one("#boot-detail", Static), line)


class PlatformErrorScreen(Screen[None]):
    DEFAULT_CSS = """
    PlatformErrorScreen { align: center middle; }
    #error-box { width: 76; height: auto; border: heavy $error; padding: 2 3; }
    #error-box .title { text-style: bold; color: $error; margin: 0 0 1 0; }
    #error-box .detail { color: $text; margin: 0 0 2 0; }
    """

    BINDINGS = [("q", "done", "Quit"), ("escape", "done", "Quit"), ("enter", "done", "Quit")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="error-box"):
            yield Label("Sorry, this machine can't run cc-sentiment.", classes="title")
            yield Label(self.message, classes="detail")
            yield Button("Quit", id="quit-btn", variant="primary")

    @on(Button.Pressed, "#quit-btn")
    def on_quit(self) -> None:
        self.dismiss(None)

    def action_done(self) -> None:
        self.dismiss(None)


class StatShareScreen(Screen[None]):
    DEFAULT_CSS = """
    StatShareScreen { align: center middle; }
    #stat-box { width: 76; height: auto; border: heavy $accent; padding: 2 3; }
    #stat-box .title { text-style: bold; color: $text; margin: 0 0 1 0; }
    #stat-box .stat { color: $accent; text-style: bold; margin: 0 0 1 0; }
    #stat-box .detail { color: $text-muted; margin: 0 0 1 0; }
    #stat-box Button { margin: 1 1 0 0; }
    """

    BINDINGS = [
        ("enter", "tweet", "Tweet"),
        ("escape", "skip", "Skip"),
    ]

    TWEET_INTENT_URL: ClassVar[str] = "https://twitter.com/intent/tweet"

    def __init__(self, stat: MyStat, contributor_id: str, contributor_type: str) -> None:
        super().__init__()
        self.stat = stat
        self.contributor_id = contributor_id
        self.contributor_type = contributor_type

    @property
    def share_url(self) -> str:
        params = {"t": self.stat.text} | (
            {"u": self.contributor_id} if self.contributor_type in ("github", "gist") else {}
        )
        return f"{CCSentimentApp.DASHBOARD_URL}/?{urlencode(params)}"

    @property
    def tweet_url(self) -> str:
        return f"{self.TWEET_INTENT_URL}?{urlencode({'text': self.stat.tweet_text, 'url': self.share_url})}"

    def compose(self) -> ComposeResult:
        with Vertical(id="stat-box"):
            yield Label("Your cc-sentiment snapshot", classes="title")
            yield Label(f"You are {self.stat.text}.", classes="stat")
            yield Label(
                "Share it? The card on Twitter will show your GitHub avatar and this stat.",
                classes="detail",
            )
            with Horizontal():
                yield Button("Tweet it", id="stat-tweet", variant="primary")
                yield Button("Not now", id="stat-skip", variant="default")

    @on(Button.Pressed, "#stat-tweet")
    async def on_tweet_button(self) -> None:
        await self._open_tweet()

    @on(Button.Pressed, "#stat-skip")
    def on_skip_button(self) -> None:
        self.dismiss(None)

    async def action_tweet(self) -> None:
        await self._open_tweet()

    def action_skip(self) -> None:
        self.dismiss(None)

    async def _open_tweet(self) -> None:
        await anyio.to_thread.run_sync(webbrowser.open, self.tweet_url)
        self.dismiss(None)


class DaemonPromptScreen(Screen[bool]):
    DEFAULT_CSS = """
    DaemonPromptScreen { align: center middle; }
    #daemon-box { width: 76; height: auto; border: heavy $accent; padding: 2 3; }
    #daemon-box .title { text-style: bold; color: $text; margin: 0 0 1 0; }
    #daemon-box .detail { color: $text-muted; margin: 0 0 1 0; }
    #daemon-box Button { margin: 1 1 0 0; }
    """

    BINDINGS = [("escape", "cancel", "Skip")]

    def compose(self) -> ComposeResult:
        with Vertical(id="daemon-box"):
            yield Label("Run this automatically each day?", classes="title")
            yield Label(
                "We can schedule a background job that refreshes your numbers daily. "
                "No need to remember to run this.",
                classes="detail",
            )
            yield Label(
                "Nothing else changes. Undo any time with [b]cc-sentiment uninstall[/].",
                classes="detail",
            )
            with Horizontal():
                yield Button("Schedule it", id="daemon-yes", variant="primary")
                yield Button("Not now", id="daemon-no", variant="default")

    @on(Button.Pressed, "#daemon-yes")
    def on_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#daemon-no")
    def on_no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CostReviewScreen(Screen[bool]):
    DEFAULT_CSS = """
    CostReviewScreen { align: center middle; }
    #cost-box { width: 76; height: auto; border: heavy $accent; padding: 2 3; }
    #cost-box .title { text-style: bold; color: $text; margin: 0 0 1 0; }
    #cost-box .detail { color: $text-muted; margin: 0 0 1 0; }
    #cost-box .emphasis { color: $text; margin: 0 0 2 0; }
    #cost-box Button { margin: 1 1 0 0; }
    """

    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, bucket_count: int, model: str) -> None:
        super().__init__()
        self.bucket_count = bucket_count
        self.model = model
        self.cost = ClaudeCLIEngine.estimate_cost_usd(bucket_count)

    def compose(self) -> ComposeResult:
        with Vertical(id="cost-box"):
            yield Label(f"Use {self.model} for scoring?", classes="title")
            yield Label(
                f"This machine can't run local inference, so we'll use the Claude API "
                f"via `claude -p` to score [b]{self.bucket_count}[/] new buckets.",
                classes="detail",
            )
            yield Label(
                f"Estimated cost: about [b]${self.cost:.2f}[/] "
                f"(at ${ClaudeCLIEngine.HAIKU_INPUT_USD_PER_MTOK:.2f}/MTok in, "
                f"${ClaudeCLIEngine.HAIKU_OUTPUT_USD_PER_MTOK:.2f}/MTok out). "
                f"Actual cost is often lower thanks to prompt caching.",
                classes="emphasis",
            )
            yield Label(
                "This gets billed by Anthropic through your existing `claude` account. "
                "Your conversation text still leaves the machine only as part of this API call.",
                classes="detail",
            )
            with Horizontal():
                yield Button("Continue", id="cost-yes", variant="primary")
                yield Button("Cancel", id="cost-no", variant="default")

    @on(Button.Pressed, "#cost-yes")
    def on_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cost-no")
    def on_cancel(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class SetupScreen(Screen[bool]):
    ROUGH_BUCKETS_PER_FILE: ClassVar[int] = 6

    DEFAULT_CSS = """
    SetupScreen { align: center middle; }
    #wizard { width: 80; height: auto; max-height: 90%; border: heavy $accent; padding: 1 2; overflow-y: auto; }
    #wizard Label { margin: 1 0 0 0; }
    #wizard .step-title { text-style: bold; color: $text; margin: 0 0 1 0; }
    #wizard Input { margin: 0 0 1 0; }
    #wizard Button { margin: 1 0 0 0; }
    #wizard .status { color: $text-muted; margin: 0 0 1 0; }
    #wizard .faq { color: $text-muted; margin: 1 0 0 0; }
    #wizard .error { color: $error; }
    #wizard .success { color: $success; }
    #wizard DataTable { height: auto; max-height: 10; margin: 0 0 1 0; }
    #wizard RadioSet { margin: 0 0 1 0; }
    #wizard .key-text { color: $text-muted; margin: 0 0 1 0; max-height: 3; overflow-y: auto; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Quit", priority=True),
        Binding("ctrl+c", "cancel", "Quit", priority=True),
    ]

    username: reactive[str] = reactive("")
    selected_key: reactive[SSHKeyInfo | GPGKeyInfo | None] = reactive(None)

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard"):
            with ContentSwitcher(initial="step-loading"):
                yield from self.compose_loading_step()
                yield from self.compose_username_step()
                yield from self.compose_discovery_step()
                yield from self.compose_remote_step()
                yield from self.compose_upload_step()
                yield from self.compose_done_step()

    def compose_loading_step(self) -> ComposeResult:
        with Vertical(id="step-loading"):
            yield Label("Setting things up...", classes="step-title")
            yield Label(
                "Checking if we can verify your uploads automatically.",
                classes="status",
            )
            yield Static("", id="loading-activity", classes="status")

    def compose_username_step(self) -> ComposeResult:
        with Vertical(id="step-username"):
            yield Label("Who are you?", classes="step-title")
            yield Label(
                "Your GitHub username lets us verify your uploads. "
                "No account creation, no permissions needed. "
                "We just check that your public keys match.",
                classes="status",
            )
            yield Input(placeholder="GitHub username", id="username-input")
            yield Label("", id="username-status", classes="status")
            yield Button("Next", id="username-next", variant="primary")
            yield Button("I don't use GitHub", id="username-skip", variant="default")

    def compose_discovery_step(self) -> ComposeResult:
        with Vertical(id="step-discovery"):
            yield Label("Pick a signing key", classes="step-title")
            yield Label(
                "Looking for signing keys on your machine...",
                id="discovery-status", classes="status",
            )
            yield DataTable(id="key-table")
            yield RadioSet(id="key-select")
            yield Label("", id="no-keys-msg", classes="status")
            yield Label(
                "[dim]Why do we need this? Your key is like a personal stamp. "
                "It proves the data came from you, without sharing anything "
                "private. We never read or upload your private key.[/]",
                classes="faq",
            )
            yield Button("Next", id="discovery-next", variant="primary", disabled=True)
            yield Button("Back", id="discovery-back", variant="default")

    def compose_remote_step(self) -> ComposeResult:
        with Vertical(id="step-remote"):
            yield Label("Verifying your key", classes="step-title")
            yield Label(
                "Checking that the dashboard can verify your uploads...",
                id="remote-status", classes="status",
            )
            yield Static("", id="remote-checks")
            yield Button("Next", id="remote-next", variant="primary", disabled=True)
            yield Button("Back", id="remote-back", variant="default")

    def compose_upload_step(self) -> ComposeResult:
        with Vertical(id="step-upload"):
            yield Label("Link your key", classes="step-title")
            yield Label(
                "The dashboard needs to be able to look up your key "
                "to verify your uploads. We'll help you set this up.",
                id="upload-status", classes="status",
            )
            yield RadioSet(id="upload-options")
            yield Label("", id="upload-key-text", classes="key-text")
            yield Label("", id="upload-result", classes="status")
            yield Button("Link my key", id="upload-go", variant="primary", disabled=True)
            yield Button("I'll do it myself", id="upload-skip", variant="default")
            yield Button("Back", id="upload-back", variant="default")

    def compose_done_step(self) -> ComposeResult:
        with Vertical(id="step-done"):
            yield Label("You're all set", classes="step-title")
            yield Label("", id="done-summary", classes="success")
            yield Label("", id="done-verify", classes="status")
            yield Static("", id="done-identify", classes="faq")
            yield Static("", id="done-process", classes="faq")
            yield Static("", id="done-payload", classes="faq")
            yield Static("", id="done-eta", classes="faq")
            yield Button("Contribute my stats", id="done-btn", variant="primary")

    def _populate_done_info(self) -> None:
        identify = self.query_one("#done-identify", Static)
        match self.state.config:
            case GistConfig(gist_id=g):
                identify.update(
                    "[b]How we know it's you:[/] each upload is signed with a cc-sentiment "
                    f"key stored only on this machine. Its public half lives in gist {g[:7]} "
                    "on your GitHub; the dashboard reads it from there to verify signatures."
                )
            case _:
                identify.update(
                    "[b]How we know it's you:[/] each upload is signed locally with "
                    "your private key. The dashboard checks the signature against "
                    "your public key. No account, no password, no permissions."
                )
        process = self.query_one("#done-process", Static)
        match default_engine():
            case "omlx":
                process.update(
                    "[b]Where scoring happens:[/] entirely on your Mac. A "
                    "small Gemma model runs on your Apple Silicon GPU; your "
                    "conversation text never leaves the machine."
                )
            case "claude":
                process.update(
                    "[b]Where scoring happens:[/] on your machine via the "
                    "`claude` CLI (no Apple Silicon here for local inference). "
                    "Transcripts go to the Claude API, never to our dashboard."
                )
        self.query_one("#done-payload", Static).update(self.render_sample_payload())
        files = len(TranscriptDiscovery.find_transcripts())
        rate = Hardware.estimate_buckets_per_sec(default_engine())
        self.query_one("#done-eta", Static).update(
            f"[dim]Found [b]{files}[/] transcripts. "
            f"About {format_duration(files * self.ROUGH_BUCKETS_PER_FILE / rate)} on this Mac.[/]"
            if rate and files else ""
        )

    @staticmethod
    def render_sample_payload() -> str:
        fields = (
            ("time", '"2026-04-15T14:23:05Z"'),
            ("conversation_id", '"7f3a9b2c-0e4d-4a91-b6f8-e2c8d9a1f4b2"'),
            ("sentiment_score", "4"),
            ("claude_model", '"claude-haiku-4-5"'),
            ("turn_count", "14"),
            ("thinking_chars", "2847"),
            ("read_edit_ratio", "0.71"),
        )
        width = max(len(k) for k, _ in fields)
        rows = [
            f'  [cyan]"{k}"[/]:{" " * (width - len(k) + 2)}[{"green" if v.startswith(chr(34)) else "yellow"}]{v}[/],'
            for k, v in fields
        ]
        return (
            "[b]What actually gets sent:[/] one row per conversation, "
            "[dim]signed by your key. No text, no prompts, no code.[/]\n\n"
            "[dim]{[/]\n"
            + "\n".join(rows)
            + "\n  [dim]...[/]\n[dim]}[/]"
        )

    def on_mount(self) -> None:
        table = self.query_one("#key-table", DataTable)
        table.add_columns("Type", "Fingerprint", "Email")
        table.display = False
        self.query_one("#key-select", RadioSet).display = False
        self.try_auto_setup()

    @work()
    async def try_auto_setup(self) -> None:
        emit = StatusEmitter(self.query_one("#loading-activity", Static))
        ok, username = await AutoSetup(self.state, emit).run()
        if ok:
            self._on_auto_setup_success()
            return
        self._on_auto_setup_fail(username)

    def _on_auto_setup_success(self) -> None:
        config = self.state.config
        assert config is not None
        summary = self.query_one("#done-summary", Label)
        match config:
            case SSHConfig(contributor_id=cid, key_path=p):
                summary.update(f"Signed in as [b]{cid}[/] using SSH key [dim]{p.name}[/]")
            case GPGConfig(contributor_id=cid, fpr=f):
                summary.update(f"Signed in as [b]{cid}[/] using GPG [dim]{f[-8:]}[/]")
            case GistConfig(contributor_id=cid, gist_id=g):
                summary.update(f"Signed in as [b]{cid}[/] using cc-sentiment gist [dim]{g[:7]}[/]")
        self.query_one("#done-verify", Label).update(
            "[green]You're set up. Ready to upload.[/]"
        )
        self._populate_done_info()
        self.query_one(ContentSwitcher).current = "step-done"

    def _on_auto_setup_fail(self, username: str | None) -> None:
        if username:
            self.query_one("#username-input", Input).value = username
            self.query_one("#username-status", Label).update(f"Auto-detected: {username}")
        self.query_one(ContentSwitcher).current = "step-username"

    @on(Button.Pressed, "#username-next")
    def on_username_next(self) -> None:
        username = self.query_one("#username-input", Input).value.strip()
        if not username:
            self.query_one("#username-status", Label).update("[red]Username is required[/]")
            return
        self.username = username
        self.validate_and_discover()

    @on(Button.Pressed, "#username-skip")
    def on_username_skip(self) -> None:
        self.username = ""
        self._switch_to_discovery()

    @work(thread=True)
    def validate_and_discover(self) -> None:
        status = self.query_one("#username-status", Label)
        self.app.call_from_thread(status.update, f"Validating {self.username}...")
        try:
            response = httpx.get(f"https://api.github.com/users/{self.username}", timeout=10.0)
        except httpx.HTTPError:
            self.app.call_from_thread(status.update, "[red]Could not reach GitHub API[/]")
            return
        if response.status_code != 200:
            self.app.call_from_thread(status.update, f"[red]GitHub user '{self.username}' not found[/]")
            return
        self.app.call_from_thread(self._switch_to_discovery)

    def _switch_to_discovery(self) -> None:
        self.query_one(ContentSwitcher).current = "step-discovery"
        self.discover_keys()

    @work(thread=True)
    def discover_keys(self) -> None:
        ssh_keys = KeyDiscovery.find_ssh_keys()
        gpg_keys = KeyDiscovery.find_gpg_keys()
        self.app.call_from_thread(self._populate_key_table, ssh_keys, gpg_keys)

    def _populate_key_table(
        self,
        ssh_keys: tuple[SSHKeyInfo, ...],
        gpg_keys: tuple[GPGKeyInfo, ...],
    ) -> None:
        table = self.query_one("#key-table", DataTable)
        radio = self.query_one("#key-select", RadioSet)
        status = self.query_one("#discovery-status", Label)
        no_keys = self.query_one("#no-keys-msg", Label)

        all_keys: list[SSHKeyInfo | GPGKeyInfo] = (
            [*ssh_keys, *gpg_keys] if self.username else list(gpg_keys)
        )
        self._discovered_keys = all_keys

        if not all_keys:
            status.update("No signing keys found on your machine.")
            if self.username and KeyDiscovery.gh_authenticated():
                no_keys.update(
                    "We can make a small signing key just for cc-sentiment and save its "
                    "public half as a gist on your GitHub. Press Next."
                )
                self.query_one("#discovery-next", Button).disabled = False
                self._generation_mode = "gist"
            elif KeyDiscovery.has_tool("gpg"):
                no_keys.update("No problem. We'll create one for you. Just press Next.")
                self.query_one("#discovery-next", Button).disabled = False
                self._generation_mode = "gpg"
            elif not self.username:
                no_keys.update("Go back and enter a GitHub username, or install gpg (brew install gnupg) to use GPG.")
                self._generation_mode = None
            else:
                no_keys.update("Install the GitHub CLI (brew install gh) to save a signing key as a gist, or run: ssh-keygen -t ed25519.")
                self._generation_mode = None
            return

        self._generation_mode = None
        table.display = True
        status.update(f"Found {len(all_keys)} key{'s' if len(all_keys) != 1 else ''} on your machine:")

        if len(all_keys) == 1:
            radio.display = False
            self.query_one("#discovery-next", Button).disabled = False
        else:
            radio.display = True
            radio_children = []
            for key in all_keys:
                match key:
                    case SSHKeyInfo(path=p, algorithm=a):
                        radio_children.append(RadioButton(f"SSH: {p.name} ({a})"))
                    case GPGKeyInfo(fpr=f, email=e):
                        radio_children.append(RadioButton(f"GPG: {f[-8:]} ({e})"))
            radio.mount_all(radio_children)
            self.query_one("#discovery-next", Button).disabled = False

        for key in all_keys:
            match key:
                case SSHKeyInfo(path=p, algorithm=_, comment=c):
                    table.add_row("SSH", str(p), c)
                case GPGKeyInfo(fpr=f, email=e):
                    table.add_row("GPG", f"{f[:4]} {f[4:8]} ... {f[-8:-4]} {f[-4:]}", e)

    @on(Button.Pressed, "#discovery-back")
    def on_discovery_back(self) -> None:
        self.query_one(ContentSwitcher).current = "step-username"

    @on(Button.Pressed, "#discovery-next")
    def on_discovery_next(self) -> None:
        match getattr(self, "_generation_mode", None):
            case "gist":
                self.generate_gist_key()
                return
            case "gpg":
                self.generate_gpg_key()
                return
        radio = self.query_one("#key-select", RadioSet)
        idx = radio.pressed_index if radio.pressed_index >= 0 else 0
        self.selected_key = self._discovered_keys[idx]
        self.query_one(ContentSwitcher).current = "step-remote"
        self.check_remotes()

    @work(thread=True)
    def generate_gpg_key(self) -> None:
        status = self.query_one("#discovery-status", Label)
        self.app.call_from_thread(status.update, "Creating a signing key for you...")

        email = self.username + "@users.noreply.github.com"
        batch_input = f"""%no-protection
Key-Type: eddsa
Key-Curve: ed25519
Name-Real: {self.username}
Name-Email: {email}
Expire-Date: 0
%commit
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt") as f:
            f.write(batch_input)
            f.flush()
            result = subprocess.run(
                ["gpg", "--batch", "--gen-key", f.name],
                capture_output=True, text=True, timeout=30,
            )

        if result.returncode != 0:
            self.app.call_from_thread(status.update, f"[red]Key generation failed: {result.stderr.strip()}[/]")
            return

        gpg_keys = KeyDiscovery.find_gpg_keys()
        new_key = next((k for k in gpg_keys if k.email == email), None)
        if not new_key:
            self.app.call_from_thread(status.update, "[red]Key generated but not found in keyring[/]")
            return

        self.selected_key = new_key
        self.app.call_from_thread(status.update, f"[green]Generated key: {new_key.fpr[-8:]}[/]")
        self.app.call_from_thread(self._go_to_remote)

    @work(thread=True)
    def generate_gist_key(self) -> None:
        status = self.query_one("#discovery-status", Label)
        self.app.call_from_thread(status.update, "Creating a signing key and saving it as a gist...")
        try:
            key_path = KeyDiscovery.generate_gist_keypair()
            gist_id = KeyDiscovery.create_gist(key_path)
        except subprocess.CalledProcessError as e:
            err = (e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr or str(e)).strip()
            self.app.call_from_thread(status.update, f"[red]Couldn't create the gist: {err}[/]")
            return
        self.state.config = GistConfig(
            contributor_id=ContributorId(self.username),
            key_path=key_path,
            gist_id=gist_id,
        )
        self.app.call_from_thread(status.update, f"[green]Saved key to gist {gist_id[:7]}[/]")
        self.app.call_from_thread(self._finish_gist, gist_id)

    def _finish_gist(self, gist_id: str) -> None:
        self.query_one("#done-summary", Label).update(
            f"Signed in as [b]{self.username}[/] using cc-sentiment gist [dim]{gist_id[:7]}[/]"
        )
        self._populate_done_info()
        self.query_one(ContentSwitcher).current = "step-done"
        self.verify_server_config()

    def _go_to_remote(self) -> None:
        self.query_one(ContentSwitcher).current = "step-remote"
        self.check_remotes()

    @work(thread=True)
    def check_remotes(self) -> None:
        checks_widget = self.query_one("#remote-checks", Static)
        status = self.query_one("#remote-status", Label)
        key = self.selected_key
        results: list[str] = []
        found = False
        self._key_on_openpgp = False

        match key:
            case SSHKeyInfo(path=p):
                self.app.call_from_thread(checks_widget.update, "  [dim]...[/] Checking GitHub")
                try:
                    github_keys = KeyDiscovery.fetch_github_ssh_keys(self.username)
                    local_fp = SSHBackend(private_key_path=p).fingerprint()
                    if any(" ".join(gk.split()[:2]) == local_fp for gk in github_keys):
                        results.append("  [green]✓[/] Found on GitHub")
                        found = True
                    else:
                        results.append("  [yellow]—[/] Not on GitHub yet")
                except httpx.HTTPError:
                    results.append("  [yellow]?[/] Couldn't reach GitHub")

            case GPGKeyInfo(fpr=f):
                checks = []
                if self.username:
                    checks.append("  [dim]...[/] Checking GitHub")
                checks.append("  [dim]...[/] Checking keys.openpgp.org")
                self.app.call_from_thread(checks_widget.update, "\n".join(checks))

                if self.username:
                    try:
                        if KeyDiscovery.gpg_key_on_github(self.username, f):
                            results.append("  [green]✓[/] Found on GitHub")
                            found = True
                        else:
                            results.append("  [yellow]—[/] Not on GitHub yet")
                    except httpx.HTTPError:
                        results.append("  [yellow]?[/] Couldn't reach GitHub")

                try:
                    openpgp_key = KeyDiscovery.fetch_openpgp_key(f)
                    if openpgp_key:
                        results.append("  [green]✓[/] Found on keys.openpgp.org")
                        found = True
                        self._key_on_openpgp = True
                    else:
                        results.append("  [yellow]—[/] Not on keys.openpgp.org yet")
                except httpx.HTTPError:
                    results.append("  [yellow]?[/] Couldn't reach keys.openpgp.org")

        self.app.call_from_thread(checks_widget.update, "\n".join(results))

        if found:
            self.app.call_from_thread(status.update, "[green]You're set up. Ready to upload.[/]")
            self.app.call_from_thread(self._enable_remote_next)
            self._key_on_remote = True
        else:
            msg = "Not linked yet. We can set this up next."
            self.app.call_from_thread(status.update, msg)
            self.app.call_from_thread(self._enable_remote_next)
            self._key_on_remote = False

    def _enable_remote_next(self) -> None:
        self.query_one("#remote-next", Button).disabled = False

    @on(Button.Pressed, "#remote-back")
    def on_remote_back(self) -> None:
        self.query_one(ContentSwitcher).current = "step-discovery"

    @on(Button.Pressed, "#remote-next")
    async def on_remote_next(self) -> None:
        if getattr(self, "_key_on_remote", False):
            self._save_and_finish()
        else:
            self.query_one(ContentSwitcher).current = "step-upload"
            await self._populate_upload_options()

    async def _populate_upload_options(self) -> None:
        radio = self.query_one("#upload-options", RadioSet)
        has_gh = shutil.which("gh") is not None
        key = self.selected_key

        options: list[RadioButton] = []
        self._upload_actions: list[str] = []

        match key:
            case SSHKeyInfo():
                rb = RadioButton(f"Link to GitHub{'' if has_gh else ' (needs gh CLI)'}")
                rb.disabled = not has_gh
                options.append(rb)
                self._upload_actions.append("github-ssh")

            case GPGKeyInfo():
                if self.username:
                    rb = RadioButton(f"Link to GitHub{'' if has_gh else ' (needs gh CLI)'}")
                    rb.disabled = not has_gh
                    options.append(rb)
                    self._upload_actions.append("github-gpg")

                options.append(RadioButton("Publish to keys.openpgp.org"))
                self._upload_actions.append("openpgp")

        options.append(RadioButton("Show key so I can add it myself"))
        self._upload_actions.append("manual")

        enabled_actions = [
            (opt, act)
            for opt, act in zip(options, self._upload_actions)
            if not opt.disabled and act != "manual"
        ]
        if len(enabled_actions) == 1:
            radio.display = False
        else:
            radio.mount_all(options)

        pub_text = ""
        match key:
            case SSHKeyInfo(path=p):
                pub_text = await anyio.to_thread.run_sync(SSHBackend(private_key_path=p).public_key_text)
            case GPGKeyInfo(fpr=f):
                pub_text = await anyio.to_thread.run_sync(GPGBackend(fpr=f).public_key_text)

        self.query_one("#upload-key-text", Label).update(
            f"[dim]{pub_text[:200]}...[/]" if len(pub_text) > 200 else f"[dim]{pub_text}[/]"
        )
        self.query_one("#upload-go", Button).disabled = False

    @on(Button.Pressed, "#upload-go")
    def on_upload_go(self) -> None:
        radio = self.query_one("#upload-options", RadioSet)
        idx = radio.pressed_index if radio.pressed_index >= 0 else 0
        action = self._upload_actions[idx]
        self.run_upload(action)

    @on(Button.Pressed, "#upload-skip")
    def on_upload_skip(self) -> None:
        self._save_and_finish()

    @on(Button.Pressed, "#upload-back")
    def on_upload_back(self) -> None:
        self.query_one(ContentSwitcher).current = "step-remote"

    @work(thread=True)
    def run_upload(self, action: str) -> None:
        result_label = self.query_one("#upload-result", Label)
        key = self.selected_key

        match action:
            case "github-ssh":
                assert isinstance(key, SSHKeyInfo)
                pub_path = key.path.with_suffix(key.path.suffix + ".pub")
                result = subprocess.run(
                    ["gh", "ssh-key", "add", str(pub_path), "-t", "cc-sentiment"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode == 0:
                    self.app.call_from_thread(result_label.update, "[green]Key linked to GitHub. You're all set.[/]")
                    self.app.call_from_thread(self._save_and_finish)
                else:
                    self.app.call_from_thread(result_label.update, f"[red]Something went wrong: {result.stderr.strip()}[/]")

            case "github-gpg":
                assert isinstance(key, GPGKeyInfo)
                pub_text = GPGBackend(fpr=key.fpr).public_key_text()
                with tempfile.NamedTemporaryFile(mode="w", suffix=".asc", delete=False) as f:
                    f.write(pub_text)
                    tmp_path = Path(f.name)
                try:
                    result = subprocess.run(
                        ["gh", "gpg-key", "add", str(tmp_path)],
                        capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode == 0:
                        self.app.call_from_thread(result_label.update, "[green]Key linked to GitHub. You're all set.[/]")
                        self.app.call_from_thread(self._save_and_finish)
                    else:
                        self.app.call_from_thread(result_label.update, f"[red]Something went wrong: {result.stderr.strip()}[/]")
                finally:
                    tmp_path.unlink(missing_ok=True)

            case "openpgp":
                assert isinstance(key, GPGKeyInfo)
                self.app.call_from_thread(result_label.update, "Publishing to keys.openpgp.org...")
                try:
                    pub_text = GPGBackend(fpr=key.fpr).public_key_text()
                    token, statuses = KeyDiscovery.upload_openpgp_key(pub_text)
                    emails = [e for e, s in statuses.items() if s == "unpublished"]
                    if emails:
                        KeyDiscovery.request_openpgp_verify(token, emails)
                        self.app.call_from_thread(
                            result_label.update,
                            f"[yellow]Almost done. Check your email ({', '.join(emails)}) "
                            f"for a verification link, then press Start scanning.[/]",
                        )
                    else:
                        self.app.call_from_thread(result_label.update, "[green]Key already published. You're all set.[/]")
                    self.app.call_from_thread(self._save_and_finish)
                except httpx.HTTPError as e:
                    self.app.call_from_thread(result_label.update, f"[red]Couldn't reach keys.openpgp.org: {e}[/]")

            case "manual":
                assert key is not None
                url = (
                    "https://github.com/settings/ssh/new"
                    if isinstance(key, SSHKeyInfo)
                    else "https://github.com/settings/gpg/new"
                )
                self.app.call_from_thread(result_label.update, f"Paste your public key at:\n{url}")
                self.app.call_from_thread(self._save_and_finish)

    def _save_and_finish(self) -> None:
        key = self.selected_key
        identity = self.username

        match key:
            case SSHKeyInfo(path=p):
                self.state.config = SSHConfig(contributor_id=ContributorId(identity), key_path=p)
            case GPGKeyInfo(fpr=f):
                if identity:
                    self.state.config = GPGConfig(contributor_type="github", contributor_id=ContributorId(identity), fpr=f)
                else:
                    self.state.config = GPGConfig(contributor_type="gpg", contributor_id=ContributorId(f), fpr=f)

        summary = self.query_one("#done-summary", Label)
        match key:
            case SSHKeyInfo(path=p):
                summary.update(f"Signed in as [b]{identity}[/] using SSH key [dim]{p.name}[/]")
            case GPGKeyInfo(fpr=f):
                label = identity or f"GPG {f[-8:]}"
                summary.update(f"Signed in as [b]{label}[/]")

        self._populate_done_info()
        self.query_one(ContentSwitcher).current = "step-done"
        self.verify_server_config()

    @work()
    async def verify_server_config(self) -> None:
        from cc_sentiment.upload import AuthOk, Uploader
        await anyio.to_thread.run_sync(self.state.save)
        verify_label = self.query_one("#done-verify", Label)
        verify_label.update("[dim]Verifying with dashboard...[/]")

        assert self.state.config is not None
        if isinstance(await Uploader().probe_credentials(self.state.config), AuthOk):
            verify_label.update(
                "[green]You're set up. Ready to upload.[/]",
            )
        else:
            verify_label.update(
                "[yellow]We couldn't verify your setup just yet. This usually means:\n"
                "  • If you uploaded to keys.openpgp.org — check your email for a verification link\n"
                "  • If you just added a key to GitHub — it can take a minute to propagate\n"
                "  • You can run cc-sentiment again anytime to retry[/]",
            )

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CCSentimentApp(App[None]):
    DASHBOARD_URL: ClassVar[str] = "https://sentiments.cc"
    RESCAN_CONFIRM_SECONDS: ClassVar[float] = 5.0
    INSIGHTS_MIN_RECORDS: ClassVar[int] = 20
    INSIGHTS_MIN_SAMPLES: ClassVar[int] = 3
    WEEKDAY_LABELS: ClassVar[tuple[str, ...]] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

    CSS = """
    Screen { layout: vertical; background: $surface; }
    #main { height: 1fr; padding: 1 2; }
    #header-section { height: auto; }
    #title-row { height: 3; }
    #title-text { width: 1fr; }
    #score-digits { width: auto; min-width: 20; }
    #score-label { text-align: right; height: 1; color: $text-muted; }
    #progress-section { height: auto; margin: 1 0; }
    #progress-row { height: auto; }
    #progress-label { width: auto; min-width: 24; }
    #scan-progress { width: 1fr; }
    #chart-section { height: auto; margin: 1 0 0 0; }
    #chart-section.inactive, #stats-section.inactive { display: none; }
    #charts-row { height: auto; }
    #hourly-column { width: 1fr; height: auto; margin: 0 2 0 0; }
    #hourly-chart-label { height: 1; color: $text-muted; }
    #hourly-chart { height: 7; }
    #distribution { width: 1fr; height: auto; }
    #engine-boot-section { height: auto; margin: 1 0 0 0; display: none; }
    #engine-boot-section.active { display: block; }
    #engine-boot-status { height: 1; }
    #engine-boot-log { height: auto; max-height: 8; color: $text-muted; }
    #stats-section { height: auto; margin: 1 0 0 0; }
    #stats-row { height: 4; }
    .stat-card { width: 1fr; height: 4; padding: 0 1; border: tall $primary-background; }
    .stat-card .stat-value { text-style: bold; }
    .stat-card .stat-label { color: $text-muted; text-style: bold; }
    #insights-section { height: 1; margin: 1 0 0 0; }
    #insights-section.inactive { display: none; }
    #status-line { height: auto; margin: 1 0 0 0; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("r", "rescan", "Rescan"),
        ("o", "open_dashboard", "Open dashboard"),
    ]

    scored: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Initializing...")
    stage: reactive[Stage] = reactive(Booting())

    def __init__(
        self,
        state: AppState,
        model_repo: str | None = None,
        db_path: Path | None = None,
        setup_only: bool = False,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self.theme = "tokyo-night"
        self.state = state
        self.model_repo = model_repo
        self.db_path = db_path or Repository.default_path()
        self.setup_only = setup_only
        self.debug_mode = debug
        self.repo: Repository | None = None
        self.records: list[SentimentRecord] = []
        self._score_bars: dict[int, ScoreBar] = {}
        self._start_time = 0.0
        self._boot_screen: BootingScreen | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="main"):
            with Vertical(id="header-section"):
                with Horizontal(id="title-row"):
                    yield Static(f"[b]cc-sentiment[/b] [dim]v{CLIENT_VERSION}[/]", id="title-text")
                    yield Digits("-.--", id="score-digits")
                yield Static("[dim]average sentiment[/]", id="score-label")

            with Vertical(id="progress-section"):
                with Horizontal(id="progress-row"):
                    yield Label("Preparing...", id="progress-label")
                    yield ProgressBar(id="scan-progress", total=100, show_eta=False, show_percentage=True)

            Rule(line_style="heavy")

            with Vertical(id="chart-section", classes="inactive"):
                with Horizontal(id="charts-row"):
                    with Vertical(id="hourly-column"):
                        yield Static("[dim]Sentiment by hour of day[/]", id="hourly-chart-label")
                        yield HourlyChart(id="hourly-chart")
                    with Vertical(id="distribution"):
                        for s in range(1, 6):
                            bar = ScoreBar(s)
                            bar.id = f"bar-{s}"
                            self._score_bars[s] = bar
                            yield bar

            with Vertical(id="engine-boot-section"):
                yield SpinnerLine(id="engine-boot-status")
                yield Static("", id="engine-boot-log")

            with Vertical(id="stats-section", classes="inactive"):
                Rule()
                with Horizontal(id="stats-row"):
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-buckets", classes="stat-value")
                        yield Static("moments", classes="stat-label")
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-sessions", classes="stat-value")
                        yield Static("chats", classes="stat-label")
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-files", classes="stat-value")
                        yield Static("transcripts", classes="stat-label")
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-rate", classes="stat-value")
                        yield Static("moments/sec", classes="stat-label")

            yield Static("", id="insights-section", classes="inactive")

            yield Label("", id="status-line")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "cc-sentiment"
        self._start_time = time.monotonic()
        self._boot_screen = BootingScreen()
        await self.push_screen(self._boot_screen)
        self._boot_screen.status = "Loading local cache..."
        self.repo = await anyio.to_thread.run_sync(Repository.open, self.db_path)
        await self._seed_from_repo()
        if self.setup_only:
            await self._dismiss_boot_screen()
            await self.push_screen_wait(SetupScreen(self.state))
            self.exit()
            return
        self.run_flow()

    async def _dismiss_boot_screen(self) -> None:
        if self._boot_screen is None:
            return
        self._boot_screen.dismiss(None)
        self._boot_screen = None

    def _set_boot_status(self, value: str) -> None:
        if self._boot_screen is not None:
            self._boot_screen.status = value

    def _debug(self, msg: str) -> None:
        if not self.debug_mode:
            return
        if self._boot_screen is not None:
            self._boot_screen.append_detail(f"debug: {msg}")
            return
        append_line(self.query_one("#status-line", Label), f"[red dim]debug:[/] {msg}")

    async def on_unmount(self) -> None:
        if self.repo:
            await anyio.to_thread.run_sync(self.repo.close)

    async def _seed_from_repo(self) -> None:
        assert self.repo is not None
        existing = await anyio.to_thread.run_sync(self.repo.all_records)
        if not existing:
            return
        self.records = list(existing)
        _, _, total_files = await anyio.to_thread.run_sync(self.repo.stats)
        self.query_one("#stat-files", Static).update(f"[b]{total_files}[/]")
        self._render_scores()
        self.query_one("#stats-section").remove_class("inactive")

    def watch_stage(self, stage: Stage) -> None:
        if isinstance(stage, (Uploading, IdleEmpty, IdleCaughtUp, IdleAfterUpload)):
            self.query_one("#engine-boot-section").remove_class("active")
        match stage:
            case Booting():
                self._update_status("[dim]Initializing...[/]")
            case Authenticating():
                self._update_status("[dim]Verifying key...[/]")
            case Discovering():
                self._update_status("[dim]Discovering transcripts...[/]")
            case Scoring():
                self._update_status(
                    "[dim]Scoring locally on your Mac. Nothing has been uploaded yet.[/]"
                )
            case Uploading():
                self.query_one("#scan-progress", ProgressBar).update(total=100, progress=0)
                self.query_one("#progress-label", Label).update("Uploading...")
                self._update_status("[dim]Uploading records...[/]")
            case IdleEmpty():
                self._render_stats(0, 0, 0)
                self._update_status(
                    "[green]All set. No conversations yet. Come back after using Claude Code.[/] "
                    "[dim]Press O to browse the dashboard.[/]"
                )
            case IdleCaughtUp(total_buckets=b, total_sessions=s, total_files=f):
                self._render_stats(b, s, f)
                self._update_status(
                    f"[green]All caught up.[/] "
                    f"{s} chat{'s' if s != 1 else ''}, "
                    f"{b} moment{'s' if b != 1 else ''} scored. "
                    f"[dim]Press R to rescan, O to open dashboard.[/]"
                )
            case IdleAfterUpload(total_buckets=b, total_sessions=s, total_files=f):
                self._render_stats(b, s, f)
                self._update_status(
                    "[green]Uploaded.[/] See your data at "
                    "[link='https://sentiments.cc'][b]sentiments.cc[/b][/link]. "
                    "[dim]Press O to open.[/]"
                )
            case Error(message=m):
                self._update_status(m)
            case RescanConfirm():
                self._update_status(
                    "[yellow]Press R again within 5s to clear all state and rescan from scratch.[/]"
                )

    def _render_stats(self, buckets: int, sessions: int, files: int) -> None:
        self.query_one("#stat-buckets", Static).update(f"[b]{buckets}[/]")
        self.query_one("#stat-sessions", Static).update(f"[b]{sessions}[/]")
        self.query_one("#stat-files", Static).update(f"[b]{files}[/]")
        self.query_one("#stats-section").remove_class("inactive")

    async def _authenticate(self) -> bool:
        from cc_sentiment.upload import (
            AuthOk,
            AuthServerError,
            AuthUnauthorized,
            AuthUnreachable,
            Uploader,
        )
        while True:
            if self.state.config is None:
                ok = await self.push_screen_wait(SetupScreen(self.state))
                if not ok:
                    return False
                continue
            self.stage = Authenticating()
            self._set_boot_status("Verifying your key with the server...")
            match await Uploader().probe_credentials(self.state.config):
                case AuthOk():
                    return True
                case AuthUnauthorized():
                    self._update_status(
                        "[yellow]Server doesn't recognize this key. Let's try setup again.[/]"
                    )
                    self.state.config = None
                    await anyio.to_thread.run_sync(self.state.save)
                    continue
                case AuthUnreachable(detail=d):
                    self._debug(f"AuthUnreachable: {d}")
                    self.stage = Error(f"[red]Couldn't reach the server.[/] [dim]{d}[/]")
                    return False
                case AuthServerError(status=s):
                    self._debug(f"AuthServerError: status={s}")
                    self.stage = Error(f"[red]Server error verifying key ({s}).[/]")
                    return False

    @work()
    async def run_flow(self) -> None:
        from cc_sentiment.pipeline import Pipeline
        from cc_sentiment.upload import Uploader

        assert self.repo is not None

        self._set_boot_status("Choosing local engine...")
        try:
            engine = await anyio.to_thread.run_sync(resolve_engine, None)
        except RuntimeError as e:
            await self._dismiss_boot_screen()
            await self.push_screen_wait(PlatformErrorScreen(str(e)))
            self.exit()
            return
        self._debug(f"engine={engine}")

        self.stage = Discovering()
        self._set_boot_status("Discovering transcripts...")
        transcripts = await anyio.to_thread.run_sync(Pipeline.discover_new_transcripts, self.repo)
        pending = await anyio.to_thread.run_sync(self.repo.pending_records)
        self._debug(f"transcripts={len(transcripts)} pending={len(pending)}")

        if (transcripts or pending) and not await self._authenticate():
            await self._dismiss_boot_screen()
            self.exit()
            return

        bucket_count = 0
        if transcripts:
            self._set_boot_status("Sizing things up...")
            bucket_count = await anyio.to_thread.run_sync(
                Pipeline.count_new_buckets, self.repo, transcripts
            )
            self._debug(f"bucket_count={bucket_count}")
            rate = Hardware.estimate_buckets_per_sec(engine)
            if rate and rate > 0:
                self._update_status(
                    f"[dim]Found [b]{bucket_count}[/] moments. "
                    f"About {format_duration(bucket_count / rate)} on this Mac.[/]"
                )
            else:
                self._update_status(f"[dim]Found [b]{bucket_count}[/] moments.[/]")

        if engine == "claude" and bucket_count > 0:
            ok = await self.push_screen_wait(
                CostReviewScreen(bucket_count, self.model_repo or ClaudeCLIEngine.HAIKU_MODEL)
            )
            if not ok:
                await self._dismiss_boot_screen()
                self.exit()
                return

        await self._dismiss_boot_screen()

        if transcripts and bucket_count > 0:
            _, _, existing_files = await anyio.to_thread.run_sync(self.repo.stats)
            self._set_total(bucket_count, engine, existing_files + len(transcripts))
            boot = EngineBootView(
                app=self,
                section=self.query_one("#engine-boot-section"),
                status=self.query_one("#engine-boot-status", SpinnerLine),
                log=self.query_one("#engine-boot-log", Static),
            )
            boot.show(engine)
            await Pipeline.run(
                self.repo, engine, self.model_repo, transcripts,
                on_records=self._add_records, on_bucket=self._add_buckets,
                on_engine_log=boot.write_from_thread,
                on_snippet=boot.add_snippet,
            )

        pending = await anyio.to_thread.run_sync(self.repo.pending_records)
        uploaded = False
        if pending:
            self.stage = Uploading()
            try:
                await Uploader().upload(
                    pending, self.state, self.repo,
                    on_progress=self._on_upload_progress,
                )
                uploaded = True
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (401, 403):
                    self.stage = Error(
                        f"[red bold]Server rejected upload ({e.response.status_code}).[/] "
                        "Run [b]cc-sentiment setup[/] again, or upload your key to GitHub/keys.openpgp.org."
                    )
                else:
                    self.stage = Error(
                        f"[red bold]Server rejected upload ({e.response.status_code}).[/] "
                        f"Records kept locally — press R to retry."
                    )
                return
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError):
                self.stage = Error(
                    "[red bold]Couldn't reach the server.[/] "
                    "Records kept locally — press R to retry once you're back online."
                )
                return
            except subprocess.CalledProcessError as e:
                self.stage = Error(
                    f"[red bold]Signing failed ({e.returncode}).[/] "
                    "Check that your signing key is still accessible, or run "
                    "[b]cc-sentiment[/] again to pick a different one."
                )
                return

        await self._enter_idle(uploaded=uploaded)

        if uploaded:
            await self._offer_stat_share()
            await self._offer_daemon_install()

    async def _fetch_my_stat_with_retry(self) -> StatFetchResult:
        from cc_sentiment.upload import Uploader

        assert self.state.config is not None
        uploader = Uploader()
        for attempt in range(3):
            try:
                stat = await uploader.fetch_my_stat(self.state.config)
            except (httpx.HTTPError, httpx.InvalidURL) as e:
                self._debug(f"fetch_my_stat attempt {attempt + 1}: {type(e).__name__}: {e}")
                return StatFetchHttpError(detail=f"{type(e).__name__}: {e}")
            if stat is not None:
                return StatFetchOk(stat=stat)
            self._debug(f"fetch_my_stat attempt {attempt + 1}: None")
            if attempt < 2:
                await anyio.sleep(2)
        return StatFetchEmpty()

    async def _offer_stat_share(self) -> None:
        assert self.state.config is not None
        match await self._fetch_my_stat_with_retry():
            case StatFetchHttpError():
                self._append_status("[dim]Couldn't fetch your share card.[/]")
            case StatFetchEmpty():
                self._append_status(
                    "[dim]Personal share card warming up — check back on the next run.[/]"
                )
            case StatFetchOk(stat=stat):
                await self.push_screen_wait(StatShareScreen(
                    stat=stat,
                    contributor_id=self.state.config.contributor_id,
                    contributor_type=self.state.config.contributor_type,
                ))

    async def _offer_daemon_install(self) -> None:
        if self.state.daemon_prompt_dismissed or LaunchAgent.is_installed():
            return
        if not await self.push_screen_wait(DaemonPromptScreen()):
            self.state.daemon_prompt_dismissed = True
            await anyio.to_thread.run_sync(self.state.save)
            return
        try:
            await anyio.to_thread.run_sync(LaunchAgent.install)
        except subprocess.CalledProcessError as e:
            self._update_status(
                f"[yellow]Couldn't schedule the background job ({e.returncode}).[/] "
                "[dim]Try `cc-sentiment install` manually.[/]"
            )
            return
        self._update_status(
            "[green]Scheduled.[/] It'll refresh your numbers daily in the background. "
            "[dim]Undo with `cc-sentiment uninstall`.[/]"
        )

    async def _enter_idle(self, uploaded: bool) -> None:
        assert self.repo is not None
        total_buckets, total_sessions, total_files = await anyio.to_thread.run_sync(
            self.repo.stats
        )
        if uploaded:
            self.stage = IdleAfterUpload(
                total_buckets=total_buckets,
                total_sessions=total_sessions,
                total_files=total_files,
            )
        elif total_sessions == 0:
            self.stage = IdleEmpty()
        else:
            self.stage = IdleCaughtUp(
                total_buckets=total_buckets,
                total_sessions=total_sessions,
                total_files=total_files,
            )

    async def action_open_dashboard(self) -> None:
        await anyio.to_thread.run_sync(webbrowser.open, self.DASHBOARD_URL)
        self._update_status(f"[dim]Opened {self.DASHBOARD_URL}.[/]")
        self.set_timer(3.0, lambda: self.watch_stage(self.stage))

    async def action_rescan(self) -> None:
        match self.stage:
            case RescanConfirm():
                await self._reset_for_rescan()
                self.run_flow()
            case IdleEmpty() | IdleCaughtUp() | IdleAfterUpload() | Error() as prev:
                self.stage = RescanConfirm(prev=prev)
                self.set_timer(self.RESCAN_CONFIRM_SECONDS, self._cancel_rescan)

    async def _cancel_rescan(self) -> None:
        match self.stage:
            case RescanConfirm(prev=p):
                self.stage = p

    async def _reset_for_rescan(self) -> None:
        assert self.repo is not None
        await anyio.to_thread.run_sync(self.repo.clear_all)
        self.records = []
        self.scored = 0
        self.total = 0
        self._start_time = time.monotonic()
        self.query_one("#scan-progress", ProgressBar).update(total=100, progress=0)
        self.query_one("#progress-label", Label).update("Preparing...")
        self.query_one("#score-digits", Digits).update("-.--")
        self.query_one("#hourly-chart", HourlyChart).update_chart([])
        for s in range(1, 6):
            self._score_bars[s].update("")
        for stat_id in ("#stat-buckets", "#stat-sessions", "#stat-files", "#stat-rate"):
            self.query_one(stat_id, Static).update("--")
        self.query_one("#chart-section").add_class("inactive")
        self.query_one("#stats-section").add_class("inactive")

    def _set_total(self, total: int, engine: str, total_files: int) -> None:
        self.total = total
        self.scored = 0
        self._start_time = time.monotonic()
        rate = Hardware.estimate_buckets_per_sec(engine)
        self._initial_estimate_seconds = total / rate if rate and rate > 0 else None
        self.query_one("#scan-progress", ProgressBar).update(total=total, progress=0)
        self._update_progress_label()
        self.query_one("#stat-files", Static).update(f"[b]{total_files}[/]")
        self.query_one("#stats-section").remove_class("inactive")
        self.stage = Scoring(total=total, engine=engine)

    def _update_progress_label(self) -> None:
        elapsed = time.monotonic() - self._start_time
        projected = (
            elapsed * self.total / self.scored if self.scored > 0 and self.total > 0
            else self._initial_estimate_seconds if self._initial_estimate_seconds is not None
            else elapsed
        )
        self.query_one("#progress-label", Label).update(
            f"[b]{format_hms(elapsed)}[/] / ~{format_hms(projected)}"
        )

    def _add_buckets(self, n: int) -> None:
        asyncio.get_running_loop()
        self.scored += n
        self.query_one("#scan-progress", ProgressBar).update(progress=self.scored)
        self._update_progress_label()

        elapsed = time.monotonic() - self._start_time
        rate = self.scored / elapsed if elapsed > 0 else 0
        self.query_one("#stat-rate", Static).update(f"{rate:.1f}")

    def _on_upload_progress(self, fraction: float) -> None:
        self.query_one("#scan-progress", ProgressBar).update(progress=int(fraction * 100))

    def _add_records(self, new_records: list[SentimentRecord]) -> None:
        asyncio.get_running_loop()
        self.records.extend(new_records)
        self._render_scores()

    def _update_status(self, text: str) -> None:
        self.status_text = text
        self.query_one("#status-line", Label).update(text)

    def _append_status(self, addition: str) -> None:
        self._update_status(f"{self.status_text}\n{addition}".strip())

    def _render_scores(self) -> None:
        if not self.records:
            return

        self.query_one("#chart-section").remove_class("inactive")
        scores = [int(r.sentiment_score) for r in self.records]
        counts = Counter(scores)
        total = len(scores)
        max_count = max(counts.values()) if counts else 1

        for s in range(1, 6):
            n = counts.get(s, 0)
            self._score_bars[s].update(self._score_bars[s].render_bar(n, total, max_count))

        avg = mean(scores)
        self.query_one("#score-digits", Digits).update(f"{avg:.2f}")
        self.query_one("#hourly-chart", HourlyChart).update_chart(self.records)

        sessions = len({r.conversation_id for r in self.records})
        self.query_one("#stat-buckets", Static).update(f"[b]{total}[/]")
        self.query_one("#stat-sessions", Static).update(f"[b]{sessions}[/]")
        self._render_insights()

    @staticmethod
    def pick_toughest[K](groups: dict[K, list[int]], min_samples: int) -> K | None:
        qualifying = {k: mean(v) for k, v in groups.items() if len(v) >= min_samples}
        return min(qualifying, key=qualifying.__getitem__) if qualifying else None

    @staticmethod
    def short_model(model: str) -> str:
        return next(
            (t for t in model.split("-") if t not in ("claude", "anthropic") and not t.isdigit()),
            model,
        )

    def _render_insights(self) -> None:
        insights = self.query_one("#insights-section", Static)
        if len(self.records) < self.INSIGHTS_MIN_RECORDS:
            insights.add_class("inactive")
            return

        hours: dict[int, list[int]] = defaultdict(list)
        days: dict[int, list[int]] = defaultdict(list)
        models: dict[str, list[int]] = defaultdict(list)
        for r in self.records:
            local = r.time.astimezone()
            score = int(r.sentiment_score)
            hours[local.hour].append(score)
            days[local.weekday()].append(score)
            models[r.claude_model].append(score)

        parts: list[str] = []
        if (h := self.pick_toughest(hours, self.INSIGHTS_MIN_SAMPLES)) is not None:
            parts.append(f"[dim]toughest hour:[/] [b]{format_hour(h)}[/]")
        if (d := self.pick_toughest(days, self.INSIGHTS_MIN_SAMPLES)) is not None:
            parts.append(f"[dim]toughest day:[/] [b]{self.WEEKDAY_LABELS[d]}[/]")
        if (m := self.pick_toughest(models, self.INSIGHTS_MIN_SAMPLES)) is not None:
            parts.append(f"[dim]toughest model:[/] [b]{self.short_model(m)}[/]")

        if not parts:
            insights.add_class("inactive")
            return
        insights.update(" · ".join(parts))
        insights.remove_class("inactive")

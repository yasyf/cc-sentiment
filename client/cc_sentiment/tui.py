from __future__ import annotations

import shutil
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from statistics import mean, median

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Center, Horizontal, Vertical
from textual.reactive import reactive
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
    Sparkline,
    Static,
)

from cc_sentiment.models import (
    AppState,
    ContributorId,
    GPGConfig,
    SentimentRecord,
    SSHConfig,
)
from cc_sentiment.signing import (
    GPGBackend,
    GPGKeyInfo,
    KeyDiscovery,
    SSHBackend,
    SSHKeyInfo,
)

SCORE_COLORS = {1: "red", 2: "red", 3: "yellow", 4: "green", 5: "green"}
SCORE_LABELS = {1: "frustrated", 2: "annoyed", 3: "neutral", 4: "satisfied", 5: "delighted"}
SCORE_ICONS = {1: "😤", 2: "😒", 3: "😐", 4: "😊", 5: "🤩"}

CHIP_SPEED = {"M1": 1.5, "M2": 2.0, "M3": 2.5, "M4": 2.8, "M5": 3.2}


class SetupApp(App[None]):
    CSS = """
    Screen { align: center middle; }
    #wizard { width: 80; height: auto; max-height: 44; border: heavy $accent; padding: 1 2; }
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

    BINDINGS = [("q", "quit", "Quit"), ("escape", "quit", "Quit")]

    username: reactive[str] = reactive("")
    selected_key: reactive[SSHKeyInfo | GPGKeyInfo | None] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="wizard"):
            with ContentSwitcher(initial="step-username"):
                yield from self.compose_username_step()
                yield from self.compose_discovery_step()
                yield from self.compose_remote_step()
                yield from self.compose_upload_step()
                yield from self.compose_done_step()
        yield Footer()

    def compose_username_step(self) -> ComposeResult:
        with Vertical(id="step-username"):
            yield Label("Who are you?", classes="step-title")
            yield Label(
                "We verify uploads using your public keys on GitHub. "
                "No account creation needed -- just your username.",
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
                "Checking which SSH and GPG keys you already have...",
                id="discovery-status", classes="status",
            )
            yield DataTable(id="key-table")
            yield RadioSet(id="key-select")
            yield Label("", id="no-keys-msg", classes="status")
            yield Label(
                "[dim]Why? Your key proves the data came from you. "
                "We never read or upload your private key -- only "
                "the signature it produces.[/]",
                classes="faq",
            )
            yield Button("Next", id="discovery-next", variant="primary", disabled=True)

    def compose_remote_step(self) -> ComposeResult:
        with Vertical(id="step-remote"):
            yield Label("Verifying your key", classes="step-title")
            yield Label(
                "Checking that this key is linked to your GitHub account...",
                id="remote-status", classes="status",
            )
            yield Static("", id="remote-checks")
            yield Button("Next", id="remote-next", variant="primary", disabled=True)

    def compose_upload_step(self) -> ComposeResult:
        with Vertical(id="step-upload"):
            yield Label("Register your key", classes="step-title")
            yield Label(
                "Your key needs to be on a public keyserver so the "
                "server can verify your uploads.",
                id="upload-status", classes="status",
            )
            yield RadioSet(id="upload-options")
            yield Label("", id="upload-key-text", classes="key-text")
            yield Label("", id="upload-result", classes="status")
            yield Button("Upload key", id="upload-go", variant="primary", disabled=True)
            yield Button("I'll do it manually", id="upload-skip", variant="default")

    def compose_done_step(self) -> ComposeResult:
        with Vertical(id="step-done"):
            yield Label("You're all set", classes="step-title")
            yield Label("", id="done-summary", classes="success")
            yield Label(
                "[dim]What gets uploaded: a 1-5 score and timestamp per conversation. "
                "What stays on your machine: all conversation text and code.[/]",
                classes="faq",
            )
            yield Button("Start scanning", id="done-btn", variant="primary")

    def on_mount(self) -> None:
        self.title = "cc-sentiment"
        self.sub_title = "setup"
        self.detect_username()
        table = self.query_one("#key-table", DataTable)
        table.add_columns("Type", "Fingerprint", "Email")
        table.display = False
        self.query_one("#key-select", RadioSet).display = False

    @work(thread=True)
    def detect_username(self) -> None:
        for cmd in (
            ["gh", "api", "user", "--jq", ".login"],
            ["git", "config", "github.user"],
        ):
            if not shutil.which(cmd[0]):
                continue
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    self.call_from_thread(self._set_detected_username, result.stdout.strip())
                    return
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue

    def _set_detected_username(self, username: str) -> None:
        self.query_one("#username-input", Input).value = username
        self.query_one("#username-status", Label).update(f"Auto-detected: {username}")

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
        self.call_from_thread(status.update, f"Validating {self.username}...")

        try:
            response = httpx.get(f"https://api.github.com/users/{self.username}", timeout=10.0)
            if response.status_code != 200:
                self.call_from_thread(status.update, f"[red]GitHub user '{self.username}' not found[/]")
                return
        except httpx.HTTPError:
            self.call_from_thread(status.update, "[red]Could not reach GitHub API[/]")
            return

        self.call_from_thread(self._switch_to_discovery)

    def _switch_to_discovery(self) -> None:
        self.query_one(ContentSwitcher).current = "step-discovery"
        self.discover_keys()

    @work(thread=True)
    def discover_keys(self) -> None:
        ssh_keys = KeyDiscovery.find_ssh_keys()
        gpg_keys = KeyDiscovery.find_gpg_keys()
        self.call_from_thread(self._populate_key_table, ssh_keys, gpg_keys)

    def _populate_key_table(self, ssh_keys: tuple[SSHKeyInfo, ...], gpg_keys: tuple[GPGKeyInfo, ...]) -> None:
        table = self.query_one("#key-table", DataTable)
        radio = self.query_one("#key-select", RadioSet)
        status = self.query_one("#discovery-status", Label)
        no_keys = self.query_one("#no-keys-msg", Label)

        if self.username:
            all_keys: list[SSHKeyInfo | GPGKeyInfo] = [*ssh_keys, *gpg_keys]
        else:
            all_keys = list(gpg_keys)

        self._discovered_keys = all_keys

        if not all_keys:
            status.update("No signing keys found.")
            if KeyDiscovery.has_tool("gpg"):
                no_keys.update("No worries -- we'll generate a GPG key for you automatically. Press Next.")
                self.query_one("#discovery-next", Button).disabled = False
                self._generate_gpg = True
            elif not self.username:
                no_keys.update("[red]No GPG keys found. Install gpg (brew install gnupg) or go back and enter a GitHub username to use SSH.[/]")
                self._generate_gpg = False
            else:
                no_keys.update("[red]No signing keys found. Install gpg (brew install gnupg) or create an SSH key first.[/]")
                self._generate_gpg = False
            return

        self._generate_gpg = False
        table.display = True
        radio.display = True
        status.update(f"Found {len(all_keys)} existing key{'s' if len(all_keys) != 1 else ''}:")

        radio_children = []
        for key in all_keys:
            match key:
                case SSHKeyInfo(path=p, algorithm=a, comment=c):
                    table.add_row("SSH", str(p), c)
                    radio_children.append(RadioButton(f"SSH: {p.name} ({a})"))
                case GPGKeyInfo(fpr=f, email=e, algo=a):
                    table.add_row("GPG", f"{f[:4]} {f[4:8]} ... {f[-8:-4]} {f[-4:]}", e)
                    radio_children.append(RadioButton(f"GPG: {f[-8:]} ({e})"))

        radio.mount_all(radio_children)
        self.query_one("#discovery-next", Button).disabled = False

    @on(Button.Pressed, "#discovery-next")
    def on_discovery_next(self) -> None:
        if getattr(self, "_generate_gpg", False):
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
        self.call_from_thread(status.update, "Creating a new GPG key for you...")

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
            self.call_from_thread(status.update, f"[red]Key generation failed: {result.stderr.strip()}[/]")
            return

        gpg_keys = KeyDiscovery.find_gpg_keys()
        new_key = next((k for k in gpg_keys if k.email == email), None)
        if not new_key:
            self.call_from_thread(status.update, "[red]Key generated but not found in keyring[/]")
            return

        self.selected_key = new_key
        self.call_from_thread(status.update, f"[green]Generated key: {new_key.fpr[-8:]}[/]")
        self.call_from_thread(self._go_to_remote)

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
                self.call_from_thread(checks_widget.update, "  [dim]...[/] GitHub SSH keys")
                try:
                    github_keys = KeyDiscovery.fetch_github_ssh_keys(self.username)
                    local_fp = SSHBackend(private_key_path=p).fingerprint()
                    if any(" ".join(gk.split()[:2]) == local_fp for gk in github_keys):
                        results.append("  [green]✓[/] GitHub SSH keys — matched")
                        found = True
                    else:
                        results.append("  [red]✗[/] GitHub SSH keys — not found")
                except httpx.HTTPError:
                    results.append("  [yellow]?[/] GitHub SSH keys — error")

            case GPGKeyInfo(fpr=f):
                checks = []
                if self.username:
                    checks.append("  [dim]...[/] GitHub GPG keys")
                checks.append("  [dim]...[/] keys.openpgp.org")
                self.call_from_thread(checks_widget.update, "\n".join(checks))

                if self.username:
                    try:
                        armor = KeyDiscovery.fetch_github_gpg_keys(self.username)
                        if armor:
                            import gnupg
                            imported = gnupg.GPG().import_keys(armor)
                            if f in set(imported.fingerprints):
                                results.append("  [green]✓[/] GitHub GPG keys — matched")
                                found = True
                            else:
                                results.append("  [red]✗[/] GitHub GPG keys — not found")
                        else:
                            results.append("  [red]✗[/] GitHub GPG keys — none registered")
                    except httpx.HTTPError:
                        results.append("  [yellow]?[/] GitHub GPG keys — error")

                try:
                    openpgp_key = KeyDiscovery.fetch_openpgp_key(f)
                    if openpgp_key:
                        results.append("  [green]✓[/] keys.openpgp.org — found")
                        found = True
                        self._key_on_openpgp = True
                    else:
                        results.append("  [red]✗[/] keys.openpgp.org — not found")
                except httpx.HTTPError:
                    results.append("  [yellow]?[/] keys.openpgp.org — error")

        self.call_from_thread(checks_widget.update, "\n".join(results))

        if found:
            self.call_from_thread(status.update, "[green]Key verified. You're good to go.[/]")
            self.call_from_thread(self._enable_remote_next)
            self._key_on_remote = True
        else:
            msg = "Key not found on any keyserver. We can register it in the next step."
            self.call_from_thread(status.update, msg)
            self.call_from_thread(self._enable_remote_next)
            self._key_on_remote = False

    def _enable_remote_next(self) -> None:
        self.query_one("#remote-next", Button).disabled = False

    @on(Button.Pressed, "#remote-next")
    def on_remote_next(self) -> None:
        if getattr(self, "_key_on_remote", False):
            self._save_and_finish()
        else:
            self.query_one(ContentSwitcher).current = "step-upload"
            self._populate_upload_options()

    def _populate_upload_options(self) -> None:
        radio = self.query_one("#upload-options", RadioSet)
        has_gh = shutil.which("gh") is not None
        key = self.selected_key

        options: list[RadioButton] = []
        self._upload_actions: list[str] = []

        match key:
            case SSHKeyInfo():
                rb = RadioButton(f"Add to GitHub automatically{'' if has_gh else ' (needs gh CLI)'}")
                rb.disabled = not has_gh
                options.append(rb)
                self._upload_actions.append("github-ssh")

            case GPGKeyInfo():
                if self.username:
                    rb = RadioButton(f"Add to GitHub automatically{'' if has_gh else ' (needs gh CLI)'}")
                    rb.disabled = not has_gh
                    options.append(rb)
                    self._upload_actions.append("github-gpg")

                options.append(RadioButton("Publish to keys.openpgp.org"))
                self._upload_actions.append("openpgp")

        options.append(RadioButton("Show key so I can add it myself"))
        self._upload_actions.append("manual")

        radio.mount_all(options)

        pub_text = ""
        match key:
            case SSHKeyInfo(path=p):
                pub_text = SSHBackend(private_key_path=p).public_key_text()
            case GPGKeyInfo(fpr=f):
                pub_text = GPGBackend(fpr=f).public_key_text()

        self.query_one("#upload-key-text", Label).update(f"[dim]{pub_text[:200]}...[/]" if len(pub_text) > 200 else f"[dim]{pub_text}[/]")
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
                    self.call_from_thread(result_label.update, "[green]SSH key uploaded to GitHub[/]")
                    self.call_from_thread(self._finish_after_upload)
                else:
                    self.call_from_thread(result_label.update, f"[red]Failed: {result.stderr.strip()}[/]")

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
                        self.call_from_thread(result_label.update, "[green]GPG key uploaded to GitHub[/]")
                        self.call_from_thread(self._finish_after_upload)
                    else:
                        self.call_from_thread(result_label.update, f"[red]Failed: {result.stderr.strip()}[/]")
                finally:
                    tmp_path.unlink(missing_ok=True)

            case "openpgp":
                assert isinstance(key, GPGKeyInfo)
                self.call_from_thread(result_label.update, "Uploading to keys.openpgp.org...")
                try:
                    pub_text = GPGBackend(fpr=key.fpr).public_key_text()
                    token, statuses = KeyDiscovery.upload_openpgp_key(pub_text)
                    emails = [e for e, s in statuses.items() if s == "unpublished"]
                    if emails:
                        KeyDiscovery.request_openpgp_verify(token, emails)
                        self.call_from_thread(
                            result_label.update,
                            f"[yellow]Verification email sent to {', '.join(emails)}.\n"
                            f"Click the link in the email, then press Done.[/]",
                        )
                    else:
                        self.call_from_thread(result_label.update, "[green]Key already published on keys.openpgp.org[/]")
                    self.call_from_thread(self._finish_after_upload)
                except httpx.HTTPError as e:
                    self.call_from_thread(result_label.update, f"[red]Upload failed: {e}[/]")

            case "manual":
                match key:
                    case SSHKeyInfo():
                        url = "https://github.com/settings/ssh/new"
                    case GPGKeyInfo():
                        url = "https://github.com/settings/gpg/new"
                self.call_from_thread(result_label.update, f"Add your public key at:\n{url}")
                self.call_from_thread(self._finish_after_upload)

    def _finish_after_upload(self) -> None:
        self._save_and_finish()

    def _save_and_finish(self) -> None:
        state = AppState.load()
        key = self.selected_key
        identity = self.username

        match key:
            case SSHKeyInfo(path=p):
                state.config = SSHConfig(contributor_id=ContributorId(identity), key_path=p)
            case GPGKeyInfo(fpr=f):
                if identity:
                    state.config = GPGConfig(contributor_type="github", contributor_id=ContributorId(identity), fpr=f)
                else:
                    state.config = GPGConfig(contributor_type="gpg", contributor_id=ContributorId(f), fpr=f)

        state.save()

        summary = self.query_one("#done-summary", Label)
        match key:
            case SSHKeyInfo(path=p):
                summary.update(f"Signed in as [b]{identity}[/] using SSH key [dim]{p.name}[/]")
            case GPGKeyInfo(fpr=f):
                label = identity or f"GPG {f[-8:]}"
                summary.update(f"Signed in as [b]{label}[/]")

        self.query_one(ContentSwitcher).current = "step-done"

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.exit()


class ScoreBar(Static):
    DEFAULT_CSS = """
    ScoreBar { height: 1; }
    """

    def __init__(self, score: int) -> None:
        super().__init__()
        self.score = score

    def render_bar(self, count: int, total: int, max_count: int) -> str:
        pct = 100 * count / total if total else 0
        bar_width = 40
        bar_len = int(bar_width * count / max_count) if max_count else 0
        color = SCORE_COLORS[self.score]
        icon = SCORE_ICONS[self.score]
        label = SCORE_LABELS[self.score]
        bar = "━" * bar_len + "╺" + "─" * (bar_width - bar_len)
        return f" {icon} {self.score} [{color}]{label:>11}[/]  [{color}]{bar}[/]  {pct:4.1f}%  ({count})"


class ScanApp(App[None]):
    CSS = """
    Screen { layout: vertical; background: $surface; }
    #main { height: 1fr; padding: 1 2; }
    #header-section { height: auto; }
    #title-row { height: 3; }
    #title-text { width: 1fr; }
    #score-digits { width: auto; min-width: 20; }
    #progress-section { height: auto; margin: 1 0; }
    #progress-row { height: auto; }
    #progress-label { width: auto; min-width: 24; }
    #scan-progress { width: 1fr; }
    #chart-section { height: auto; margin: 1 0 0 0; }
    #sparkline-container { height: 3; margin: 0 0 1 0; }
    #score-sparkline { height: 3; }
    #distribution { height: auto; }
    #stats-section { height: auto; margin: 1 0 0 0; }
    #stats-row { height: 3; }
    .stat-card { width: 1fr; height: 3; padding: 0 1; border: tall $primary-background; }
    .stat-card .stat-value { text-style: bold; }
    .stat-card .stat-label { color: $text-muted; }
    #status-line { height: 1; margin: 1 0 0 0; }
    #cached-line { height: 1; color: $text-muted; }
    """

    BINDINGS = [("q", "quit", "Quit"), ("escape", "quit", "Quit")]

    scored: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    records: reactive[list] = reactive(list, init=False)
    uploaded: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Initializing...")
    cached_buckets: reactive[int] = reactive(0)

    def __init__(
        self,
        state: AppState,
        engine: str,
        model_repo: str | None,
        limit: int | None,
        do_upload: bool,
    ) -> None:
        super().__init__()
        self.state = state
        self.engine = engine
        self.model_repo = model_repo
        self.limit = limit
        self.do_upload = do_upload
        self.records = []
        self._start_time = 0.0
        self._score_history: list[float] = []
        self._score_bars: dict[int, ScoreBar] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="main"):
            with Vertical(id="header-section"):
                with Horizontal(id="title-row"):
                    yield Static("[b]cc-sentiment[/b] scan", id="title-text")
                    yield Digits("-.--", id="score-digits")

            with Vertical(id="progress-section"):
                with Horizontal(id="progress-row"):
                    yield Label("Preparing...", id="progress-label")
                    yield ProgressBar(id="scan-progress", total=100, show_eta=True, show_percentage=True)

            Rule(line_style="heavy")

            with Vertical(id="chart-section"):
                yield Static("[dim]Score trend[/]", id="sparkline-label")
                with Vertical(id="sparkline-container"):
                    yield Sparkline([], id="score-sparkline")

                yield Static("", id="cached-line")

                with Vertical(id="distribution"):
                    for s in range(1, 6):
                        bar = ScoreBar(s)
                        bar.id = f"bar-{s}"
                        self._score_bars[s] = bar
                        yield bar

            with Vertical(id="stats-section"):
                Rule()
                with Horizontal(id="stats-row"):
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-buckets", classes="stat-value")
                        yield Static("buckets", classes="stat-label")
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-sessions", classes="stat-value")
                        yield Static("sessions", classes="stat-label")
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-files", classes="stat-value")
                        yield Static("files", classes="stat-label")
                    with Vertical(classes="stat-card"):
                        yield Static("--", id="stat-rate", classes="stat-value")
                        yield Static("buckets/s", classes="stat-label")

            yield Label("", id="status-line")
        yield Footer()

    def on_mount(self) -> None:
        import time
        self.title = "cc-sentiment"
        self.sub_title = "scan & upload"
        self._start_time = time.monotonic()
        self.run_scan()

    @work()
    async def run_scan(self) -> None:
        import time

        import anyio

        from cc_sentiment.pipeline import Pipeline
        from cc_sentiment.upload import Uploader

        self._update_status("[dim]Discovering transcripts...[/]")

        new_transcripts = await anyio.to_thread.run_sync(Pipeline.discover_new_transcripts, self.state)
        if self.limit is not None:
            new_transcripts = new_transcripts[:self.limit]

        if not new_transcripts:
            self._update_status("[yellow]No new transcripts found. All up to date.[/]")
            return

        total_cached = sum(
            len(self.state.processed_files[str(p)].scored_buckets)
            for p, _ in new_transcripts
            if str(p) in self.state.processed_files
        )
        if total_cached:
            self.cached_buckets = total_cached
            self.query_one("#cached-line", Static).update(
                f"[dim]{total_cached} cached buckets will be skipped[/]",
            )

        self._set_total(len(new_transcripts))
        self._update_status(f"[dim]Loading {self.engine} engine...[/]")

        all_records = await Pipeline.run(
            self.state, self.engine, self.model_repo,
            new_transcripts, self._add_records,
        )

        if self.do_upload and all_records:
            self._update_status("[dim]Uploading records...[/]")
            try:
                uploader = Uploader()
                pending = Uploader.records_from_state(self.state)
                await uploader.upload(pending, self.state)
                self._set_uploaded(len(pending))
                self._update_status(f"[green bold]Done.[/] {len(pending)} records uploaded to dashboard.")
            except Exception as e:
                self._update_status(f"[red bold]Upload failed:[/] {e}")
        elif self.do_upload and not all_records:
            pending = Uploader.records_from_state(self.state)
            if pending:
                self._update_status("[dim]Uploading pending records...[/]")
                try:
                    uploader = Uploader()
                    await uploader.upload(pending, self.state)
                    self._set_uploaded(len(pending))
                    self._update_status(f"[green bold]Done.[/] {len(pending)} pending records uploaded.")
                except Exception as e:
                    self._update_status(f"[red bold]Upload failed:[/] {e}")
            else:
                self._update_status("[yellow]No new buckets to score. All cached.[/]")
        elif all_records:
            elapsed = time.monotonic() - self._start_time
            self._update_status(f"[green bold]Done.[/] {len(all_records)} records scored in {elapsed:.0f}s.")
        else:
            self._update_status("[yellow]No new buckets to score.[/]")

    def _set_total(self, total: int) -> None:
        self.total = total
        self.query_one("#scan-progress", ProgressBar).update(total=total, progress=0)
        self.query_one("#progress-label", Label).update(f"[b]0[/]/{total} files")

    def _add_records(self, new_records: list[SentimentRecord]) -> None:
        import time

        self.records.extend(new_records)
        self.scored += 1
        self.query_one("#scan-progress", ProgressBar).update(progress=self.scored)
        self.query_one("#progress-label", Label).update(f"[b]{self.scored}[/]/{self.total} files")

        for r in new_records:
            self._score_history.append(float(r.sentiment_score))

        self._render_scores()

        elapsed = time.monotonic() - self._start_time
        rate = len(self.records) / elapsed if elapsed > 0 else 0
        self.query_one("#stat-rate", Static).update(f"{rate:.1f}")

    def _set_uploaded(self, count: int) -> None:
        self.uploaded = count

    def _update_status(self, text: str) -> None:
        self.status_text = text
        self.query_one("#status-line", Label).update(text)

    def _render_scores(self) -> None:
        if not self.records:
            return

        scores = [int(r.sentiment_score) for r in self.records]
        counts = Counter(scores)
        total = len(scores)
        max_count = max(counts.values()) if counts else 1

        for s in range(1, 6):
            n = counts.get(s, 0)
            self._score_bars[s].update(self._score_bars[s].render_bar(n, total, max_count))

        avg = mean(scores)
        self.query_one("#score-digits", Digits).update(f"{avg:.2f}")

        self.query_one("#score-sparkline", Sparkline).data = self._score_history[-80:]

        sessions = len({r.conversation_id for r in self.records})
        self.query_one("#stat-buckets", Static).update(f"[b]{total}[/]")
        self.query_one("#stat-sessions", Static).update(f"[b]{sessions}[/]")
        self.query_one("#stat-files", Static).update(f"[b]{self.scored}[/]")

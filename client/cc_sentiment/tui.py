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
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RadioButton,
    RadioSet,
    Static,
)

from cc_sentiment.models import (
    AppState,
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

CHIP_SPEED = {"M1": 1.5, "M2": 2.0, "M3": 2.5, "M4": 2.8, "M5": 3.2}


class SetupApp(App[None]):
    CSS = """
    Screen { align: center middle; }
    #wizard { width: 80; height: auto; max-height: 40; border: heavy $accent; padding: 1 2; }
    #wizard Label { margin: 1 0 0 0; }
    #wizard .step-title { text-style: bold; color: $text; margin: 0 0 1 0; }
    #wizard Input { margin: 0 0 1 0; }
    #wizard Button { margin: 1 0 0 0; }
    #wizard .status { color: $text-muted; margin: 0 0 1 0; }
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
            yield Label("Step 1: GitHub Username", classes="step-title")
            yield Label("We'll use your GitHub identity to sign uploads.", classes="status")
            yield Input(placeholder="GitHub username", id="username-input")
            yield Label("", id="username-status", classes="status")
            yield Button("Next", id="username-next", variant="primary")

    def compose_discovery_step(self) -> ComposeResult:
        with Vertical(id="step-discovery"):
            yield Label("Step 2: Signing Key", classes="step-title")
            yield Label("Scanning for SSH and GPG keys...", id="discovery-status", classes="status")
            yield DataTable(id="key-table")
            yield RadioSet(id="key-select")
            yield Label("", id="no-keys-msg", classes="status")
            yield Button("Next", id="discovery-next", variant="primary", disabled=True)

    def compose_remote_step(self) -> ComposeResult:
        with Vertical(id="step-remote"):
            yield Label("Step 3: Verify Key on Remote", classes="step-title")
            yield Label("Checking if your key is registered...", id="remote-status", classes="status")
            yield Static("", id="remote-checks")
            yield Button("Next", id="remote-next", variant="primary", disabled=True)

    def compose_upload_step(self) -> ComposeResult:
        with Vertical(id="step-upload"):
            yield Label("Step 4: Register Key", classes="step-title")
            yield Label("Your key isn't registered on any keyserver yet.", id="upload-status", classes="status")
            yield RadioSet(id="upload-options")
            yield Label("", id="upload-key-text", classes="key-text")
            yield Label("", id="upload-result", classes="status")
            yield Button("Upload", id="upload-go", variant="primary", disabled=True)
            yield Button("Skip (manual setup)", id="upload-skip", variant="default")

    def compose_done_step(self) -> ComposeResult:
        with Vertical(id="step-done"):
            yield Label("Setup Complete", classes="step-title")
            yield Label("", id="done-summary", classes="success")
            yield Button("Done", id="done-btn", variant="primary")

    def on_mount(self) -> None:
        self.title = "cc-sentiment setup"
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

        all_keys: list[SSHKeyInfo | GPGKeyInfo] = [*ssh_keys, *gpg_keys]
        self._discovered_keys = all_keys

        if not all_keys:
            status.update("No signing keys found.")
            if KeyDiscovery.has_tool("gpg"):
                no_keys.update("We can generate a GPG key for you. Press Next to continue.")
                self.query_one("#discovery-next", Button).disabled = False
                self._generate_gpg = True
            else:
                no_keys.update("[red]No keys found and gpg is not installed. Install gpg or add an SSH key.[/]")
                self._generate_gpg = False
            return

        self._generate_gpg = False
        table.display = True
        radio.display = True
        status.update(f"Found {len(all_keys)} signing key(s):")

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
        self.call_from_thread(status.update, "Generating GPG key...")

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
                self.call_from_thread(checks_widget.update, "  [dim]...[/] GitHub GPG keys\n  [dim]...[/] keys.openpgp.org")

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
                    else:
                        results.append("  [red]✗[/] keys.openpgp.org — not found")
                except httpx.HTTPError:
                    results.append("  [yellow]?[/] keys.openpgp.org — error")

        self.call_from_thread(checks_widget.update, "\n".join(results))

        if found:
            self.call_from_thread(status.update, "[green]Key found on remote. Ready to go.[/]")
            self.call_from_thread(self._enable_remote_next)
            self._key_on_remote = True
        else:
            self.call_from_thread(status.update, "Key not found on any keyserver.")
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
                label = "Upload SSH key to GitHub"
                rb = RadioButton(f"{label} {'(requires gh CLI)' if not has_gh else ''}")
                rb.disabled = not has_gh
                options.append(rb)
                self._upload_actions.append("github-ssh")

            case GPGKeyInfo():
                label = "Upload GPG key to GitHub"
                rb = RadioButton(f"{label} {'(requires gh CLI)' if not has_gh else ''}")
                rb.disabled = not has_gh
                options.append(rb)
                self._upload_actions.append("github-gpg")

                options.append(RadioButton("Upload to keys.openpgp.org"))
                self._upload_actions.append("openpgp")

        options.append(RadioButton("Manual setup (show key + instructions)"))
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
                self.call_from_thread(result_label.update, f"Add your key at: {url}")
                self.call_from_thread(self._finish_after_upload)

    def _finish_after_upload(self) -> None:
        self._save_and_finish()

    def _save_and_finish(self) -> None:
        state = AppState.load()
        key = self.selected_key

        match key:
            case SSHKeyInfo(path=p):
                state.config = SSHConfig(github_username=self.username, key_path=p)
            case GPGKeyInfo(fpr=f):
                state.config = GPGConfig(github_username=self.username, fpr=f)

        state.save()

        summary = self.query_one("#done-summary", Label)
        match key:
            case SSHKeyInfo(path=p):
                summary.update(f"Configured: {self.username} with SSH key {p}")
            case GPGKeyInfo(fpr=f):
                summary.update(f"Configured: {self.username} with GPG key {f[-8:]}")

        self.query_one(ContentSwitcher).current = "step-done"

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.exit()


class ScanApp(App[None]):
    CSS = """
    Screen { layout: vertical; }
    #scan-container { height: 100%; padding: 1 2; }
    .score-row { height: 1; }
    .score-bar { width: 1fr; }
    #stats-line { margin: 1 0 0 0; color: $text-muted; }
    #status-line { margin: 1 0 0 0; }
    #progress-label { margin: 0 0 0 0; }
    """

    scored: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    records: reactive[list] = reactive(list, init=False)
    uploaded: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Initializing...")

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

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="scan-container"):
            yield Label("", id="progress-label")
            yield ProgressBar(id="scan-progress", total=100)
            yield Static("", id="score-display")
            yield Label("", id="stats-line")
            yield Label("", id="status-line")
        yield Footer()

    def on_mount(self) -> None:
        import time
        self.title = "cc-sentiment scan"
        self._start_time = time.monotonic()
        self.run_scan()

    @work(thread=True)
    def run_scan(self) -> None:
        import time

        import anyio

        from cc_sentiment.pipeline import Pipeline
        from cc_sentiment.upload import Uploader

        self.call_from_thread(self._update_status, "Discovering transcripts...")

        new_transcripts = Pipeline.discover_new_transcripts(self.state)
        if self.limit is not None:
            new_transcripts = new_transcripts[:self.limit]

        if not new_transcripts:
            self.call_from_thread(self._update_status, "No new transcripts found.")
            return

        self.call_from_thread(self._set_total, len(new_transcripts))
        self.call_from_thread(self._update_status, f"Loading {self.engine} engine...")

        def on_records(records: list[SentimentRecord]) -> None:
            self.call_from_thread(self._add_records, records)

        all_records = anyio.from_thread.run(
            Pipeline.run,
            self.state, self.engine, self.model_repo,
            new_transcripts, on_records,
        )

        if self.do_upload and all_records:
            self.call_from_thread(self._update_status, f"Uploading {len(all_records)} records...")
            try:
                uploader = Uploader()
                anyio.from_thread.run(uploader.upload, all_records, self.state)
                self.call_from_thread(self._set_uploaded, len(all_records))
                self.call_from_thread(self._update_status, f"[green]Done. {len(all_records)} records uploaded.[/]")
            except Exception as e:
                self.call_from_thread(self._update_status, f"[red]Upload failed: {e}[/]")
        elif all_records:
            elapsed = time.monotonic() - self._start_time
            self.call_from_thread(self._update_status, f"[green]Done. {len(all_records)} records scored in {elapsed:.0f}s.[/]")
        else:
            self.call_from_thread(self._update_status, "No records produced.")

    def _set_total(self, total: int) -> None:
        self.total = total
        self.query_one("#scan-progress", ProgressBar).update(total=total, progress=0)
        self.query_one("#progress-label", Label).update(f"0/{total} transcripts")

    def _add_records(self, new_records: list[SentimentRecord]) -> None:
        self.records.extend(new_records)
        self.scored += 1
        self.query_one("#scan-progress", ProgressBar).update(progress=self.scored)
        self.query_one("#progress-label", Label).update(f"{self.scored}/{self.total} transcripts")
        self._render_scores()

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

        lines = []
        for s in range(1, 6):
            n = counts.get(s, 0)
            pct = 100 * n / total
            bar_len = int(30 * n / max_count) if max_count else 0
            color = SCORE_COLORS[s]
            label = SCORE_LABELS[s]
            lines.append(f"  [{color}]{s}[/] {label:>11}  [{color}]{'█' * bar_len}[/] {pct:.0f}% ({n})")

        self.query_one("#score-display", Static).update("\n".join(lines))

        sessions = len({r.conversation_id for r in self.records})
        self.query_one("#stats-line", Label).update(
            f"  mean={mean(scores):.1f}  median={median(scores):.0f}  "
            f"{total} buckets from {sessions} sessions"
        )

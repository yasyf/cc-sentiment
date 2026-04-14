from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from collections import Counter
from pathlib import Path
from statistics import mean

import anyio
import anyio.to_thread
import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import Screen
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

from cc_sentiment.engines import (
    HAIKU_INPUT_USD_PER_MTOK,
    HAIKU_MODEL,
    HAIKU_OUTPUT_USD_PER_MTOK,
    default_engine,
    estimate_claude_cost_usd,
    resolve_engine,
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

RESCAN_CONFIRM_SECONDS = 5.0


def detect_git_username() -> str | None:
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


async def _try_config(state: AppState, config: SSHConfig | GPGConfig) -> bool:
    from cc_sentiment.upload import Uploader
    try:
        await Uploader().verify_credentials(config)
    except httpx.HTTPError:
        return False
    state.config = config
    await anyio.to_thread.run_sync(state.save)
    return True


async def auto_setup_silent(state: AppState) -> bool:
    username = await anyio.to_thread.run_sync(detect_git_username)

    if username:
        if backend := await anyio.to_thread.run_sync(KeyDiscovery.match_ssh_key, username):
            if await _try_config(
                state,
                SSHConfig(contributor_id=ContributorId(username), key_path=backend.private_key_path),
            ):
                return True
        if backend := await anyio.to_thread.run_sync(KeyDiscovery.match_gpg_key, username):
            if await _try_config(
                state,
                GPGConfig(contributor_type="github", contributor_id=ContributorId(username), fpr=backend.fpr),
            ):
                return True

    for info in await anyio.to_thread.run_sync(KeyDiscovery.find_gpg_keys):
        if not await anyio.to_thread.run_sync(KeyDiscovery.fetch_openpgp_key, info.fpr):
            continue
        config = (
            GPGConfig(contributor_type="github", contributor_id=ContributorId(username), fpr=info.fpr)
            if username
            else GPGConfig(contributor_type="gpg", contributor_id=ContributorId(info.fpr), fpr=info.fpr)
        )
        if await _try_config(state, config):
            return True

    return False


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
            yield Label("Sorry — this machine can't run cc-sentiment.", classes="title")
            yield Label(self.message, classes="detail")
            yield Button("Quit", id="quit-btn", variant="primary")

    @on(Button.Pressed, "#quit-btn")
    def on_quit(self) -> None:
        self.dismiss(None)

    def action_done(self) -> None:
        self.dismiss(None)


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
        self.cost = estimate_claude_cost_usd(bucket_count)

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
                f"(at ${HAIKU_INPUT_USD_PER_MTOK:.2f}/MTok in, "
                f"${HAIKU_OUTPUT_USD_PER_MTOK:.2f}/MTok out). "
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
    DEFAULT_CSS = """
    SetupScreen { align: center middle; }
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
                id="loading-status", classes="status",
            )

    def compose_username_step(self) -> ComposeResult:
        with Vertical(id="step-username"):
            yield Label("Who are you?", classes="step-title")
            yield Label(
                "Your GitHub username lets us verify your uploads — "
                "no account creation, no permissions needed. "
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
                "[dim]Why do we need this? Your key is like a personal stamp — "
                "it proves the data came from you, without sharing anything "
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
            yield Button("Contribute my stats", id="done-btn", variant="primary")

    def _populate_done_info(self) -> None:
        self.query_one("#done-identify", Static).update(
            "[b]How we know it's you:[/] each upload is signed locally with "
            "your private key. The dashboard checks the signature against "
            "your public key — no account, no password, no permissions."
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
        self.query_one("#done-payload", Static).update(
            "[b]What we receive:[/] just a 1–5 score, a timestamp, and the "
            "Claude Code version for each conversation. Nothing else."
        )

    def on_mount(self) -> None:
        table = self.query_one("#key-table", DataTable)
        table.add_columns("Type", "Fingerprint", "Email")
        table.display = False
        self.query_one("#key-select", RadioSet).display = False
        self.try_auto_setup()

    @work()
    async def try_auto_setup(self) -> None:
        if await auto_setup_silent(self.state):
            self._on_auto_setup_success()
            return
        username = await anyio.to_thread.run_sync(detect_git_username)
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
        self.query_one("#done-verify", Label).update(
            "[green]Verified — the dashboard can confirm your uploads.[/]"
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
            if KeyDiscovery.has_tool("gpg"):
                no_keys.update("No worries — we'll create a signing key for you automatically. Just press Next.")
                self.query_one("#discovery-next", Button).disabled = False
                self._generate_gpg = True
            elif not self.username:
                no_keys.update("Go back and enter a GitHub username to use SSH keys instead, or install gpg (brew install gnupg) to use GPG.")
                self._generate_gpg = False
            else:
                no_keys.update("You can create an SSH key by running: ssh-keygen -t ed25519")
                self._generate_gpg = False
            return

        self._generate_gpg = False
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
                        armor = KeyDiscovery.fetch_github_gpg_keys(self.username)
                        if armor:
                            import gnupg
                            imported = gnupg.GPG().import_keys(armor)
                            if f in set(imported.fingerprints):
                                results.append("  [green]✓[/] Found on GitHub")
                                found = True
                            else:
                                results.append("  [yellow]—[/] Not on GitHub yet")
                        else:
                            results.append("  [yellow]—[/] No keys on GitHub yet")
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
            self.app.call_from_thread(status.update, "[green]Key verified — the dashboard can confirm your uploads.[/]")
            self.app.call_from_thread(self._enable_remote_next)
            self._key_on_remote = True
        else:
            msg = "Not linked yet — no worries, we can set this up in the next step."
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
                    self.app.call_from_thread(result_label.update, "[green]Key linked to GitHub — you're all set.[/]")
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
                        self.app.call_from_thread(result_label.update, "[green]Key linked to GitHub — you're all set.[/]")
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
                            f"[yellow]Almost done — check your email ({', '.join(emails)}) "
                            f"for a verification link, then press Start scanning.[/]",
                        )
                    else:
                        self.app.call_from_thread(result_label.update, "[green]Key already published — you're all set.[/]")
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
        from cc_sentiment.upload import Uploader
        await anyio.to_thread.run_sync(self.state.save)
        verify_label = self.query_one("#done-verify", Label)
        verify_label.update("[dim]Verifying with dashboard...[/]")

        assert self.state.config is not None
        try:
            await Uploader().verify_credentials(self.state.config)
        except httpx.HTTPError:
            verify_label.update(
                "[yellow]We couldn't verify your setup just yet. This usually means:\n"
                "  • If you uploaded to keys.openpgp.org — check your email for a verification link\n"
                "  • If you just added a key to GitHub — it can take a minute to propagate\n"
                "  • You can run cc-sentiment again anytime to retry[/]",
            )
        else:
            verify_label.update(
                "[green]Verified — the dashboard can confirm your uploads.[/]",
            )

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CCSentimentApp(App[None]):
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

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("r", "rescan", "Rescan"),
    ]

    scored: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Initializing...")

    def __init__(self, state: AppState, model_repo: str | None = None) -> None:
        super().__init__()
        self.state = state
        self.model_repo = model_repo
        self.records: list[SentimentRecord] = []
        self._score_history: list[float] = []
        self._score_bars: dict[int, ScoreBar] = {}
        self._start_time = 0.0
        self._rescan_armed = False
        self._rescan_pending = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="main"):
            with Vertical(id="header-section"):
                with Horizontal(id="title-row"):
                    yield Static("[b]cc-sentiment[/b]", id="title-text")
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
        self.title = "cc-sentiment"
        self._start_time = time.monotonic()
        self._seed_from_state()
        self.run_flow()

    def _seed_from_state(self) -> None:
        existing = [r for s in self.state.sessions.values() for r in s.records]
        if not existing:
            return
        self.records = list(existing)
        self._score_history = [float(r.sentiment_score) for r in existing]
        self._render_scores()

    @work()
    async def run_flow(self) -> None:
        from cc_sentiment.pipeline import Pipeline
        from cc_sentiment.upload import Uploader

        try:
            engine = await anyio.to_thread.run_sync(resolve_engine, None)
        except RuntimeError as e:
            await self.push_screen_wait(PlatformErrorScreen(str(e)))
            self.exit()
            return

        self._update_status("[dim]Discovering transcripts...[/]")
        transcripts = await anyio.to_thread.run_sync(Pipeline.discover_new_transcripts, self.state)
        pending = Uploader.records_from_state(self.state)

        if (transcripts or pending) and self.state.config is None:
            ok = await self.push_screen_wait(SetupScreen(self.state))
            if not ok:
                self.exit()
                return

        if engine == "claude" and transcripts:
            self._update_status("[dim]Counting new buckets for cost estimate...[/]")
            bucket_count = await anyio.to_thread.run_sync(
                Pipeline.count_new_buckets, self.state, transcripts
            )
            if bucket_count > 0:
                ok = await self.push_screen_wait(
                    CostReviewScreen(bucket_count, self.model_repo or HAIKU_MODEL)
                )
                if not ok:
                    self.exit()
                    return

        if transcripts:
            self._set_total(len(transcripts))
            self._update_status(f"[dim]Loading {engine} engine...[/]")
            await Pipeline.run(
                self.state, engine, self.model_repo, transcripts, self._add_records,
            )

        pending = Uploader.records_from_state(self.state)
        if pending:
            self._update_status("[dim]Uploading records...[/]")
            try:
                await Uploader().upload(pending, self.state)
            except Exception as e:
                self._update_status(f"[red bold]Upload failed:[/] {e}")
                self._rescan_armed = True
                return

        self._show_idle()
        self._rescan_armed = True

    def _show_idle(self) -> None:
        total_buckets = sum(len(s.records) for s in self.state.sessions.values())
        total_sessions = len(self.state.sessions)
        total_files = len(self.state.processed_files)
        self.query_one("#stat-buckets", Static).update(f"[b]{total_buckets}[/]")
        self.query_one("#stat-sessions", Static).update(f"[b]{total_sessions}[/]")
        self.query_one("#stat-files", Static).update(f"[b]{total_files}[/]")
        if total_sessions == 0:
            self._update_status("[green]All set — no conversations yet. Come back after using Claude Code.[/]")
        else:
            self._update_status(
                f"[green]All caught up.[/] "
                f"{total_sessions} session{'s' if total_sessions != 1 else ''}, "
                f"{total_buckets} bucket{'s' if total_buckets != 1 else ''} scored. "
                f"[dim]Press R to rescan.[/]"
            )

    def action_rescan(self) -> None:
        if not self._rescan_armed:
            return
        if self._rescan_pending:
            self._rescan_pending = False
            self._rescan_armed = False
            self._reset_for_rescan()
            self.run_flow()
            return
        self._rescan_pending = True
        self._update_status(
            "[yellow]Press R again within 5s to clear all state and rescan from scratch.[/]"
        )
        self.set_timer(RESCAN_CONFIRM_SECONDS, self._cancel_rescan)

    def _cancel_rescan(self) -> None:
        if self._rescan_pending:
            self._rescan_pending = False
            self._show_idle()

    def _reset_for_rescan(self) -> None:
        self.state.processed_files.clear()
        self.state.sessions.clear()
        self.state.save()
        self.records = []
        self._score_history = []
        self.scored = 0
        self.total = 0
        self.query_one("#scan-progress", ProgressBar).update(total=100, progress=0)
        self.query_one("#progress-label", Label).update("Preparing...")
        self.query_one("#score-digits", Digits).update("-.--")
        self.query_one("#score-sparkline", Sparkline).data = []
        for s in range(1, 6):
            self._score_bars[s].update("")
        for stat_id in ("#stat-buckets", "#stat-sessions", "#stat-files", "#stat-rate"):
            self.query_one(stat_id, Static).update("--")
        self.query_one("#cached-line", Static).update("")

    def _set_total(self, total: int) -> None:
        self.total = total
        self.query_one("#scan-progress", ProgressBar).update(total=total, progress=0)
        self.query_one("#progress-label", Label).update(f"[b]0[/]/{total} files")

    def _add_records(self, new_records: list[SentimentRecord]) -> None:
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

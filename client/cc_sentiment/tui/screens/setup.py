from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

import anyio
import anyio.to_thread
import httpx
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Input,
    Label,
    RadioButton,
    RadioSet,
    Static,
)

from cc_sentiment.engines import EngineFactory
from cc_sentiment.hardware import Hardware
from cc_sentiment.models import (
    AppState,
    ContributorId,
    GistConfig,
    GPGConfig,
    SSHConfig,
)
from cc_sentiment.signing import (
    GPGBackend,
    GPGKeyInfo,
    KeyDiscovery,
    SSHBackend,
    SSHKeyInfo,
)
from cc_sentiment.transcripts import TranscriptDiscovery

from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.status import AutoSetup, StatusEmitter


class SetupScreen(Dialog[bool]):
    ROUGH_BUCKETS_PER_FILE: ClassVar[int] = 6

    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    SetupScreen > #dialog-box { width: 80; max-height: 90%; overflow-y: auto; }
    SetupScreen > #dialog-box Label { margin: 1 0 0 0; }
    SetupScreen > #dialog-box .step-title { text-style: bold; color: $text; margin: 0 0 1 0; }
    SetupScreen > #dialog-box Input { margin: 0 0 1 0; }
    SetupScreen > #dialog-box Button { margin: 1 0 0 0; }
    SetupScreen > #dialog-box .status { color: $text-muted; margin: 0 0 1 0; }
    SetupScreen > #dialog-box .faq { color: $text-muted; margin: 1 0 0 0; }
    SetupScreen > #dialog-box .error { color: $error; }
    SetupScreen > #dialog-box .success { color: $success; }
    SetupScreen > #dialog-box DataTable { height: auto; max-height: 10; margin: 0 0 1 0; }
    SetupScreen > #dialog-box RadioSet { margin: 0 0 1 0; }
    SetupScreen > #dialog-box .key-text { color: $text-muted; margin: 0 0 1 0; max-height: 3; overflow-y: auto; }
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
        with Vertical(id="dialog-box"):
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
        match EngineFactory.default():
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
        rate = Hardware.estimate_buckets_per_sec(EngineFactory.default())
        self.query_one("#done-eta", Static).update(
            f"[dim]Found [b]{files:,}[/] transcripts. "
            f"About {TimeFormat.format_duration(files * self.ROUGH_BUCKETS_PER_FILE / rate)} to score on this Mac.[/]"
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

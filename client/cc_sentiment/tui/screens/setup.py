from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

import anyio
import anyio.to_thread
import httpx
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.worker import Worker
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
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
from cc_sentiment.tui.widgets import KeyPreview, PendingStatus, StepActions, StepBody, StepHeader


@dataclass(slots=True)
class SetupActionState:
    username_validation_running: bool = False
    discovery_action_running: bool = False
    remote_action_running: bool = False
    upload_running: bool = False


class SetupStage(StrEnum):
    LOADING = "step-loading"
    USERNAME = "step-username"
    DISCOVERY = "step-discovery"
    REMOTE = "step-remote"
    UPLOAD = "step-upload"
    DONE = "step-done"


class SetupScreen(Dialog[bool]):
    ROUGH_BUCKETS_PER_FILE: ClassVar[int] = 6
    PALETTE_CLASSES: ClassVar[tuple[str, ...]] = ("muted", "success", "warning", "error")

    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    SetupScreen > #dialog-box RadioSet {
        width: 100%;
    }
    SetupScreen > #dialog-box RadioButton {
        width: 100%;
    }
    SetupScreen > #dialog-box #remote-checks,
    SetupScreen > #dialog-box #done-summary,
    SetupScreen > #dialog-box #done-payload,
    SetupScreen > #dialog-box #done-eta {
        width: 100%;
    }
    SetupScreen > #dialog-box #loading-activity {
        margin: 0 0 1 0;
    }
    """

    BINDINGS = [
        Binding("enter", "activate_primary", "Continue", priority=True),
        Binding("escape", "cancel", "Quit", priority=True),
        Binding("ctrl+c", "cancel", "Quit", priority=True),
    ]

    username: reactive[str] = reactive("")
    selected_key: reactive[SSHKeyInfo | GPGKeyInfo | None] = reactive(None)

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.actions = SetupActionState()
        self.transition_history = [SetupStage.LOADING]
        self._username_status_snapshot = ""
        self._discovered_keys: list[SSHKeyInfo | GPGKeyInfo] = []
        self._generation_mode: str | None = None
        self._generation_radio_index: int | None = None
        self._upload_actions: list[str] = []
        self._key_on_remote = False
        self._key_on_openpgp = False
        self._remote_check_generation = 0
        self._remote_check_worker: Worker[None] | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            with ContentSwitcher(initial=SetupStage.LOADING.value):
                yield from self.compose_loading_step()
                yield from self.compose_username_step()
                yield from self.compose_discovery_step()
                yield from self.compose_remote_step()
                yield from self.compose_upload_step()
                yield from self.compose_done_step()

    @property
    def current_stage(self) -> SetupStage:
        return SetupStage(self.query_one(ContentSwitcher).current)

    def transition_to(self, stage: SetupStage) -> None:
        previous = self.current_stage
        if stage is previous:
            self.call_after_refresh(self._focus_step_target, stage)
            return
        match stage:
            case SetupStage.LOADING:
                self._reset_discovery_stage()
                self._reset_remote_stage()
                self._reset_upload_stage()
                self._reset_done_stage()
            case SetupStage.USERNAME | SetupStage.DISCOVERY:
                self._reset_remote_stage()
                self._reset_upload_stage()
                self._reset_done_stage()
            case SetupStage.REMOTE:
                if previous is not SetupStage.UPLOAD:
                    self._reset_remote_stage()
                self._reset_upload_stage()
                self._reset_done_stage()
            case SetupStage.UPLOAD:
                self._reset_upload_stage()
                self._reset_done_stage()
            case SetupStage.DONE:
                pass
        self.query_one(ContentSwitcher).current = stage.value
        self.transition_history.append(stage)
        self.call_after_refresh(self._focus_step_target, stage)

    def _focus_widget(self, widget: Input | Button | RadioSet) -> None:
        if getattr(widget, "disabled", False) or not widget.display:
            return
        widget.focus()

    def _focus_step_target(self, stage: SetupStage | str) -> None:
        match stage if isinstance(stage, SetupStage) else SetupStage(stage):
            case SetupStage.USERNAME:
                self._focus_widget(self.query_one("#username-input", Input))
            case SetupStage.DISCOVERY:
                self._focus_widget(self.query_one("#discovery-next", Button))
            case SetupStage.REMOTE:
                self._focus_widget(self.query_one("#remote-next", Button))
            case SetupStage.UPLOAD:
                self._focus_widget(self.query_one("#upload-go", Button))
            case SetupStage.DONE:
                self._focus_widget(self.query_one("#done-btn", Button))

    def _finish_username_validation(self) -> None:
        self.actions.username_validation_running = False

    def _finish_discovery_action(self) -> None:
        self.actions.discovery_action_running = False

    def _finish_upload_action(self) -> None:
        self.actions.upload_running = False

    def _current_primary_button(self) -> Button | None:
        with suppress(NoMatches, StopIteration):
            step = self.query_one(f"#{self.current_stage.value}", Vertical)
            return next(
                button
                for button in step.query(Button).results(Button)
                if button.variant == "primary"
            )
        return None

    def _set_tone(
        self,
        widget: Static,
        text: str | Text,
        tone: str = "muted",
    ) -> None:
        for palette_class in self.PALETTE_CLASSES:
            widget.remove_class(palette_class)
        widget.add_class(tone)
        widget.update(text)

    @staticmethod
    def _display_fingerprint(fingerprint: str) -> str:
        hex_chars = set("0123456789abcdefABCDEF")
        if any(char not in hex_chars for char in fingerprint):
            return fingerprint
        if len(fingerprint) == 40:
            return f"{fingerprint[:4]} {fingerprint[4:8]} ... {fingerprint[-8:-4]} {fingerprint[-4:]}"
        return " ".join(
            fingerprint[index:index + 4]
            for index in range(0, len(fingerprint), 4)
        )

    def _key_radio_label(self, key: SSHKeyInfo | GPGKeyInfo) -> Text:
        match key:
            case SSHKeyInfo(path=path, algorithm=algorithm):
                return Text(f"SSH · {path.name} · {algorithm}")
            case GPGKeyInfo(fpr=fingerprint, email=email):
                return Text(
                    f"GPG · {self._display_fingerprint(fingerprint)} · {email}"
                )

    def _clear_radio_set(self, radio: RadioSet) -> None:
        radio._pressed_button = None
        radio.display = False
        radio.remove_children()

    def _reset_discovery_stage(self) -> None:
        radio = self.query_one("#key-select", RadioSet)
        self._discovered_keys = []
        self._generation_mode = None
        self._generation_radio_index = None
        self.selected_key = None
        self._clear_radio_set(radio)
        self._set_tone(
            self.query_one("#discovery-status", Static),
            "Looking for signing keys on your machine...",
        )
        self.query_one("#discovery-help", Static).update("")
        self.query_one("#discovery-next", Button).disabled = True

    def _reset_remote_stage(self) -> None:
        self._cancel_remote_check()
        self._key_on_remote = False
        self._key_on_openpgp = False
        self._set_tone(
            self.query_one("#remote-status", Static),
            "Checking where the dashboard can read your public key...",
        )
        self.query_one("#remote-checks", Static).update("")
        self.query_one("#remote-next", Button).disabled = True

    def _reset_upload_stage(self) -> None:
        radio = self.query_one("#upload-options", RadioSet)
        self._upload_actions = []
        self._clear_radio_set(radio)
        self.query_one("#upload-key-text", KeyPreview).text = ""
        self._set_tone(self.query_one("#upload-result", Static), "")
        self.query_one("#upload-go", Button).disabled = True

    def _reset_done_stage(self) -> None:
        self._set_tone(self.query_one("#done-summary", Static), "", "success")
        self._set_tone(self.query_one("#done-verify", Static), "")
        self.query_one("#done-payload", Static).update("")
        self.query_one("#done-identify", Static).update("")
        self.query_one("#done-process", Static).update("")
        self.query_one("#done-eta", Static).update("")

    def _mount_discovery_options(self, radio_children: list[RadioButton]) -> None:
        radio = self.query_one("#key-select", RadioSet)
        radio.mount_all(radio_children)
        radio.display = True

    def _cancel_remote_check(self) -> None:
        self._remote_check_generation += 1
        if self._remote_check_worker is not None:
            self._remote_check_worker.cancel()
        self._remote_check_worker = None

    def _remote_check_is_current(
        self,
        generation: int,
        key: SSHKeyInfo | GPGKeyInfo | None,
    ) -> bool:
        return generation == self._remote_check_generation and key == self.selected_key

    def _set_remote_pending(self, generation: int, checks: str, key: SSHKeyInfo | GPGKeyInfo | None) -> None:
        if not self._remote_check_is_current(generation, key):
            return
        self.query_one("#remote-checks", Static).update(checks)

    def _apply_remote_results(
        self,
        generation: int,
        key: SSHKeyInfo | GPGKeyInfo | None,
        results: str,
        found: bool,
        key_on_openpgp: bool,
    ) -> None:
        if not self._remote_check_is_current(generation, key):
            return
        self._key_on_openpgp = key_on_openpgp
        self.query_one("#remote-checks", Static).update(results)
        self._key_on_remote = found
        self._set_tone(
            self.query_one("#remote-status", Static),
            "You're set up. Ready to upload." if found else "Not linked yet. We can set this up next.",
            "success" if found else "muted",
        )
        self._enable_remote_next()

    def compose_loading_step(self) -> ComposeResult:
        with Vertical(id="step-loading"):
            yield StepHeader(
                "Setting things up...",
                "Checking whether your current setup already works.",
            )
            yield StepBody(
                PendingStatus("", id="loading-activity"),
            )

    def compose_username_step(self) -> ComposeResult:
        with Vertical(id="step-username"):
            yield StepHeader(
                "Who are you?",
                "Add your GitHub username so we can match the public key you already use.",
            )
            yield StepBody(
                Input(placeholder="GitHub username", id="username-input"),
                Static("", id="username-status", classes="status-line muted"),
                StepActions(
                    Button("I don't use GitHub", id="username-skip", variant="default"),
                    primary=Button("Next", id="username-next", variant="primary"),
                ),
            )

    def compose_discovery_step(self) -> ComposeResult:
        with Vertical(id="step-discovery"):
            yield StepHeader(
                "Pick a signing key",
                "Choose a local key to sign your uploads.",
            )
            yield StepBody(
                RadioSet(id="key-select"),
                Static("", id="discovery-status", classes="status-line muted"),
                StepActions(
                    Button("Back", id="discovery-back", variant="default"),
                    primary=Button("Next", id="discovery-next", variant="primary", disabled=True),
                ),
                Static("", classes="after-actions-rule"),
                Static("", id="discovery-help", classes="after-actions-copy muted"),
            )

    def compose_remote_step(self) -> ComposeResult:
        with Vertical(id="step-remote"):
            yield StepHeader(
                "Verifying your key",
                "Checking where the dashboard can read your public key.",
            )
            yield StepBody(
                Static("", id="remote-checks", classes="copy-block"),
                Static("", id="remote-status", classes="status-line muted"),
                StepActions(
                    Button("Back", id="remote-back", variant="default"),
                    primary=Button("Next", id="remote-next", variant="primary", disabled=True),
                ),
            )

    def compose_upload_step(self) -> ComposeResult:
        with Vertical(id="step-upload"):
            yield StepHeader(
                "Link your key",
                "Choose how the dashboard can look up your public key.",
            )
            yield StepBody(
                RadioSet(id="upload-options"),
                KeyPreview("", id="upload-key-text"),
                Static("", id="upload-result", classes="status-line muted"),
                StepActions(
                    Button("Back", id="upload-back", variant="default"),
                    Button("I'll do it myself", id="upload-skip", variant="default"),
                    primary=Button("Link my key", id="upload-go", variant="primary", disabled=True),
                ),
            )

    def compose_done_step(self) -> ComposeResult:
        with Vertical(id="step-done"):
            yield StepHeader(
                "You're all set",
                "Review how uploads are signed and what the dashboard receives.",
            )
            yield StepBody(
                Static("", id="done-summary", classes="copy-block success"),
                Static("", id="done-verify", classes="status-line muted"),
                Static("", id="done-payload", classes="copy-block"),
                StepActions(
                    primary=Button("Contribute my stats", id="done-btn", variant="primary"),
                ),
                Static("", classes="after-actions-rule"),
                Static("", id="done-identify", classes="after-actions-copy muted"),
                Static("", id="done-process", classes="after-actions-copy muted"),
                Static("", id="done-eta", classes="after-actions-copy muted"),
            )

    def _populate_done_info(self) -> None:
        identify = self.query_one("#done-identify", Static)
        match self.state.config:
            case GistConfig(gist_id=g):
                identify.update(
                    f"How we know it's you: uploads are signed on this Mac, and gist {g[:7]} holds the public key."
                )
            case _:
                identify.update(
                    "How we know it's you: uploads are signed on this Mac, and the dashboard checks your public key."
                )
        process = self.query_one("#done-process", Static)
        match EngineFactory.default():
            case "omlx":
                process.update(
                    "Where scoring happens: entirely on your Mac with a local Gemma model."
                )
            case "claude":
                process.update(
                    "Where scoring happens: through the claude CLI on this Mac, never through the dashboard."
                )
        self.query_one("#done-payload", Static).update(self.render_sample_payload())
        self._finalize_done_screen()

    @work()
    async def _finalize_done_screen(self) -> None:
        transcripts = await anyio.to_thread.run_sync(TranscriptDiscovery.find_transcripts)
        files = len(transcripts)
        rate = Hardware.estimate_buckets_per_sec(EngineFactory.default())
        self.query_one("#done-eta", Static).update(
            f"Found {files:,} transcripts. About {TimeFormat.format_duration(files * self.ROUGH_BUCKETS_PER_FILE / rate)} to score here."
            if rate and files else ""
        )

    @staticmethod
    def render_sample_payload() -> str:
        return "\n".join(
            (
                "What gets sent:",
                "{",
                '  "time": "2026-04-15T14:23:05Z",',
                '  "conversation_id": "7f3a9b2c-0e4d-4a91-b6f8",',
                '  "sentiment_score": 4,',
                '  "claude_model": "claude-haiku-4-5",',
                '  "turn_count": 14,',
                '  "read_edit_ratio": 0.71',
                "}",
            )
        )

    def on_mount(self) -> None:
        self.query_one("#key-select", RadioSet).display = False
        self.try_auto_setup()

    def action_activate_primary(self) -> None:
        if button := self._current_primary_button():
            button.press()

    @work()
    async def try_auto_setup(self) -> None:
        emit = StatusEmitter(self.query_one("#loading-activity", PendingStatus))
        ok, username = await AutoSetup(self.state, emit).run()
        if ok:
            self._on_auto_setup_success()
            return
        self._on_auto_setup_fail(username)

    def _on_auto_setup_success(self) -> None:
        config = self.state.config
        assert config is not None
        summary = self.query_one("#done-summary", Static)
        match config:
            case SSHConfig(contributor_id=cid, key_path=p):
                summary.update(f"Signed in as {cid} using SSH key {p.name}.")
            case GPGConfig(contributor_id=cid, fpr=f):
                summary.update(f"Signed in as {cid} using GPG {self._display_fingerprint(f)}.")
            case GistConfig(contributor_id=cid, gist_id=g):
                summary.update(f"Signed in as {cid} using cc-sentiment gist {g[:7]}.")
        self._set_tone(
            self.query_one("#done-verify", Static),
            "You're set up. Ready to upload.",
            "success",
        )
        self._populate_done_info()
        self.transition_to(SetupStage.DONE)

    def _on_auto_setup_fail(self, username: str | None) -> None:
        if username:
            self.query_one("#username-input", Input).value = username
            self._set_tone(
                self.query_one("#username-status", Static),
                f"Auto-detected: {username}",
            )
        self.transition_to(SetupStage.USERNAME)

    @on(Button.Pressed, "#username-next")
    def on_username_next(self) -> None:
        if self.actions.username_validation_running:
            return
        username = self.query_one("#username-input", Input).value.strip()
        if not username:
            self._set_tone(
                self.query_one("#username-status", Static),
                "Username is required",
                "error",
            )
            return
        self._username_status_snapshot = str(self.query_one("#username-status", Static).render())
        self.username = username
        self.actions.username_validation_running = True
        self.validate_and_discover()

    @on(Button.Pressed, "#username-skip")
    def on_username_skip(self) -> None:
        self.username = ""
        self._switch_to_discovery()

    @work(thread=True)
    def validate_and_discover(self) -> None:
        status = self.query_one("#username-status", Static)
        self.app.call_from_thread(self._set_tone, status, f"Validating {self.username}...")
        try:
            response = httpx.get(f"https://api.github.com/users/{self.username}", timeout=10.0)
        except httpx.HTTPError:
            self.app.call_from_thread(self._set_tone, status, "Could not reach GitHub API", "error")
            self.app.call_from_thread(self._finish_username_validation)
            return
        if response.status_code != 200:
            self.app.call_from_thread(
                self._set_tone,
                status,
                f"GitHub user '{self.username}' not found",
                "error",
            )
            self.app.call_from_thread(self._finish_username_validation)
            return
        self.app.call_from_thread(self._switch_to_discovery)
        self.app.call_from_thread(self._finish_username_validation)

    def _switch_to_discovery(self) -> None:
        self._reset_discovery_stage()
        self.transition_to(SetupStage.DISCOVERY)
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
        radio = self.query_one("#key-select", RadioSet)
        status = self.query_one("#discovery-status", Static)
        help_text = self.query_one("#discovery-help", Static)
        next_btn = self.query_one("#discovery-next", Button)
        self._clear_radio_set(radio)

        all_keys: list[SSHKeyInfo | GPGKeyInfo] = (
            [*ssh_keys, *gpg_keys] if self.username else list(gpg_keys)
        )
        self._discovered_keys = all_keys
        self._generation_mode = self._pick_generation_mode()
        self._generation_radio_index = len(all_keys) if self._generation_mode is not None else None

        radio_children = [
            *[
                RadioButton(self._key_radio_label(key))
                for key in all_keys
            ],
            *([RadioButton("Create a new cc-sentiment key")] if self._generation_mode is not None else []),
        ]

        if not radio_children:
            self._set_tone(status, "No signing keys found on your machine.")
            if not self.username:
                help_text.update(
                    "Go back and enter a GitHub username, or install gpg "
                    "(brew install gnupg) to use GPG."
                )
            else:
                help_text.update(
                    "To create a signing key for cc-sentiment, install the GitHub CLI "
                    "(brew install gh) or gpg (brew install gnupg)."
                )
            next_btn.disabled = True
            return

        if not all_keys:
            self._set_tone(status, "No signing keys found on your machine.")
            help_text.update(self._generation_prompt())
        else:
            help_text.update("")
            hint = (
                " Pick one, or create a new cc-sentiment key."
                if self._generation_mode
                else " Pick one."
                if len(all_keys) > 1
                else ""
            )
            plural = "s" if len(all_keys) != 1 else ""
            self._set_tone(status, f"Found {len(all_keys)} key{plural} on your machine.{hint}")

        self.call_after_refresh(self._mount_discovery_options, radio_children)
        next_btn.disabled = False
        if self.current_stage is SetupStage.DISCOVERY:
            self._focus_step_target(SetupStage.DISCOVERY)

    def _pick_generation_mode(self) -> str | None:
        if self.username and KeyDiscovery.gh_authenticated():
            return "gist"
        if self.username and KeyDiscovery.has_tool("ssh-keygen"):
            return "ssh"
        if KeyDiscovery.has_tool("gpg"):
            return "gpg"
        return None

    def _generation_prompt(self) -> str:
        match self._generation_mode:
            case "gist":
                return (
                    "We can make a small signing key for cc-sentiment and save its public key in a gist."
                )
            case "ssh":
                return (
                    "We'll create a small SSH key here, then help you add its public key to GitHub."
                )
            case "gpg":
                return "No problem. We'll create a GPG key for you here."
            case _:
                return ""

    @on(Button.Pressed, "#discovery-back")
    def on_discovery_back(self) -> None:
        self.query_one("#username-status", Static).update(self._username_status_snapshot)
        self.transition_to(SetupStage.USERNAME)

    @on(RadioSet.Changed, "#key-select")
    def on_discovery_selection_changed(self) -> None:
        self._cancel_remote_check()

    @on(Button.Pressed, "#discovery-next")
    def on_discovery_next(self) -> None:
        if self.actions.discovery_action_running:
            return
        if not self._discovered_keys:
            self.actions.discovery_action_running = True
            self._dispatch_generation()
            return
        radio = self.query_one("#key-select", RadioSet)
        idx = radio.pressed_index if radio.pressed_index >= 0 else 0
        if self._generation_radio_index is not None and idx == self._generation_radio_index:
            self.actions.discovery_action_running = True
            self._dispatch_generation()
            return
        self.actions.discovery_action_running = True
        self.selected_key = self._discovered_keys[idx]
        self._go_to_remote()

    def _dispatch_generation(self) -> None:
        match self._generation_mode:
            case "gist":
                self.generate_gist_key()
            case "ssh":
                self.generate_managed_ssh_key()
            case "gpg":
                self.generate_gpg_key()

    @work(thread=True)
    def generate_gpg_key(self) -> None:
        status = self.query_one("#discovery-status", Static)
        self.app.call_from_thread(self._set_tone, status, "Creating a signing key for you...")

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
            self.app.call_from_thread(
                self._set_tone,
                status,
                f"Key generation failed: {result.stderr.strip()}",
                "error",
            )
            self.app.call_from_thread(self._finish_discovery_action)
            return

        gpg_keys = KeyDiscovery.find_gpg_keys()
        new_key = next((k for k in gpg_keys if k.email == email), None)
        if not new_key:
            self.app.call_from_thread(
                self._set_tone,
                status,
                "Key generated but not found in keyring",
                "error",
            )
            self.app.call_from_thread(self._finish_discovery_action)
            return

        self.selected_key = new_key
        self.app.call_from_thread(
            self._set_tone,
            status,
            f"Generated key: {self._display_fingerprint(new_key.fpr)}",
            "success",
        )
        self.app.call_from_thread(self._go_to_remote)

    @work(thread=True)
    def generate_managed_ssh_key(self) -> None:
        status = self.query_one("#discovery-status", Static)
        self.app.call_from_thread(self._set_tone, status, "Creating a local signing key for cc-sentiment...")
        try:
            key_path = KeyDiscovery.generate_gist_keypair()
        except subprocess.CalledProcessError as e:
            err = (e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr or str(e)).strip()
            self.app.call_from_thread(self._set_tone, status, f"Couldn't create the key: {err}", "error")
            self.app.call_from_thread(self._finish_discovery_action)
            return
        parts = key_path.with_suffix(key_path.suffix + ".pub").read_text().strip().split()
        self.selected_key = SSHKeyInfo(
            path=key_path,
            algorithm=parts[0] if len(parts) >= 2 else "unknown",
            comment=parts[2] if len(parts) >= 3 else "",
        )
        self.app.call_from_thread(
            self._set_tone,
            status,
            "Created a local key. Let's link it next.",
            "success",
        )
        self.app.call_from_thread(self._go_to_remote)

    @work(thread=True)
    def generate_gist_key(self) -> None:
        status = self.query_one("#discovery-status", Static)
        self.app.call_from_thread(
            self._set_tone,
            status,
            "Creating a signing key and saving its public key as a gist...",
        )
        try:
            key_path = KeyDiscovery.generate_gist_keypair()
            gist_id = KeyDiscovery.create_gist(key_path)
        except subprocess.CalledProcessError as e:
            err = (e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr or str(e)).strip()
            self.app.call_from_thread(self._set_tone, status, f"Couldn't create the gist: {err}", "error")
            self.app.call_from_thread(self._finish_discovery_action)
            return
        self.state.config = GistConfig(
            contributor_id=ContributorId(self.username),
            key_path=key_path,
            gist_id=gist_id,
        )
        self.app.call_from_thread(
            self._set_tone,
            status,
            f"Saved key to gist {gist_id[:7]}",
            "success",
        )
        self.app.call_from_thread(self._finish_gist, gist_id)

    def _finish_gist(self, gist_id: str) -> None:
        self.query_one("#done-summary", Static).update(
            f"Signed in as {self.username} using cc-sentiment gist {gist_id[:7]}."
        )
        self._populate_done_info()
        self._finish_discovery_action()
        self.transition_to(SetupStage.DONE)
        self.verify_server_config()

    def _go_to_remote(self) -> None:
        self._finish_discovery_action()
        self.transition_to(SetupStage.REMOTE)
        self._remote_check_generation += 1
        self._remote_check_worker = self.check_remotes(self._remote_check_generation, self.selected_key)

    @work(thread=True)
    def check_remotes(
        self,
        generation: int | None = None,
        key: SSHKeyInfo | GPGKeyInfo | None = None,
    ) -> None:
        generation = self._remote_check_generation if generation is None else generation
        key = self.selected_key if key is None else key
        results: list[str] = []
        found = False
        key_on_openpgp = False

        match key:
            case SSHKeyInfo(path=p):
                self.app.call_from_thread(self._set_remote_pending, generation, "  [dim]...[/] Checking GitHub", key)
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
                self.app.call_from_thread(self._set_remote_pending, generation, "\n".join(checks), key)

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
                        key_on_openpgp = True
                    else:
                        results.append("  [yellow]—[/] Not on keys.openpgp.org yet")
                except httpx.HTTPError:
                    results.append("  [yellow]?[/] Couldn't reach keys.openpgp.org")

        self.app.call_from_thread(
            self._apply_remote_results,
            generation,
            key,
            "\n".join(results),
            found,
            key_on_openpgp,
        )

    def _enable_remote_next(self) -> None:
        self.query_one("#remote-next", Button).disabled = False
        if self.current_stage is SetupStage.REMOTE:
            self._focus_step_target(SetupStage.REMOTE)

    @on(Button.Pressed, "#remote-back")
    def on_remote_back(self) -> None:
        self._switch_to_discovery()

    @on(Button.Pressed, "#remote-next")
    async def on_remote_next(self) -> None:
        if self.actions.remote_action_running:
            return
        self.actions.remote_action_running = True
        try:
            if self._key_on_remote:
                self._save_and_finish()
            else:
                self.transition_to(SetupStage.UPLOAD)
                await self._populate_upload_options()
        finally:
            self.actions.remote_action_running = False

    async def _populate_upload_options(self) -> None:
        radio = self.query_one("#upload-options", RadioSet)
        self._clear_radio_set(radio)
        has_gh = shutil.which("gh") is not None
        gh_authed = has_gh and await anyio.to_thread.run_sync(KeyDiscovery.gh_authenticated)
        gh_suffix = "" if gh_authed else (" (needs `gh auth login`)" if has_gh else " (needs gh CLI)")
        key = self.selected_key

        options: list[RadioButton] = []
        self._upload_actions: list[str] = []

        match key:
            case SSHKeyInfo():
                rb = RadioButton(f"Link to GitHub{gh_suffix}")
                rb.disabled = not gh_authed
                options.append(rb)
                self._upload_actions.append("github-ssh")

            case GPGKeyInfo():
                if self.username:
                    rb = RadioButton(f"Link to GitHub{gh_suffix}")
                    rb.disabled = not gh_authed
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
            radio.display = True

        pub_text = ""
        match key:
            case SSHKeyInfo(path=p):
                pub_text = await anyio.to_thread.run_sync(SSHBackend(private_key_path=p).public_key_text)
            case GPGKeyInfo(fpr=f):
                pub_text = await anyio.to_thread.run_sync(GPGBackend(fpr=f).public_key_text)

        self.query_one("#upload-key-text", KeyPreview).text = pub_text
        self.query_one("#upload-go", Button).disabled = False
        if self.current_stage is SetupStage.UPLOAD:
            self._focus_step_target(SetupStage.UPLOAD)

    @on(Button.Pressed, "#upload-go")
    def on_upload_go(self) -> None:
        if self.actions.upload_running:
            return
        radio = self.query_one("#upload-options", RadioSet)
        idx = radio.pressed_index if radio.pressed_index >= 0 else 0
        action = self._upload_actions[idx]
        self.actions.upload_running = True
        self.run_upload(action)

    @on(Button.Pressed, "#upload-skip")
    def on_upload_skip(self) -> None:
        self._save_and_finish()

    @on(Button.Pressed, "#upload-back")
    def on_upload_back(self) -> None:
        self.transition_to(SetupStage.REMOTE)

    @work(thread=True)
    def run_upload(self, action: str) -> None:
        result_label = self.query_one("#upload-result", Static)
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
                    self.app.call_from_thread(self._set_tone, result_label, "Key linked to GitHub. You're all set.", "success")
                    self.app.call_from_thread(self._save_and_finish)
                else:
                    self.app.call_from_thread(
                        self._set_tone,
                        result_label,
                        f"Something went wrong: {result.stderr.strip()}",
                        "error",
                    )
                    self.app.call_from_thread(self._finish_upload_action)

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
                        self.app.call_from_thread(self._set_tone, result_label, "Key linked to GitHub. You're all set.", "success")
                        self.app.call_from_thread(self._save_and_finish)
                    else:
                        self.app.call_from_thread(
                            self._set_tone,
                            result_label,
                            f"Something went wrong: {result.stderr.strip()}",
                            "error",
                        )
                        self.app.call_from_thread(self._finish_upload_action)
                finally:
                    tmp_path.unlink(missing_ok=True)

            case "openpgp":
                assert isinstance(key, GPGKeyInfo)
                self.app.call_from_thread(self._set_tone, result_label, "Publishing to keys.openpgp.org...")
                try:
                    pub_text = GPGBackend(fpr=key.fpr).public_key_text()
                    token, statuses = KeyDiscovery.upload_openpgp_key(pub_text)
                    emails = [e for e, s in statuses.items() if s == "unpublished"]
                    if emails:
                        KeyDiscovery.request_openpgp_verify(token, emails)
                        self.app.call_from_thread(
                            self._set_tone,
                            result_label,
                            f"Almost done. Check your email ({', '.join(emails)}) for a verification link.",
                            "warning",
                        )
                    else:
                        self.app.call_from_thread(self._set_tone, result_label, "Key already published. You're all set.", "success")
                    self.app.call_from_thread(self._save_and_finish)
                except httpx.HTTPError as e:
                    self.app.call_from_thread(
                        self._set_tone,
                        result_label,
                        f"Couldn't reach keys.openpgp.org: {e}",
                        "error",
                    )
                    self.app.call_from_thread(self._finish_upload_action)

            case "manual":
                assert key is not None
                url = (
                    "https://github.com/settings/ssh/new"
                    if isinstance(key, SSHKeyInfo)
                    else "https://github.com/settings/gpg/new"
                )
                self.app.call_from_thread(self._set_tone, result_label, f"Paste your public key at:\n{url}")
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

        summary = self.query_one("#done-summary", Static)
        match key:
            case SSHKeyInfo(path=p):
                summary.update(f"Signed in as {identity} using SSH key {p.name}.")
            case GPGKeyInfo(fpr=f):
                label = identity or f"GPG {f[-8:]}"
                summary.update(f"Signed in as {label}.")

        self._populate_done_info()
        self._finish_upload_action()
        self.transition_to(SetupStage.DONE)
        self.verify_server_config()

    @work()
    async def verify_server_config(self) -> None:
        from cc_sentiment.upload import AuthOk, Uploader
        await anyio.to_thread.run_sync(self.state.save)
        verify_label = self.query_one("#done-verify", Static)
        self._set_tone(verify_label, "Verifying with the dashboard...")

        assert self.state.config is not None
        if isinstance(await Uploader().probe_credentials(self.state.config), AuthOk):
            self._set_tone(verify_label, "You're set up. Ready to upload.", "success")
        else:
            self._set_tone(verify_label, "We couldn't verify your setup just yet.", "warning")

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

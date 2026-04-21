from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from enum import StrEnum
import shutil
import subprocess
import tempfile
from pathlib import Path
from time import monotonic
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
    DataTable,
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
from cc_sentiment.tui.widgets import Card, KeyPreview, PendingStatus, StepActions, StepBody, StepHeader

PENDING_PROPAGATION_WINDOW_SECONDS = 300.0
PENDING_RETRY_SECONDS = 10.0


@dataclass(slots=True)
class SetupActionState:
    username_validation_running: bool = False
    discovery_action_running: bool = False
    remote_action_running: bool = False
    upload_running: bool = False


@dataclass(frozen=True, slots=True)
class UploadOption:
    action: str
    label: str


@dataclass(frozen=True, slots=True)
class RemoteCheckRow:
    glyph: str
    check: str
    detail: str
    tone: str


@dataclass(slots=True)
class VerificationPollState:
    started_at: float
    next_retry_at: float | None = None

    def restart(self, now: float) -> None:
        self.started_at = now
        self.next_retry_at = None

    def schedule_next(self, now: float) -> None:
        self.next_retry_at = now + PENDING_RETRY_SECONDS

    def clear(self) -> None:
        self.next_retry_at = None

    def due(self, now: float) -> bool:
        return self.next_retry_at is not None and now >= self.next_retry_at


class SetupStage(StrEnum):
    LOADING = "step-loading"
    USERNAME = "step-username"
    DISCOVERY = "step-discovery"
    REMOTE = "step-remote"
    UPLOAD = "step-upload"
    DONE = "step-done"


class VerificationState(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"
    FAILED = "failed"


class DoneBranch(Vertical):
    verification_state: reactive[VerificationState] = reactive(VerificationState.VERIFIED, recompose=True)
    verification_ok: reactive[bool] = reactive(True, recompose=True)
    summary_text: reactive[str] = reactive("", recompose=True)
    identify_text: reactive[str] = reactive("", recompose=True)
    process_text: reactive[str] = reactive("", recompose=True)
    eta_text: reactive[str] = reactive("", recompose=True)
    instructions_text: reactive[str] = reactive("", recompose=True)
    pending_label: reactive[str] = reactive("")

    def _visible_verification_state(self) -> VerificationState:
        return (
            self.verification_state
            if self.verification_ok or self.verification_state is not VerificationState.VERIFIED
            else VerificationState.PENDING
        )

    def _verification_message(self) -> tuple[str, str]:
        match self._visible_verification_state():
            case VerificationState.VERIFIED:
                return "You're set up. Ready to upload.", "success"
            case VerificationState.PENDING:
                return "The dashboard still can't verify this public key.", "warning"
            case VerificationState.FAILED:
                return "We still couldn't verify this public key.", "error"

    def compose(self) -> ComposeResult:
        verification_text, verification_tone = self._verification_message()
        summary_text = self.summary_text or "Signed in with your selected key."

        match self._visible_verification_state():
            case VerificationState.VERIFIED:
                yield Card(
                    Static(summary_text, id="done-summary", classes="success"),
                    Static(verification_text, id="done-verify", classes="success"),
                    title="verified",
                    id="done-summary-card",
                    classes="done-card success",
                )
                yield Static(
                    "Only signed stats leave your Mac, one row per conversation.",
                    id="done-payload-lead",
                    classes="muted",
                )
                yield Card(
                    Static(SetupScreen.render_sample_payload(), id="done-payload", classes="code"),
                    title="What actually gets sent",
                    id="done-payload-card",
                    classes="done-card",
                )
                yield StepActions(
                    primary=Button("Contribute my stats", id="done-btn", variant="primary"),
                )
                yield Static(self.identify_text, id="done-identify", classes="muted")
                yield Static(self.process_text, id="done-process", classes="muted")
                yield Static(self.eta_text, id="done-eta", classes="muted")
            case VerificationState.PENDING:
                yield Card(
                    Static(summary_text, id="done-summary", classes="warning"),
                    Static(verification_text, id="done-verify", classes=verification_tone),
                    title="pending",
                    id="done-summary-card",
                    classes="done-card warning",
                )
                yield PendingStatus(self.pending_label, id="pending-status")
                yield Card(
                    Static(self.instructions_text, id="done-instructions", classes="muted"),
                    title="Next steps",
                    id="done-instructions-card",
                    classes="done-card",
                )
                yield StepActions(
                    Button("Exit, continue later", id="pending-exit", variant="default"),
                    primary=Button("Retry now", id="pending-retry", variant="primary"),
                )
            case VerificationState.FAILED:
                yield Card(
                    Static(summary_text, id="done-summary", classes="error"),
                    Static(verification_text, id="done-verify", classes=verification_tone),
                    title="failed",
                    id="done-summary-card",
                    classes="done-card error",
                )
                yield Card(
                    Static(self.instructions_text, id="done-instructions", classes="muted"),
                    title="Next steps",
                    id="done-instructions-card",
                    classes="done-card",
                )
                yield StepActions(
                    Button("Exit", id="failed-exit", variant="default"),
                    primary=Button("Retry", id="failed-retry", variant="primary"),
                )

    def watch_pending_label(self, label: str) -> None:
        with suppress(NoMatches):
            self.query_one("#pending-status", PendingStatus).label = label


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
    SetupScreen > #dialog-box #done-branch,
    SetupScreen > #dialog-box #done-payload-lead,
    SetupScreen > #dialog-box #done-instructions,
    SetupScreen > #dialog-box #pending-status,
    SetupScreen > #dialog-box #done-identify,
    SetupScreen > #dialog-box #done-process {
        width: 100%;
    }
    SetupScreen > #dialog-box .done-card {
        width: 100%;
        margin: 0 0 1 0;
    }
    SetupScreen > #dialog-box .done-card.success {
        border: round $success;
    }
    SetupScreen > #dialog-box .done-card.warning {
        border: round $warning;
    }
    SetupScreen > #dialog-box .done-card.error {
        border: round $error;
    }
    """

    BINDINGS = [
        Binding("enter", "activate_primary", "Continue", priority=True),
        Binding("escape", "cancel", "Quit", priority=True),
        Binding("ctrl+c", "cancel", "Quit", priority=True),
    ]

    username: reactive[str] = reactive("")
    selected_key: reactive[SSHKeyInfo | GPGKeyInfo | None] = reactive(None)
    verification_state: reactive[VerificationState] = reactive(VerificationState.VERIFIED)
    verification_ok: reactive[bool] = reactive(True)

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
        self.verification_poll = VerificationPollState(started_at=monotonic())
        self._verify_worker: Worker[None] | None = None
        self._done_summary_text = ""
        self._done_identify_text = ""
        self._done_process_text = ""
        self._done_eta_text = ""
        self._verification_detail = ""
        self._verification_action = ""

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

    def transition_to(self, stage: SetupStage, preserve_remote: bool = False) -> None:
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
                if previous is not SetupStage.UPLOAD and not preserve_remote:
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
                with suppress(NoMatches):
                    self._focus_widget(self.query_one("#done-btn", Button))
                    return
                with suppress(NoMatches):
                    self._focus_widget(self.query_one("#pending-retry", Button))
                    return
                with suppress(NoMatches):
                    self._focus_widget(self.query_one("#failed-retry", Button))
                    return
                with suppress(NoMatches):
                    self._focus_widget(self.query_one("#pending-exit", Button))
                    return
                with suppress(NoMatches):
                    self._focus_widget(self.query_one("#failed-exit", Button))

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

    def watch_verification_state(self, _: VerificationState) -> None:
        self._render_done_branch()

    def watch_verification_ok(self, _: bool) -> None:
        self._render_done_branch()

    def _set_tone(
        self,
        widget: Static,
        text: str | Text,
        tone: str = "muted",
    ) -> None:
        self._set_palette_classes(widget, tone)
        widget.update(text)

    def _set_palette_classes(self, widget: Static, tone: str | None) -> None:
        for palette_class in self.PALETTE_CLASSES:
            widget.remove_class(palette_class)
        if tone is not None:
            widget.add_class(tone)

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
        self._set_remote_header(
            "Verifying your key",
            "Checking where the dashboard can read your public key.",
        )
        self._render_remote_checks([])
        self._set_tone(
            self.query_one("#remote-status", Static),
            "Checking where the dashboard can read your public key...",
        )
        self.query_one("#remote-next", Button).disabled = True

    def _reset_upload_stage(self) -> None:
        radio = self.query_one("#upload-options", RadioSet)
        self._upload_actions = []
        self._clear_radio_set(radio)
        self.query_one("#upload-key-text", KeyPreview).text = ""
        self._set_tone(self.query_one("#upload-result", Static), "")
        go_button = self.query_one("#upload-go", Button)
        go_button.label = "Link my key"
        go_button.disabled = True

    def _reset_done_stage(self) -> None:
        self._done_summary_text = ""
        self._done_identify_text = ""
        self._done_process_text = ""
        self._done_eta_text = ""
        self._verification_detail = ""
        self._verification_action = ""
        self.verification_poll.restart(monotonic())
        self._cancel_verify_worker()
        self._render_done_branch()

    def _set_done_header(self, title: str, explainer: str, tone: str | None = None) -> None:
        header = self.query_one("#step-done", Vertical).query_one(StepHeader)
        title_widget = header.query_one(".step-title", Static)
        explainer_widget = header.query_one(".step-explainer", Static)
        self._set_palette_classes(title_widget, tone)
        title_widget.update(title)
        self._set_tone(explainer_widget, explainer)

    @staticmethod
    def _format_pending_elapsed(seconds: float) -> str:
        elapsed = max(0, int(seconds))
        return f"{elapsed // 60}:{elapsed % 60:02d}"

    def _pending_label(self) -> str:
        return f"Waiting for your key to propagate… {self._format_pending_elapsed(monotonic() - self.verification_poll.started_at)}"

    def _manual_destination_url(self) -> str:
        return (
            "https://github.com/settings/ssh/new"
            if isinstance(self.selected_key, SSHKeyInfo)
            else "https://github.com/settings/gpg/new"
        )

    def _instructions_text(self) -> str:
        prefix = (
            "sentiments.cc is temporarily unreachable right now. "
            if self._verification_detail == "temporarily unreachable"
            else ""
        )
        match self._verification_action:
            case "manual":
                return prefix + f"Paste your public key at {self._manual_destination_url()}, then retry once GitHub shows it."
            case "openpgp":
                return prefix + "Check your email for the keys.openpgp.org verification link, finish publishing the key, then retry."
            case "github-auth":
                return prefix + "Try `gh auth login` and retry."
            case "github-ssh" | "github-gpg":
                return prefix + "Give GitHub a moment to propagate your public key, then retry."
            case "gist":
                return prefix + "Keep your cc-sentiment gist public so the dashboard can read the key, then retry."
            case _:
                return prefix + "Wait a moment for your public key to propagate, then retry."

    def _visible_verification_state(self) -> VerificationState:
        return (
            self.verification_state
            if self.verification_ok or self.verification_state is not VerificationState.VERIFIED
            else VerificationState.PENDING
        )

    def _render_done_branch(self) -> None:
        match self._visible_verification_state():
            case VerificationState.VERIFIED:
                self._set_done_header(
                    "You're all set",
                    "Review how uploads are signed and what the dashboard receives.",
                    "success",
                )
            case VerificationState.PENDING:
                self._set_done_header(
                    "Waiting for your key",
                    "The dashboard can see your setup, but it can't verify this key yet.",
                    "warning",
                )
            case VerificationState.FAILED:
                self._set_done_header(
                    "We couldn't verify your key",
                    "The dashboard still can't read this public key. Check the instructions, then retry.",
                    "error",
                )
        with suppress(NoMatches):
            branch = self.query_one("#done-branch", DoneBranch)
            branch.summary_text = self._done_summary_text
            branch.identify_text = self._done_identify_text
            branch.process_text = self._done_process_text
            branch.eta_text = self._done_eta_text
            branch.instructions_text = self._instructions_text()
            branch.pending_label = self._pending_label()
            branch.verification_state = self.verification_state
            branch.verification_ok = self.verification_ok
            if self.current_stage is SetupStage.DONE:
                self.set_timer(0.01, lambda: self._focus_step_target(SetupStage.DONE))

    def _set_verification_branch(
        self,
        state: VerificationState,
        detail: str | None = None,
    ) -> None:
        if detail is not None:
            self._verification_detail = detail
        self.verification_state = state
        self.verification_ok = state is VerificationState.VERIFIED
        self._render_done_branch()

    def _refresh_pending_status(self) -> None:
        if self._visible_verification_state() is not VerificationState.PENDING:
            return
        with suppress(NoMatches):
            self.query_one("#done-branch", DoneBranch).pending_label = self._pending_label()

    def _cancel_verify_worker(self) -> None:
        if self._verify_worker is not None:
            self._verify_worker.cancel()
        self._verify_worker = None

    def _poll_verification_if_due(self) -> None:
        if self.current_stage is not SetupStage.DONE:
            return
        if self._visible_verification_state() is not VerificationState.PENDING:
            return
        if self._verify_worker is not None and self._verify_worker.is_running:
            return
        if not self.verification_poll.due(monotonic()):
            return
        self.verification_poll.clear()
        self.verify_server_config()

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

    def _configure_remote_checks_table(self) -> DataTable:
        table = self.query_one("#remote-checks", DataTable)
        if not table.ordered_columns:
            table.add_columns("glyph", "check", "detail")
        return table

    @staticmethod
    def _remote_tone_style(tone: str) -> str:
        match tone:
            case "success":
                return "green"
            case "warning":
                return "yellow"
            case "muted":
                return "dim"
            case _:
                return ""

    def _render_remote_checks(self, rows: list[RemoteCheckRow]) -> None:
        table = self._configure_remote_checks_table()
        table.clear(columns=False)
        for row in rows:
            table.add_row(
                *(
                    Text(value, style=self._remote_tone_style(row.tone))
                    for value in (row.glyph, row.check, row.detail)
                )
            )

    def _set_remote_header(self, title: str, explainer: str, tone: str | None = None) -> None:
        header = self.query_one("#step-remote", Vertical).query_one(StepHeader)
        title_widget = header.query_one(".step-title", Static)
        explainer_widget = header.query_one(".step-explainer", Static)
        self._set_palette_classes(title_widget, tone)
        title_widget.update(title)
        self._set_tone(explainer_widget, explainer)

    def _set_remote_pending(self, generation: int, key: SSHKeyInfo | GPGKeyInfo | None) -> None:
        if not self._remote_check_is_current(generation, key):
            return
        if self.current_stage is SetupStage.REMOTE:
            self._set_remote_header(
                "Verifying your key",
                "Checking where the dashboard can read your public key.",
            )
            self._render_remote_checks([])
            self._set_tone(
                self.query_one("#remote-status", Static),
                "Checking where the dashboard can read your public key...",
            )
            self.query_one("#remote-next", Button).disabled = True
            return
        self._set_tone(
            self.query_one("#discovery-status", Static),
            "Checking where the dashboard can read your public key...",
        )
        self.query_one("#discovery-next", Button).disabled = True

    def _apply_remote_results(
        self,
        generation: int,
        key: SSHKeyInfo | GPGKeyInfo | None,
        results: list[RemoteCheckRow],
        found: bool,
        key_on_openpgp: bool,
    ) -> None:
        if not self._remote_check_is_current(generation, key):
            return
        self._remote_check_worker = None
        self._key_on_openpgp = key_on_openpgp
        self._render_remote_checks(results)
        self._key_on_remote = found
        if found:
            self._set_remote_header(
                "Your key is ready",
                "We found this public key somewhere the dashboard can already read.",
                "success",
            )
            self._set_tone(
                self.query_one("#remote-status", Static),
                "You're set up. Ready to upload.",
                "success",
            )
            self._finish_discovery_action()
            if self.current_stage is SetupStage.REMOTE:
                self._enable_remote_next()
                return
            self._save_and_finish()
            return
        if self.current_stage is not SetupStage.REMOTE:
            self.transition_to(SetupStage.REMOTE, preserve_remote=True)
        self._set_remote_header(
            "Your key isn't linked yet",
            "We checked the public places the dashboard looks for this key.",
            "warning",
        )
        self._set_tone(
            self.query_one("#remote-status", Static),
            "Link this key next so the dashboard can verify your uploads.",
            "warning",
        )
        self._finish_discovery_action()
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
                DataTable(id="remote-checks"),
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
                DoneBranch(id="done-branch"),
            )

    def _populate_done_info(self) -> None:
        match self.state.config:
            case GistConfig(gist_id=g):
                self._done_identify_text = (
                    f"How we know it's you: uploads are signed on this Mac, and gist {g[:7]} holds the public key."
                )
            case _:
                self._done_identify_text = (
                    "How we know it's you: uploads are signed on this Mac, and the dashboard checks your public key."
                )
        match EngineFactory.default():
            case "omlx":
                self._done_process_text = (
                    "Where scoring happens: entirely on your Mac with a local Gemma model."
                )
            case "claude":
                self._done_process_text = (
                    "Where scoring happens: through the claude CLI on this Mac, never through the dashboard."
                )
        self._render_done_branch()
        self._finalize_done_screen()

    @work()
    async def _finalize_done_screen(self) -> None:
        transcripts = await anyio.to_thread.run_sync(TranscriptDiscovery.find_transcripts)
        files = len(transcripts)
        rate = Hardware.estimate_buckets_per_sec(EngineFactory.default())
        self._done_eta_text = (
            f"Found {files:,} transcripts. About {TimeFormat.format_duration(files * self.ROUGH_BUCKETS_PER_FILE / rate)} to score here."
            if rate and files else ""
        )
        self._render_done_branch()

    @staticmethod
    def render_sample_payload() -> str:
        return "\n".join(
            (
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
        self._configure_remote_checks_table()
        self.set_interval(1, self._refresh_pending_status)
        self.set_interval(0.1, self._poll_verification_if_due)
        self._render_done_branch()
        self.try_auto_setup()

    def on_unmount(self) -> None:
        self._cancel_remote_check()
        self._cancel_verify_worker()

    def action_activate_primary(self) -> None:
        if button := self._current_primary_button():
            button.press()

    @work()
    async def try_auto_setup(self) -> None:
        if await self._resume_saved_config():
            return
        emit = StatusEmitter(self.query_one("#loading-activity", PendingStatus))
        ok, username = await AutoSetup(self.state, emit).run()
        if ok:
            self._on_auto_setup_success()
            return
        self._on_auto_setup_fail(username)

    async def _resume_saved_config(self) -> bool:
        match self.state.config:
            case SSHConfig(contributor_id=cid, key_path=path):
                if not await anyio.to_thread.run_sync(path.exists):
                    return False
                self.username = cid
                self._done_summary_text = f"Signed in as {cid} using SSH key {path.name}."
                self._verification_action = "github-ssh"
            case GPGConfig(contributor_type=contributor_type, contributor_id=cid, fpr=fpr):
                gpg_keys = await anyio.to_thread.run_sync(KeyDiscovery.find_gpg_keys)
                if not (info := next((key for key in gpg_keys if key.fpr == fpr), None)):
                    return False
                self.username = cid if contributor_type == "github" else ""
                self.selected_key = info
                label = cid if contributor_type == "github" else f"GPG {fpr[-8:]}"
                self._done_summary_text = (
                    f"Signed in as {label} using GPG {self._display_fingerprint(fpr)}."
                )
                self._verification_action = "github-gpg" if contributor_type == "github" else "openpgp"
            case GistConfig(contributor_id=cid, key_path=path, gist_id=gist_id):
                if not await anyio.to_thread.run_sync(path.exists):
                    return False
                self.username = cid
                self._done_summary_text = f"Signed in as {cid} using cc-sentiment gist {gist_id[:7]}."
                self._verification_action = "gist"
            case _:
                return False
        self.verification_poll.restart(monotonic())
        self._verification_detail = ""
        self._set_verification_branch(VerificationState.PENDING)
        self._populate_done_info()
        self.transition_to(SetupStage.DONE)
        self._render_done_branch()
        self.verify_server_config()
        return True

    def _on_auto_setup_success(self) -> None:
        config = self.state.config
        assert config is not None
        match config:
            case SSHConfig(contributor_id=cid, key_path=p):
                self._done_summary_text = f"Signed in as {cid} using SSH key {p.name}."
            case GPGConfig(contributor_type=contributor_type, contributor_id=cid, fpr=f):
                label = cid if contributor_type == "github" else f"GPG {f[-8:]}"
                self._done_summary_text = (
                    f"Signed in as {label} using GPG {self._display_fingerprint(f)}."
                )
            case GistConfig(contributor_id=cid, gist_id=g):
                self._done_summary_text = f"Signed in as {cid} using cc-sentiment gist {g[:7]}."
        self._set_verification_branch(VerificationState.VERIFIED)
        self._populate_done_info()
        self.transition_to(SetupStage.DONE)
        self._render_done_branch()

    def _on_auto_setup_fail(self, username: str | None) -> None:
        if username:
            self.query_one("#username-input", Input).value = username
            self._set_tone(
                self.query_one("#username-status", Static),
                f"Auto-detected: {username}",
            )
            self._username_status_snapshot = f"Auto-detected: {username}"
        else:
            self._username_status_snapshot = ""
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
        self._finish_discovery_action()
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
        self._generation_radio_index = len(all_keys) if self._generation_mode is not None and not all_keys else None

        radio_children = [
            *[
                RadioButton(self._key_radio_label(key))
                for key in all_keys
            ],
            *([RadioButton("Create a new cc-sentiment key")] if self._generation_radio_index is not None else []),
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
            hint = " Pick one." if len(all_keys) > 1 else ""
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
        self._cancel_remote_check()
        self._finish_discovery_action()
        self.query_one("#username-status", Static).update(self._username_status_snapshot)
        self.transition_to(SetupStage.USERNAME)

    @on(RadioSet.Changed, "#key-select")
    def on_discovery_selection_changed(self) -> None:
        self._cancel_remote_check()
        self._finish_discovery_action()
        if list(self.query("#key-select RadioButton")):
            self.query_one("#discovery-next", Button).disabled = False

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

        identity = self.username or "cc-sentiment"
        email = identity + "@users.noreply.github.com"
        batch_input = f"""%no-protection
Key-Type: eddsa
Key-Curve: ed25519
Name-Real: {identity}
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
        self._done_summary_text = (
            f"Signed in as {self.username} using cc-sentiment gist {gist_id[:7]}."
        )
        self.verification_poll.restart(monotonic())
        self._verification_detail = ""
        self._verification_action = "gist"
        self._set_verification_branch(VerificationState.PENDING)
        self._populate_done_info()
        self._finish_discovery_action()
        self.transition_to(SetupStage.DONE)
        self._render_done_branch()
        self.verify_server_config()

    def _go_to_remote(self) -> None:
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
        results: list[RemoteCheckRow] = []
        found = False
        key_on_openpgp = False
        self.app.call_from_thread(self._set_remote_pending, generation, key)

        match key:
            case SSHKeyInfo(path=p):
                try:
                    github_keys = KeyDiscovery.fetch_github_ssh_keys(self.username)
                    local_fp = SSHBackend(private_key_path=p).fingerprint()
                    if any(" ".join(gk.split()[:2]) == local_fp for gk in github_keys):
                        results.append(RemoteCheckRow("✓", "GitHub", "Found on GitHub", "success"))
                        found = True
                    else:
                        results.append(RemoteCheckRow("—", "GitHub", "Not on GitHub yet", "warning"))
                except httpx.HTTPError:
                    results.append(RemoteCheckRow("?", "GitHub", "Couldn't reach GitHub", "muted"))

            case GPGKeyInfo(fpr=f):
                if self.username:
                    try:
                        if KeyDiscovery.gpg_key_on_github(self.username, f):
                            results.append(RemoteCheckRow("✓", "GitHub", "Found on GitHub", "success"))
                            found = True
                        else:
                            results.append(RemoteCheckRow("—", "GitHub", "Not on GitHub yet", "warning"))
                    except httpx.HTTPError:
                        results.append(RemoteCheckRow("?", "GitHub", "Couldn't reach GitHub", "muted"))

                try:
                    openpgp_key = KeyDiscovery.fetch_openpgp_key(f)
                    if openpgp_key:
                        results.append(RemoteCheckRow("✓", "keys.openpgp.org", "Found on keys.openpgp.org", "success"))
                        found = True
                        key_on_openpgp = True
                    else:
                        results.append(RemoteCheckRow("—", "keys.openpgp.org", "Not on keys.openpgp.org yet", "warning"))
                except httpx.HTTPError:
                    results.append(RemoteCheckRow("?", "keys.openpgp.org", "Couldn't reach keys.openpgp.org", "warning"))

        self.app.call_from_thread(
            self._apply_remote_results,
            generation,
            key,
            results,
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
        key = self.selected_key
        gh_authed = shutil.which("gh") is not None and await anyio.to_thread.run_sync(KeyDiscovery.gh_authenticated)
        upload_options = self._build_upload_options(gh_authed, key)
        self._upload_actions = [option.action for option in upload_options]
        radio_buttons = [RadioButton(option.label) for option in upload_options]
        radio.mount_all(radio_buttons)
        if radio_buttons:
            radio_buttons[0].toggle()
        radio.display = len(radio_buttons) > 1
        self.query_one("#upload-go", Button).label = (
            "Show me the key" if self._upload_actions == ["manual"] else "Link my key"
        )

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

    def _build_upload_options(
        self,
        gh_authed: bool,
        key: SSHKeyInfo | GPGKeyInfo | None,
    ) -> list[UploadOption]:
        match key:
            case SSHKeyInfo():
                return [
                    *([UploadOption("github-ssh", "Link via GitHub (gh)")] if gh_authed else []),
                    UploadOption("manual", "Show me the key; I'll add it myself"),
                ]
            case GPGKeyInfo():
                return [
                    *([UploadOption("github-gpg", "Link via GitHub (gh)")] if gh_authed and self.username else []),
                    UploadOption("openpgp", "Publish to keys.openpgp.org"),
                    UploadOption("manual", "Show me the key; I'll add it myself"),
                ]
            case _:
                return []

    def _selected_upload_action(self) -> str:
        radio = self.query_one("#upload-options", RadioSet)
        idx = radio.pressed_index if radio.display and radio.pressed_index >= 0 else 0
        return self._upload_actions[idx]

    @on(Button.Pressed, "#upload-go")
    def on_upload_go(self) -> None:
        if self.actions.upload_running:
            return
        self.actions.upload_running = True
        self.run_upload(self._selected_upload_action())

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
                try:
                    result = subprocess.run(
                        ["gh", "ssh-key", "add", str(pub_path), "-t", "cc-sentiment"],
                        capture_output=True, text=True, timeout=30,
                    )
                except subprocess.SubprocessError as e:
                    self.app.call_from_thread(
                        self._set_tone,
                        result_label,
                        f"Something went wrong: {e}",
                        "error",
                    )
                    self.app.call_from_thread(self._finish_upload_action)
                    return
                if result.returncode == 0:
                    self._verification_action = "github-ssh"
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
                    try:
                        result = subprocess.run(
                            ["gh", "gpg-key", "add", str(tmp_path)],
                            capture_output=True, text=True, timeout=30,
                        )
                    except subprocess.SubprocessError as e:
                        self.app.call_from_thread(
                            self._set_tone,
                            result_label,
                            f"Something went wrong: {e}",
                            "error",
                        )
                        self.app.call_from_thread(self._finish_upload_action)
                        return
                    if result.returncode == 0:
                        self._verification_action = "github-gpg"
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
                    self._verification_action = "openpgp"
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
                url = self._manual_destination_url()
                self._verification_action = "manual"
                self.app.call_from_thread(
                    self._set_tone,
                    result_label,
                    f"Paste your public key at:\n{url}",
                )
                self.app.call_from_thread(self._save_and_finish)

    def _save_and_finish(self) -> None:
        key = self.selected_key
        identity = self.username
        self.verification_poll.restart(monotonic())
        self._verification_detail = ""

        match key:
            case SSHKeyInfo(path=p):
                self.state.config = SSHConfig(contributor_id=ContributorId(identity), key_path=p)
            case GPGKeyInfo(fpr=f):
                if identity:
                    self.state.config = GPGConfig(contributor_type="github", contributor_id=ContributorId(identity), fpr=f)
                else:
                    self.state.config = GPGConfig(contributor_type="gpg", contributor_id=ContributorId(f), fpr=f)

        match key:
            case SSHKeyInfo(path=p):
                self._done_summary_text = f"Signed in as {identity} using SSH key {p.name}."
            case GPGKeyInfo(fpr=f):
                label = identity or f"GPG {f[-8:]}"
                self._done_summary_text = f"Signed in as {label}."

        if not self._verification_action:
            self._verification_action = "manual"
        self._set_verification_branch(VerificationState.PENDING)
        self._populate_done_info()
        self._finish_upload_action()
        self.transition_to(SetupStage.DONE)
        self._render_done_branch()
        self.verify_server_config()

    def verify_server_config(self) -> None:
        if self._verify_worker is not None and self._verify_worker.is_running:
            return
        self._verify_worker = self.run_worker(
            self._verify_server_config(),
            name=f"setup-verify-{monotonic()}",
            exit_on_error=False,
        )

    async def _verify_server_config(self) -> None:
        from cc_sentiment.upload import AuthOk, AuthServerError, AuthUnauthorized, AuthUnreachable, Uploader

        try:
            await anyio.to_thread.run_sync(self.state.save)
            assert self.state.config is not None
            try:
                result = await Uploader().probe_credentials(self.state.config)
            except httpx.HTTPError as error:
                result = AuthUnreachable(detail=str(error))

            match result:
                case AuthOk():
                    self._verification_detail = ""
                    self.verification_poll.clear()
                    self._set_verification_branch(VerificationState.VERIFIED)
                case AuthUnauthorized():
                    self._verification_detail = ""
                    if monotonic() - self.verification_poll.started_at < PENDING_PROPAGATION_WINDOW_SECONDS:
                        self.verification_poll.schedule_next(monotonic())
                        self._set_verification_branch(VerificationState.PENDING)
                    else:
                        self.verification_poll.clear()
                        self._set_verification_branch(VerificationState.FAILED)
                case AuthUnreachable() | AuthServerError():
                    self._verification_detail = "temporarily unreachable"
                    self.verification_poll.schedule_next(monotonic())
                    self._set_verification_branch(VerificationState.PENDING)
        finally:
            self._verify_worker = None

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#pending-retry")
    @on(Button.Pressed, "#failed-retry")
    def on_retry(self) -> None:
        self._cancel_verify_worker()
        self._set_verification_branch(VerificationState.PENDING)
        self.verify_server_config()

    @on(Button.Pressed, "#pending-exit")
    @on(Button.Pressed, "#failed-exit")
    def on_exit(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

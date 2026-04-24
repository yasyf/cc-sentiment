from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import suppress
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
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Input,
    RadioButton,
    RadioSet,
    Static,
)
from textual.worker import Worker

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
from cc_sentiment.tui import setup_state
from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.setup_state import (
    DiscoveryState,
    DoneDisplayState,
    GenerationMode,
    RemoteCheckRow,
    RemoteCheckState,
    RetryTarget,
    SetupActionState,
    SetupStage,
    Tone,
    UploadOption,
    UploadPlanState,
    VerificationAction,
    VerificationPollState,
    VerificationState,
)
from cc_sentiment.tui.status import AutoSetup, StatusEmitter
from cc_sentiment.tui.widgets import (
    DoneBranch,
    KeyPreview,
    PendingStatus,
    StepActions,
    StepBody,
    StepHeader,
)
from cc_sentiment.upload import (
    AuthOk,
    AuthResult,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    Uploader,
)

__all__ = ["SetupScreen", "SetupStage", "VerificationState"]


Config = SSHConfig | GPGConfig | GistConfig


class SetupScreen(Dialog[bool]):
    ROUGH_BUCKETS_PER_FILE: ClassVar[int] = 6
    REMOTE_TONE_STYLES: ClassVar[dict[Tone, str]] = {
        Tone.SUCCESS: "green",
        Tone.WARNING: "yellow",
        Tone.MUTED: "dim",
    }
    DONE_FOCUS_CHAIN: ClassVar[tuple[str, ...]] = (
        "#done-btn",
        "#pending-retry",
        "#failed-retry",
        "#pending-exit",
        "#failed-exit",
    )

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
        self.discovery = DiscoveryState()
        self.remote_check = RemoteCheckState()
        self.upload_plan = UploadPlanState()
        self.done_display = DoneDisplayState()
        self.verification_poll = VerificationPollState(started_at=monotonic())
        self.verify_worker: Worker[None] | None = None

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

    @property
    def transition_history_names(self) -> list[str]:
        return [stage.name.lower() for stage in self.transition_history]

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
        target = stage if isinstance(stage, SetupStage) else SetupStage(stage)
        match target:
            case SetupStage.USERNAME:
                self._focus_widget(self.query_one("#username-input", Input))
            case SetupStage.DISCOVERY:
                self._focus_widget(self.query_one("#discovery-next", Button))
            case SetupStage.REMOTE:
                self._focus_widget(self.query_one("#remote-next", Button))
            case SetupStage.UPLOAD:
                self._focus_widget(self.query_one("#upload-go", Button))
            case SetupStage.DONE:
                for selector in self.DONE_FOCUS_CHAIN:
                    with suppress(NoMatches):
                        self._focus_widget(self.query_one(selector, Button))
                        return

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

    def _set_tone(self, widget: Static, text: str | Text, tone: Tone = Tone.MUTED) -> None:
        for member in Tone:
            widget.remove_class(member.value)
        widget.add_class(tone.value)
        widget.update(text)

    @staticmethod
    def _display_fingerprint(fingerprint: str) -> str:
        if not all(c in "0123456789abcdefABCDEF" for c in fingerprint):
            return fingerprint
        return (
            f"{fingerprint[:4]} {fingerprint[4:8]} ... {fingerprint[-8:-4]} {fingerprint[-4:]}"
            if len(fingerprint) == 40
            else " ".join(fingerprint[i:i + 4] for i in range(0, len(fingerprint), 4))
        )

    @classmethod
    def _config_summary_text(cls, config: Config) -> str:
        match config:
            case SSHConfig(contributor_id=cid, key_path=path):
                return f"Signed in as {cid} using SSH key {path.name}."
            case GPGConfig(contributor_type=contributor_type, contributor_id=cid, fpr=fpr):
                label = cid if contributor_type == "github" else f"GPG {fpr[-8:]}"
                return f"Signed in as {label} using GPG {cls._display_fingerprint(fpr)}."
            case GistConfig(contributor_id=cid, gist_id=gist_id):
                return f"Signed in as {cid} using cc-sentiment gist {gist_id[:7]}."

    def _key_radio_label(self, key: SSHKeyInfo | GPGKeyInfo) -> Text:
        match key:
            case SSHKeyInfo(path=path, algorithm=algorithm):
                return Text(f"SSH · {path.name} · {algorithm}")
            case GPGKeyInfo(fpr=fingerprint, email=email):
                return Text(f"GPG · {self._display_fingerprint(fingerprint)} · {email}")

    def _clear_radio_set(self, radio: RadioSet) -> None:
        radio._pressed_button = None
        radio.display = False
        radio.remove_children()

    def _step_header(self, stage: SetupStage) -> StepHeader:
        return self.query_one(f"#{stage.value}", Vertical).query_one(StepHeader)

    def _reset_discovery_stage(self) -> None:
        radio = self.query_one("#key-select", RadioSet)
        self.discovery.reset()
        self.selected_key = None
        self._clear_radio_set(radio)
        self._set_tone(
            self.query_one("#discovery-status", Static),
            "Looking for signing keys on your machine...",
        )
        self.query_one("#discovery-help", Static).update("")
        self.query_one("#discovery-next", Button).disabled = True

    def _reset_remote_stage(self) -> None:
        self.remote_check.reset()
        self._step_header(SetupStage.REMOTE).set_content(
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
        self.upload_plan.reset()
        self._clear_radio_set(radio)
        self.query_one("#upload-key-text", KeyPreview).text = ""
        self._set_tone(self.query_one("#upload-result", Static), "")
        go_button = self.query_one("#upload-go", Button)
        go_button.label = "Link my key"
        go_button.disabled = True

    def _reset_done_stage(self) -> None:
        self.done_display.reset()
        self.verification_poll.restart(monotonic())
        self._cancel_verify_worker()
        self._render_done_branch()

    @staticmethod
    def _format_pending_elapsed(seconds: float) -> str:
        elapsed = max(0, int(seconds))
        return f"{elapsed // 60}:{elapsed % 60:02d}"

    def _pending_label(self) -> str:
        elapsed = self._format_pending_elapsed(monotonic() - self.verification_poll.started_at)
        return f"Waiting for your key to propagate… {elapsed}"

    def _manual_destination_url(self) -> str:
        return (
            "https://github.com/settings/ssh/new"
            if isinstance(self.selected_key, SSHKeyInfo)
            else "https://github.com/settings/gpg/new"
        )

    def _instructions_text(self) -> str:
        failure_prefix = (
            f"{self.done_display.upload_failure_text}\n\n"
            if self.verification_state is VerificationState.FAILED
            and self.done_display.upload_failure_text
            else ""
        )
        unreachable_prefix = (
            "sentiments.cc is temporarily unreachable right now. "
            if self.done_display.verification_detail == "temporarily unreachable"
            else ""
        )
        match self.done_display.verification_action:
            case VerificationAction.MANUAL:
                suffix = (
                    f"Paste your public key at {self._manual_destination_url()}, "
                    "then retry once GitHub shows it."
                )
            case VerificationAction.OPENPGP:
                suffix = (
                    "Check your email for the keys.openpgp.org verification link, "
                    "finish publishing the key, then retry."
                )
            case VerificationAction.GITHUB_SSH | VerificationAction.GITHUB_GPG:
                suffix = "Give GitHub a moment to propagate your public key, then retry."
            case VerificationAction.GIST:
                suffix = (
                    "Keep your cc-sentiment gist public so the dashboard can read the key, "
                    "then retry."
                )
            case _:
                suffix = "Wait a moment for your public key to propagate, then retry."
        return failure_prefix + unreachable_prefix + suffix

    def _done_branch(self) -> DoneBranch:
        return self.query_one("#done-branch", DoneBranch)

    def _visible_verification_state(self) -> VerificationState:
        return (
            self.verification_state
            if self.verification_ok or self.verification_state is not VerificationState.VERIFIED
            else VerificationState.PENDING
        )

    def _render_done_branch(self) -> None:
        with suppress(NoMatches):
            match self._visible_verification_state():
                case VerificationState.VERIFIED:
                    self._step_header(SetupStage.DONE).set_content(
                        "You're all set",
                        "Review how uploads are signed and what the dashboard receives.",
                        Tone.SUCCESS,
                    )
                case VerificationState.PENDING:
                    self._step_header(SetupStage.DONE).set_content(
                        "Waiting for your key",
                        "The dashboard can see your setup, but it can't verify this key yet.",
                        Tone.WARNING,
                    )
                case VerificationState.FAILED:
                    self._step_header(SetupStage.DONE).set_content(
                        "We couldn't verify your key",
                        "The dashboard still can't read this public key. "
                        "Check the instructions, then retry.",
                        Tone.ERROR,
                    )
        with suppress(NoMatches):
            branch = self._done_branch()
            branch.summary_text = self.done_display.summary_text
            branch.identify_text = self.done_display.identify_text
            branch.process_text = self.done_display.process_text
            branch.eta_text = self.done_display.eta_text
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
            self.done_display.verification_detail = detail
        self.verification_state = state
        self.verification_ok = state is VerificationState.VERIFIED
        self._render_done_branch()

    def _enter_done(
        self,
        state: VerificationState,
        action: VerificationAction | None = None,
        *,
        verify: bool = False,
        restart_poll: bool = True,
    ) -> None:
        if restart_poll:
            self.verification_poll.restart(monotonic())
        self.done_display.verification_detail = ""
        if action is not None:
            self.done_display.verification_action = action
        self._set_verification_branch(state)
        self._populate_done_info()
        self.transition_to(SetupStage.DONE)
        self._render_done_branch()
        if verify:
            self.verify_server_config()

    def _on_verify_result(self, result: AuthResult) -> None:
        self.done_display.clear_failure()
        match result:
            case AuthOk():
                self.verification_poll.clear()
                self._set_verification_branch(VerificationState.VERIFIED)
            case AuthUnauthorized():
                if monotonic() - self.verification_poll.started_at < setup_state.PENDING_PROPAGATION_WINDOW_SECONDS:
                    self.verification_poll.schedule_next(monotonic())
                    self._set_verification_branch(VerificationState.PENDING)
                else:
                    self.verification_poll.clear()
                    self._set_verification_branch(VerificationState.FAILED)
            case AuthUnreachable() | AuthServerError():
                self.done_display.verification_detail = "temporarily unreachable"
                self.verification_poll.schedule_next(monotonic())
                self._set_verification_branch(VerificationState.PENDING)

    def _refresh_pending_status(self) -> None:
        if self._visible_verification_state() is not VerificationState.PENDING:
            return
        with suppress(NoMatches):
            self._done_branch().pending_label = self._pending_label()

    def _cancel_verify_worker(self) -> None:
        if self.verify_worker is not None:
            self.verify_worker.cancel()
        self.verify_worker = None

    def _poll_verification_if_due(self) -> None:
        if self.current_stage is not SetupStage.DONE:
            return
        if self._visible_verification_state() is not VerificationState.PENDING:
            return
        if self.verify_worker is not None and self.verify_worker.is_running:
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
        self.remote_check.cancel()

    def _remote_check_is_current(
        self,
        generation: int,
        key: SSHKeyInfo | GPGKeyInfo | None,
    ) -> bool:
        return generation == self.remote_check.generation and key == self.selected_key

    def _configure_remote_checks_table(self) -> DataTable:
        table = self.query_one("#remote-checks", DataTable)
        if not table.ordered_columns:
            table.add_columns("glyph", "check", "detail")
        return table

    def _render_remote_checks(self, rows: list[RemoteCheckRow]) -> None:
        table = self._configure_remote_checks_table()
        table.clear(columns=False)
        for row in rows:
            style = self.REMOTE_TONE_STYLES.get(row.tone, "")
            table.add_row(*(Text(value, style=style) for value in (row.glyph, row.check, row.detail)))

    def _set_remote_pending(self, generation: int, key: SSHKeyInfo | GPGKeyInfo | None) -> None:
        if not self._remote_check_is_current(generation, key):
            return
        if self.current_stage is SetupStage.REMOTE:
            self._step_header(SetupStage.REMOTE).set_content(
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
        self.remote_check.worker = None
        self.remote_check.key_on_openpgp = key_on_openpgp
        self._render_remote_checks(results)
        self.remote_check.key_on_remote = found
        self.actions.discovery_action_running = False
        if found:
            self._step_header(SetupStage.REMOTE).set_content(
                "Your key is ready",
                "We found this public key somewhere the dashboard can already read.",
                Tone.SUCCESS,
            )
            self._set_tone(
                self.query_one("#remote-status", Static),
                "You're set up. Ready to upload.",
                Tone.SUCCESS,
            )
            if self.current_stage is SetupStage.REMOTE:
                self._enable_remote_next()
                return
            self._save_and_finish()
            return
        if self.current_stage is not SetupStage.REMOTE:
            self.transition_to(SetupStage.REMOTE, preserve_remote=True)
        self._step_header(SetupStage.REMOTE).set_content(
            "Your key isn't linked yet",
            "We checked the public places the dashboard looks for this key.",
            Tone.WARNING,
        )
        self._set_tone(
            self.query_one("#remote-status", Static),
            "Link this key next so the dashboard can verify your uploads.",
            Tone.WARNING,
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
                DoneBranch(self.render_sample_payload, id="done-branch"),
            )

    def _populate_done_info(self) -> None:
        match self.state.config:
            case GistConfig(gist_id=g):
                self.done_display.identify_text = (
                    f"How we know it's you: uploads are signed on this Mac, "
                    f"and gist {g[:7]} holds the public key."
                )
            case _:
                self.done_display.identify_text = (
                    "How we know it's you: uploads are signed on this Mac, "
                    "and the dashboard checks your public key."
                )
        match EngineFactory.default():
            case "mlx":
                self.done_display.process_text = (
                    "Where scoring happens: entirely on your Mac with a local Gemma model."
                )
            case "claude":
                self.done_display.process_text = (
                    "Where scoring happens: through the claude CLI on this Mac, "
                    "never through the dashboard."
                )
        self._render_done_branch()
        self._finalize_done_screen()

    @work()
    async def _finalize_done_screen(self) -> None:
        transcripts = await anyio.to_thread.run_sync(TranscriptDiscovery.find_transcripts)
        files = len(transcripts)
        rate = Hardware.estimate_buckets_per_sec(EngineFactory.default())
        self.done_display.eta_text = (
            f"Found {files:,} transcripts. "
            f"About {TimeFormat.format_duration(files * self.ROUGH_BUCKETS_PER_FILE / rate)} to score here."
            if rate and files else ""
        )
        self._render_done_branch()

    @staticmethod
    def render_sample_payload() -> str:
        return "\n".join((
            "{",
            '  "time": "2026-04-15T14:23:05Z",',
            '  "conversation_id": "7f3a9b2c-0e4d-4a91-b6f8",',
            '  "sentiment_score": 4,',
            '  "claude_model": "claude-haiku-4-5",',
            '  "turn_count": 14,',
            '  "read_edit_ratio": 0.71',
            "}",
        ))

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
        self._persist_transition_history()

    def _persist_transition_history(self) -> None:
        if "CC_SENTIMENT_TRANSITION_HISTORY_PATH" not in os.environ:
            return
        Path(os.environ["CC_SENTIMENT_TRANSITION_HISTORY_PATH"]).write_text(
            json.dumps(self.transition_history_names)
        )

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
            case SSHConfig(contributor_id=cid, key_path=path) as config:
                if not await anyio.to_thread.run_sync(path.exists):
                    return False
                self.username = cid
                self.done_display.summary_text = self._config_summary_text(config)
                self.done_display.verification_action = VerificationAction.GITHUB_SSH
            case GPGConfig(contributor_type=contributor_type, contributor_id=cid, fpr=fpr) as config:
                gpg_keys = await anyio.to_thread.run_sync(KeyDiscovery.find_gpg_keys)
                if not (info := next((key for key in gpg_keys if key.fpr == fpr), None)):
                    return False
                self.username = cid if contributor_type == "github" else ""
                self.selected_key = info
                self.done_display.summary_text = self._config_summary_text(config)
                self.done_display.verification_action = (
                    VerificationAction.GITHUB_GPG if contributor_type == "github" else VerificationAction.OPENPGP
                )
            case GistConfig(contributor_id=cid, key_path=path) as config:
                if not await anyio.to_thread.run_sync(path.exists):
                    return False
                self.username = cid
                self.done_display.summary_text = self._config_summary_text(config)
                self.done_display.verification_action = VerificationAction.GIST
            case _:
                return False
        self._enter_done(VerificationState.PENDING, verify=True)
        return True

    def _on_auto_setup_success(self) -> None:
        config = self.state.config
        assert config is not None
        self.done_display.summary_text = self._config_summary_text(config)
        self._enter_done(VerificationState.VERIFIED, restart_poll=False)

    def _on_auto_setup_fail(self, username: str | None) -> None:
        if username:
            self.query_one("#username-input", Input).value = username
            self._set_tone(
                self.query_one("#username-status", Static),
                f"Auto-detected: {username}",
            )
            self.discovery.username_status_snapshot = f"Auto-detected: {username}"
        else:
            self.discovery.username_status_snapshot = ""
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
                Tone.ERROR,
            )
            return
        self.discovery.username_status_snapshot = str(self.query_one("#username-status", Static).render())
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
        call = self.app.call_from_thread

        def finish_with_error(message: str) -> None:
            call(self._set_tone, status, message, Tone.ERROR)
            self.actions.username_validation_running = False

        call(self._set_tone, status, f"Validating {self.username}...")
        try:
            response = httpx.get(f"https://api.github.com/users/{self.username}", timeout=10.0)
        except httpx.HTTPError:
            finish_with_error("Could not reach GitHub API")
            return
        if response.status_code != 200:
            finish_with_error(f"GitHub user '{self.username}' not found")
            return
        call(self._switch_to_discovery)
        self.actions.username_validation_running = False

    def _switch_to_discovery(self) -> None:
        self.actions.discovery_action_running = False
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
        self.discovery.discovered_keys = all_keys
        self.discovery.generation_mode = self._pick_generation_mode()
        self.discovery.generation_radio_index = (
            len(all_keys)
            if self.discovery.generation_mode is not None and not all_keys
            else None
        )

        radio_children = [
            *(RadioButton(self._key_radio_label(key)) for key in all_keys),
            *(
                [RadioButton("Create a new cc-sentiment key")]
                if self.discovery.generation_radio_index is not None
                else []
            ),
        ]

        if not radio_children:
            self._set_tone(status, "No signing keys found on your machine.")
            help_text.update(
                "Go back and enter a GitHub username, or install gpg "
                "(brew install gnupg) to use GPG."
                if not self.username
                else "To create a signing key for cc-sentiment, install the GitHub CLI "
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

    def _pick_generation_mode(self) -> GenerationMode | None:
        if self.username and KeyDiscovery.gh_authenticated():
            return GenerationMode.GIST
        if self.username and KeyDiscovery.has_tool("ssh-keygen"):
            return GenerationMode.SSH
        if KeyDiscovery.has_tool("gpg"):
            return GenerationMode.GPG
        return None

    def _generation_prompt(self) -> str:
        match self.discovery.generation_mode:
            case GenerationMode.GIST:
                return "We can make a small signing key for cc-sentiment and save its public key in a gist."
            case GenerationMode.SSH:
                return "We'll create a small SSH key here, then help you add its public key to GitHub."
            case GenerationMode.GPG:
                return "No problem. We'll create a GPG key for you here."
            case _:
                return ""

    @on(Button.Pressed, "#discovery-back")
    def on_discovery_back(self) -> None:
        self._cancel_remote_check()
        self.actions.discovery_action_running = False
        self.query_one("#username-status", Static).update(self.discovery.username_status_snapshot)
        self.transition_to(SetupStage.USERNAME)

    @on(RadioSet.Changed, "#key-select")
    def on_discovery_selection_changed(self) -> None:
        self._cancel_remote_check()
        self.actions.discovery_action_running = False
        if list(self.query("#key-select RadioButton")):
            self.query_one("#discovery-next", Button).disabled = False

    @on(Button.Pressed, "#discovery-next")
    def on_discovery_next(self) -> None:
        if self.actions.discovery_action_running:
            return
        if not self.discovery.discovered_keys:
            self.actions.discovery_action_running = True
            self._dispatch_generation()
            return
        radio = self.query_one("#key-select", RadioSet)
        idx = radio.pressed_index if radio.pressed_index >= 0 else 0
        if self.discovery.generation_radio_index is not None and idx == self.discovery.generation_radio_index:
            self.actions.discovery_action_running = True
            self._dispatch_generation()
            return
        self.actions.discovery_action_running = True
        self.selected_key = self.discovery.discovered_keys[idx]
        self._go_to_remote()

    def _dispatch_generation(self) -> None:
        match self.discovery.generation_mode:
            case GenerationMode.GIST:
                self.generate_gist_key()
            case GenerationMode.SSH:
                self.generate_managed_ssh_key()
            case GenerationMode.GPG:
                self.generate_gpg_key()

    @work(thread=True)
    def generate_gpg_key(self) -> None:
        status = self.query_one("#discovery-status", Static)
        call = self.app.call_from_thread
        call(self._set_tone, status, "Creating a signing key for you...")

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
            call(self._set_tone, status, f"Key generation failed: {result.stderr.strip()}", Tone.ERROR)
            self.actions.discovery_action_running = False
            return

        new_key = next((k for k in KeyDiscovery.find_gpg_keys() if k.email == email), None)
        if not new_key:
            call(self._set_tone, status, "Key generated but not found in keyring", Tone.ERROR)
            self.actions.discovery_action_running = False
            return

        self.selected_key = new_key
        call(self._set_tone, status, f"Generated key: {self._display_fingerprint(new_key.fpr)}", Tone.SUCCESS)
        call(self._go_to_remote)

    @work(thread=True)
    def generate_managed_ssh_key(self) -> None:
        status = self.query_one("#discovery-status", Static)
        call = self.app.call_from_thread
        call(self._set_tone, status, "Creating a local signing key for cc-sentiment...")
        try:
            key_path = KeyDiscovery.generate_gist_keypair()
        except subprocess.CalledProcessError as e:
            err = (e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr or str(e)).strip()
            call(self._set_tone, status, f"Couldn't create the key: {err}", Tone.ERROR)
            self.actions.discovery_action_running = False
            return
        parts = key_path.with_suffix(key_path.suffix + ".pub").read_text().strip().split()
        self.selected_key = SSHKeyInfo(
            path=key_path,
            algorithm=parts[0] if len(parts) >= 2 else "unknown",
            comment=parts[2] if len(parts) >= 3 else "",
        )
        call(self._set_tone, status, "Created a local key. Let's link it next.", Tone.SUCCESS)
        call(self._go_to_remote)

    @work(thread=True)
    def generate_gist_key(self) -> None:
        status = self.query_one("#discovery-status", Static)
        call = self.app.call_from_thread
        call(self._set_tone, status, "Creating a signing key and saving its public key as a gist...")
        try:
            key_path = KeyDiscovery.generate_gist_keypair()
            gist_id = KeyDiscovery.create_gist(key_path)
        except subprocess.CalledProcessError as e:
            err = (e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr or str(e)).strip()
            call(self._set_tone, status, f"Couldn't create the gist: {err}", Tone.ERROR)
            self.actions.discovery_action_running = False
            return
        self.state.config = GistConfig(
            contributor_id=ContributorId(self.username),
            key_path=key_path,
            gist_id=gist_id,
        )
        call(self._set_tone, status, f"Saved key to gist {gist_id[:7]}", Tone.SUCCESS)
        call(self._finish_gist, gist_id)

    def _finish_gist(self, gist_id: str) -> None:
        self.done_display.summary_text = (
            f"Signed in as {self.username} using cc-sentiment gist {gist_id[:7]}."
        )
        self.actions.discovery_action_running = False
        self._enter_done(VerificationState.PENDING, action=VerificationAction.GIST, verify=True)

    def _go_to_remote(self) -> None:
        self.remote_check.generation += 1
        self.remote_check.worker = self.check_remotes(self.remote_check.generation, self.selected_key)

    @work(thread=True)
    def check_remotes(
        self,
        generation: int | None = None,
        key: SSHKeyInfo | GPGKeyInfo | None = None,
    ) -> None:
        generation = self.remote_check.generation if generation is None else generation
        key = self.selected_key if key is None else key
        results: list[RemoteCheckRow] = []
        found = False
        key_on_openpgp = False
        self.app.call_from_thread(self._set_remote_pending, generation, key)

        match key:
            case SSHKeyInfo(path=p):
                try:
                    github_keys = KeyDiscovery.fetch_github_ssh_keys(self.username)
                except httpx.HTTPError:
                    results.append(RemoteCheckRow("?", "GitHub", "Couldn't reach GitHub", Tone.MUTED))
                else:
                    local_fp = SSHBackend(private_key_path=p).fingerprint()
                    if any(" ".join(gk.split()[:2]) == local_fp for gk in github_keys):
                        results.append(RemoteCheckRow("✓", "GitHub", "Found on GitHub", Tone.SUCCESS))
                        found = True
                    else:
                        results.append(RemoteCheckRow("—", "GitHub", "Not on GitHub yet", Tone.WARNING))

            case GPGKeyInfo(fpr=f):
                if self.username:
                    try:
                        on_github = KeyDiscovery.gpg_key_on_github(self.username, f)
                    except httpx.HTTPError:
                        results.append(RemoteCheckRow("?", "GitHub", "Couldn't reach GitHub", Tone.MUTED))
                    else:
                        if on_github:
                            results.append(RemoteCheckRow("✓", "GitHub", "Found on GitHub", Tone.SUCCESS))
                            found = True
                        else:
                            results.append(RemoteCheckRow("—", "GitHub", "Not on GitHub yet", Tone.WARNING))

                try:
                    armored = KeyDiscovery.fetch_openpgp_key(f)
                except httpx.HTTPError:
                    results.append(
                        RemoteCheckRow("?", "keys.openpgp.org", "Couldn't reach keys.openpgp.org", Tone.WARNING)
                    )
                else:
                    if armored:
                        results.append(
                            RemoteCheckRow("✓", "keys.openpgp.org", "Found on keys.openpgp.org", Tone.SUCCESS)
                        )
                        found = True
                        key_on_openpgp = True
                    else:
                        results.append(
                            RemoteCheckRow("—", "keys.openpgp.org", "Not on keys.openpgp.org yet", Tone.WARNING)
                        )

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
            if self.remote_check.key_on_remote:
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
        gh_authed = (
            shutil.which("gh") is not None
            and await anyio.to_thread.run_sync(KeyDiscovery.gh_authenticated)
        )
        upload_options = self._build_upload_options(gh_authed, key)
        self.upload_plan.actions = [option.action for option in upload_options]
        radio_buttons = [RadioButton(option.label) for option in upload_options]
        radio.mount_all(radio_buttons)
        if radio_buttons:
            radio_buttons[0].toggle()
        radio.display = len(radio_buttons) > 1
        self.query_one("#upload-go", Button).label = (
            "Show me the key" if self.upload_plan.actions == [VerificationAction.MANUAL] else "Link my key"
        )

        pub_text = ""
        match key:
            case SSHKeyInfo(path=p):
                pub_text = await anyio.to_thread.run_sync(SSHBackend(private_key_path=p).public_key_text)
            case GPGKeyInfo(fpr=f):
                pub_text = await anyio.to_thread.run_sync(GPGBackend(fpr=f).public_key_text)

        self.query_one("#upload-key-text", KeyPreview).text = pub_text
        self._sync_upload_preview_height()
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
                    *([UploadOption(VerificationAction.GITHUB_SSH, "Link via GitHub (gh)")] if gh_authed else []),
                    UploadOption(VerificationAction.MANUAL, "Show me the key; I'll add it myself"),
                ]
            case GPGKeyInfo():
                return [
                    *(
                        [UploadOption(VerificationAction.GITHUB_GPG, "Link via GitHub (gh)")]
                        if gh_authed and self.username
                        else []
                    ),
                    UploadOption(VerificationAction.OPENPGP, "Publish to keys.openpgp.org"),
                    UploadOption(VerificationAction.MANUAL, "Show me the key; I'll add it myself"),
                ]
            case _:
                return []

    def _selected_upload_action(self) -> VerificationAction:
        radio = self.query_one("#upload-options", RadioSet)
        idx = radio.pressed_index if radio.display and radio.pressed_index >= 0 else 0
        return self.upload_plan.actions[idx]

    def _sync_upload_preview_height(self) -> None:
        self.query_one("#upload-key-text", KeyPreview).styles.max_height = (
            5 if self._selected_upload_action() is VerificationAction.MANUAL else 4
        )

    @on(RadioSet.Changed, "#upload-options")
    def on_upload_option_changed(self) -> None:
        self._sync_upload_preview_height()

    def _apply_selected_config(self) -> None:
        identity = self.username
        match self.selected_key:
            case SSHKeyInfo(path=path):
                self.state.config = SSHConfig(contributor_id=ContributorId(identity), key_path=path)
                self.done_display.summary_text = f"Signed in as {identity} using SSH key {path.name}."
            case GPGKeyInfo(fpr=fpr):
                self.state.config = (
                    GPGConfig(contributor_type="github", contributor_id=ContributorId(identity), fpr=fpr)
                    if identity
                    else GPGConfig(contributor_type="gpg", contributor_id=ContributorId(fpr), fpr=fpr)
                )
                label = identity or f"GPG {fpr[-8:]}"
                self.done_display.summary_text = f"Signed in as {label}."
            case _:
                raise AssertionError("selected key required")

    def _save_and_fail_upload(self, action: VerificationAction, message: str) -> None:
        self._apply_selected_config()
        self.state.save()
        self.verification_poll.clear()
        self.done_display.upload_failure_text = message
        self.done_display.failed_retry_target = RetryTarget.UPLOAD
        self.actions.upload_running = False
        self._enter_done(VerificationState.FAILED, action=action, restart_poll=False)

    @on(Button.Pressed, "#upload-go")
    def on_upload_go(self) -> None:
        if self.actions.upload_running:
            return
        self.actions.upload_running = True
        self.run_upload(self._selected_upload_action())

    @on(Button.Pressed, "#upload-back")
    def on_upload_back(self) -> None:
        self.transition_to(SetupStage.REMOTE)

    def _run_gh_link(
        self,
        action: VerificationAction,
        command: list[str],
        pub_path: Path,
        cleanup: bool,
    ) -> None:
        result_label = self.query_one("#upload-result", Static)
        call = self.app.call_from_thread
        try:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            except subprocess.SubprocessError as e:
                call(self._save_and_fail_upload, action, f"Something went wrong: {e}")
                return
            if result.returncode == 0:
                self.done_display.verification_action = action
                call(self._set_tone, result_label, "Key linked to GitHub. You're all set.", Tone.SUCCESS)
                call(self._save_and_finish)
            else:
                call(
                    self._save_and_fail_upload,
                    action,
                    f"Something went wrong: {result.stderr.strip()}",
                )
        finally:
            if cleanup:
                pub_path.unlink(missing_ok=True)

    @work(thread=True)
    def run_upload(self, action: VerificationAction) -> None:
        result_label = self.query_one("#upload-result", Static)
        key = self.selected_key
        call = self.app.call_from_thread

        match action:
            case VerificationAction.GITHUB_SSH:
                assert isinstance(key, SSHKeyInfo)
                pub_path = key.path.with_suffix(key.path.suffix + ".pub")
                self._run_gh_link(
                    VerificationAction.GITHUB_SSH,
                    ["gh", "ssh-key", "add", str(pub_path), "-t", "cc-sentiment"],
                    pub_path,
                    cleanup=False,
                )

            case VerificationAction.GITHUB_GPG:
                assert isinstance(key, GPGKeyInfo)
                pub_text = GPGBackend(fpr=key.fpr).public_key_text()
                with tempfile.NamedTemporaryFile(mode="w", suffix=".asc", delete=False) as f:
                    f.write(pub_text)
                    tmp_path = Path(f.name)
                self._run_gh_link(
                    VerificationAction.GITHUB_GPG,
                    ["gh", "gpg-key", "add", str(tmp_path)],
                    tmp_path,
                    cleanup=True,
                )

            case VerificationAction.OPENPGP:
                assert isinstance(key, GPGKeyInfo)
                call(self._set_tone, result_label, "Publishing to keys.openpgp.org...")
                try:
                    pub_text = GPGBackend(fpr=key.fpr).public_key_text()
                    token, statuses = KeyDiscovery.upload_openpgp_key(pub_text)
                    emails = [e for e, s in statuses.items() if s == "unpublished"]
                    if emails:
                        KeyDiscovery.request_openpgp_verify(token, emails)
                        call(
                            self._set_tone,
                            result_label,
                            f"Almost done. Check your email ({', '.join(emails)}) "
                            "for a verification link.",
                            Tone.WARNING,
                        )
                    else:
                        call(
                            self._set_tone,
                            result_label,
                            "Key already published. You're all set.",
                            Tone.SUCCESS,
                        )
                    self.done_display.verification_action = VerificationAction.OPENPGP
                    call(self._save_and_finish)
                except httpx.HTTPError as e:
                    call(self._set_tone, result_label, f"Couldn't reach keys.openpgp.org: {e}", Tone.ERROR)
                    self.actions.upload_running = False

            case VerificationAction.MANUAL:
                assert key is not None
                self.done_display.verification_action = VerificationAction.MANUAL
                call(
                    self._set_tone,
                    result_label,
                    f"Paste your public key at:\n{self._manual_destination_url()}",
                )
                call(self._save_and_finish)

    def _save_and_finish(self) -> None:
        self.done_display.upload_failure_text = ""
        self.done_display.failed_retry_target = None
        self._apply_selected_config()
        action = self.done_display.verification_action or VerificationAction.MANUAL
        self.actions.upload_running = False
        self._enter_done(VerificationState.PENDING, action=action, verify=True)

    def verify_server_config(self) -> None:
        if self.verify_worker is not None and self.verify_worker.is_running:
            return
        self.verify_worker = self.run_worker(
            self._verify_server_config(),
            name=f"setup-verify-{monotonic()}",
            exit_on_error=False,
        )

    async def _verify_server_config(self) -> None:
        try:
            await anyio.to_thread.run_sync(self.state.save)
            assert self.state.config is not None
            try:
                result = await Uploader().probe_credentials(self.state.config)
            except httpx.HTTPError as error:
                result = AuthUnreachable(detail=str(error))
            self._on_verify_result(result)
        finally:
            self.verify_worker = None

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.dismiss(True)

    def _retry_verification(self) -> None:
        self._cancel_verify_worker()
        self._set_verification_branch(VerificationState.PENDING)
        self.verify_server_config()

    @on(Button.Pressed, "#pending-retry")
    def on_pending_retry(self) -> None:
        self._retry_verification()

    @on(Button.Pressed, "#failed-retry")
    async def on_failed_retry(self) -> None:
        if self.done_display.failed_retry_target is RetryTarget.UPLOAD:
            self.transition_to(SetupStage.UPLOAD)
            await self._populate_upload_options()
            return
        self._retry_verification()

    @on(Button.Pressed, "#pending-exit")
    @on(Button.Pressed, "#failed-exit")
    def on_exit(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

from __future__ import annotations

import subprocess
import sys
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import ClassVar, Literal

import anyio
import anyio.to_thread
import httpx
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
    RadioButton,
    RadioSet,
    Static,
)
from textual.worker import Worker

from cc_sentiment.models import (
    AppState,
    ContributorId,
    GistGPGConfig,
    GistConfig,
    GPGConfig,
    PendingSetupModel,
    SSHConfig,
)
from cc_sentiment.signing import (
    GPGBackend,
    GPGKeyInfo,
    KeyDiscovery,
    SSHBackend,
    SSHKeyInfo,
)
from cc_sentiment.signing.discovery import GIST_DESCRIPTION, GIST_README_TEMPLATE
from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.setup_helpers import (
    GIST_NEW_URL,
    GITHUB_GPG_NEW_URL,
    GITHUB_SSH_NEW_URL,
    OPENPGP_UPLOAD_URL,
    Browser,
    Clipboard,
    DiscoveryRunner,
    GistDiscovery,
    GistRef,
    IdentityProbe,
    IssueUrl,
    Sanitizer,
    SetupRoutePlanner,
)
from cc_sentiment.tui.setup_state import (
    PENDING_PROPAGATION_WINDOW_SECONDS,
    DiscoverRow,
    DiscoverRowState,
    DiscoveryResult,
    ExistingGPGKey,
    ExistingSSHKey,
    GenerateGPGKey,
    GenerateSSHKey,
    IdentityDiscovery,
    KeyKind,
    PendingSetup,
    PendingSetupStatus,
    PublishMethod,
    ResolvedGPGKey,
    ResolvedKey,
    ResolvedSSHKey,
    RouteId,
    SetupActionState,
    SetupAggregate,
    SetupRoute,
    SetupStage,
    Tone,
    UsernameSource,
    VerificationPollState,
    WorkStep,
    WorkStepState,
)
from cc_sentiment.tui.widgets import (
    DoneBranch,
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

__all__ = ["SetupScreen", "SetupStage"]

Config = SSHConfig | GPGConfig | GistConfig | GistGPGConfig


PUBLIC_LOCATION_LABEL: dict[PublishMethod, str] = {
    PublishMethod.GIST_AUTO: "GitHub gist",
    PublishMethod.GIST_MANUAL: "GitHub gist",
    PublishMethod.GITHUB_SSH: "GitHub SSH keys",
    PublishMethod.GITHUB_GPG: "GitHub GPG keys",
    PublishMethod.OPENPGP: "keys.openpgp.org",
}

DISCOVER_TITLE = "Setting up private signing"
DISCOVER_BODY = (
    "cc-sentiment signs uploads so sentiments.cc can verify they came from this device. "
    "The private key stays on this device. Only aggregate sentiment metrics are uploaded to sentiments.cc. "
    "Conversation text, file paths, prompts, tool inputs, and tool outputs are not uploaded. "
    "GitHub/GPG details are used only to find a public key and verify signatures."
)
DISCOVER_VERIFIED_COPY = "Found a working public key. No setup needed."
DISCOVER_NO_MATCH_COPY = (
    "No public key matched this device yet. We'll suggest the safest way to create or publish one."
)
SAVED_KEY_INVALID_COPY_1 = "The saved public key could not be verified anymore."
SAVED_KEY_INVALID_COPY_2 = "We'll help publish a new public key or choose another local key."
SAVED_KEY_TEMPORARY_COPY = (
    "sentiments.cc is having trouble. We'll keep your setup and you can retry later."
)

USERNAME_TITLE = "GitHub username"
USERNAME_BODY = (
    "Optional, but useful for GitHub gist verification. "
    "It is used only to find a public key; stats stay aggregate."
)
USERNAME_PLACEHOLDER = "yasyf"
USERNAME_ERROR_EMPTY = "Enter a GitHub username, or choose GPG only."
USERNAME_ERROR_NOT_FOUND = "GitHub user \u201c{user}\u201d wasn't found."
USERNAME_ERROR_UNREACHABLE = "Couldn't reach GitHub. Retry, or continue with GPG only."

PROPOSE_TITLE = "Recommended setup"
PROPOSE_BODY = (
    "We only need a public key that sentiments.cc can read. "
    "The private key stays on this device, and only aggregate sentiment metrics are uploaded."
)

OPENPGP_EMAIL_LABEL = "Verification email"
OPENPGP_EMAIL_HELP = "keys.openpgp.org sends a one-time email before publishing your public GPG key."
OPENPGP_EMAIL_INFERRED = "Found {email} from a public commit. Use it only if you can open that inbox."
OPENPGP_EMAIL_ERROR_EMPTY = "Use an email address you can open now."

WORKING_TITLE = "Finishing setup"
WORKING_BODY = "This may take a few seconds. We're publishing only the public key."
WORKING_RECOVERABLE_FAILURE = "That automatic step didn't finish: {error}. We can switch to guided setup."

GUIDE_TITLE = "Finish publishing the public key"
GUIDE_BODY = (
    "Complete the steps in your browser, then come back. "
    "cc-sentiment will keep checking automatically."
)

MANUAL_GIST_INTRO = (
    "GitHub does not reliably prefill new gists in every browser. "
    "We copied the public key to your clipboard and listed the exact fields below."
)
MANUAL_GIST_STEPS = (
    "1. Create a public gist.",
    "2. Description: cc-sentiment public key",
    "3. File name: cc-sentiment.pub",
    "4. Paste the public key from your clipboard.",
    "5. Add a second file named README.md with the cc-sentiment note below.",
    "6. Click Create public gist.",
    "7. Come back here. We'll look for the gist and verify it.",
)
MANUAL_GIST_FOOTER = f"README.md content:\n\n{GIST_README_TEMPLATE}"

MANUAL_GIST_NOT_FOUND = (
    "We couldn't find the gist automatically. "
    "Paste the gist URL and we'll verify it directly."
)
MANUAL_GIST_DESCRIPTION_MISMATCH = (
    "The gist exists, but its description must be exactly "
    "\u201ccc-sentiment public key\u201d so sentiments.cc knows it is intentional."
)
MANUAL_GIST_KEY_MISSING = "The gist exists, but cc-sentiment.pub is empty or missing."

GITHUB_SSH_GUIDE_INTRO = (
    "This adds a public SSH key to your GitHub account. "
    "Only continue if you're comfortable with that. "
    "The private key stays on this device."
)
GITHUB_SSH_GUIDE_STEPS = (
    "1. Title: cc-sentiment",
    "2. Key type: Authentication Key",
    "3. Paste the public key from your clipboard.",
    "4. Click Add SSH key.",
    "5. Return here; verification will continue automatically.",
)

GITHUB_GPG_GUIDE_INTRO = (
    "This adds a public GPG key to your GitHub account. "
    "It is used only so sentiments.cc can find a public verification key."
)
GITHUB_GPG_GUIDE_STEPS = (
    "1. Paste the GPG public key from your clipboard.",
    "2. Click Add GPG key.",
    "3. Return here; verification will continue automatically.",
)

OPENPGP_BEFORE_SEND = (
    "keys.openpgp.org will email {email}. "
    "Click the link in that email to publish the public key."
)
OPENPGP_AFTER_SEND = (
    "Verification email sent to {email}. "
    "Open it, click the verification link, then return here. We'll keep checking."
)
OPENPGP_API_FAILURE = (
    "keys.openpgp.org didn't accept the automatic request: {error}. "
    "We opened the upload page and copied the public key to your clipboard."
)

RESUME_COPY = "Continuing setup where you left off."

USERNAME_SKIP_GPG_ONLY = (
    "Continuing without GitHub. You'll be verified by GPG fingerprint instead of "
    "a GitHub public key location. Stats are still aggregate."
)

TOOLS_TITLE = "One tool is needed to finish setup"
TOOLS_BODY = (
    "cc-sentiment can do most of setup for you if GitHub CLI or GPG is installed. "
    "Choose an option below."
)
TOOLS_GH_AUTH_DETAIL = (
    "We'll run gh auth login. After you finish, cc-sentiment can create the gist automatically."
)
TOOLS_NO_BREW_BREW = "Install one of these, then return:\n\n  brew install gh\n  brew install gnupg"
TOOLS_NO_BREW_GENERIC = (
    "Install GitHub CLI or GPG with your system's package manager, then return."
)

FIX_TITLE = "Verification is still not working"
FIX_BODY = (
    "The public key is not visible yet, or sentiments.cc could not verify the test signature. "
    "This is usually a propagation delay, a gist description mismatch, or a pasted-key mismatch."
)
FIX_HELP = "If this keeps happening, open a GitHub issue or reach out to @yasyf on Twitter/X."

SETTINGS_TITLE = "Setup complete"
SETTINGS_BODY = (
    "cc-sentiment can now upload signed, aggregate sentiment metrics. "
    "Conversation text, file paths, prompts, tool inputs, and tool outputs are not uploaded."
)


class SetupScreen(Dialog[bool]):
    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    SetupScreen > #dialog-box RadioSet { width: 100%; }
    SetupScreen > #dialog-box RadioButton { width: 100%; }
    SetupScreen > #dialog-box .status-line { width: 100%; min-height: 1; margin: 0 0 1 0; }
    SetupScreen > #dialog-box .step-card {
        width: 100%; margin: 0 0 1 0; border: round $surface; padding: 0 1;
    }
    SetupScreen > #dialog-box .recommended-card {
        width: 100%; margin: 0 0 1 0; border: round $accent; padding: 0 1;
    }
    SetupScreen > #dialog-box .copy { width: 100%; }
    """

    BINDINGS = [
        Binding("enter", "activate_primary", "Continue", priority=True),
        Binding("escape", "cancel", "Quit", priority=True),
        Binding("ctrl+c", "cancel", "Quit", priority=True),
    ]

    PRIMARY_FOCUS_BY_STAGE: ClassVar[dict[SetupStage, str]] = {
        SetupStage.DISCOVER: "#discover-retry",
        SetupStage.PROPOSE: "#propose-go",
        SetupStage.WORKING: "#working-guide",
        SetupStage.GUIDE: "#guide-check",
        SetupStage.TOOLS: "#tools-primary",
        SetupStage.FIX: "#fix-retry",
        SetupStage.SETTINGS: "#done-btn",
    }

    is_pending: reactive[bool] = reactive(False)

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.aggregate = SetupAggregate(verification_poll=VerificationPollState(started_at=monotonic()))
        self.verify_worker: Worker[None] | None = None
        self.github_allowed: bool = True

    @property
    def actions(self) -> SetupActionState:
        return self.aggregate.actions

    @property
    def discovery(self) -> DiscoveryResult:
        return self.aggregate.discovery

    @property
    def selected_route(self) -> SetupRoute | None:
        return self.aggregate.selected_route

    @selected_route.setter
    def selected_route(self, value: SetupRoute | None) -> None:
        self.aggregate.selected_route = value

    @property
    def pending(self) -> PendingSetup | None:
        return self.aggregate.pending

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            with ContentSwitcher(initial=SetupStage.DISCOVER.value):
                yield from self._compose_discover()
                yield from self._compose_propose()
                yield from self._compose_working()
                yield from self._compose_guide()
                yield from self._compose_tools()
                yield from self._compose_fix()
                yield from self._compose_settings()

    @property
    def current_stage(self) -> SetupStage:
        return SetupStage(self.query_one(ContentSwitcher).current)

    def transition_to(self, stage: SetupStage) -> None:
        if self.current_stage is stage:
            self.call_after_refresh(self._focus_step_target, stage)
            return
        self.query_one(ContentSwitcher).current = stage.value
        self.call_after_refresh(self._focus_step_target, stage)

    def _compose_discover(self) -> ComposeResult:
        with Vertical(id=SetupStage.DISCOVER.value):
            yield StepHeader(DISCOVER_TITLE, DISCOVER_BODY)
            yield StepBody(
                Static("", id="discover-rows", classes="copy"),
                Input(placeholder=USERNAME_PLACEHOLDER, id="username-input"),
                Static("", id="username-status", classes="status-line muted"),
                Static("", id="discover-status", classes="status-line muted"),
                StepActions(
                    Button("Use GPG only", id="username-skip", variant="default"),
                    Button("Continue", id="username-next", variant="default"),
                    primary=Button("Try saved key again", id="discover-retry", variant="primary"),
                ),
            )

    def _compose_propose(self) -> ComposeResult:
        with Vertical(id=SetupStage.PROPOSE.value):
            yield StepHeader(PROPOSE_TITLE, PROPOSE_BODY)
            yield StepBody(
                Static("", id="propose-recommendation", classes="copy"),
                Static("", id="propose-detail", classes="copy"),
                Static("", id="propose-safety", classes="status-line muted"),
                Static("", id="propose-warning", classes="status-line warning"),
                Static("Other options", id="propose-alt-header", classes="copy muted"),
                RadioSet(id="propose-alternatives"),
                Static(OPENPGP_EMAIL_LABEL, id="propose-email-label", classes="copy"),
                Static(OPENPGP_EMAIL_HELP, id="propose-email-help", classes="status-line muted"),
                Static("", id="propose-email-inferred", classes="status-line muted"),
                Input(placeholder="email@example.com", id="propose-email"),
                Static("", id="propose-status", classes="status-line muted"),
                StepActions(
                    Button("Use a different key", id="propose-alt", variant="default"),
                    primary=Button("Continue", id="propose-go", variant="primary"),
                ),
            )

    def _compose_working(self) -> ComposeResult:
        with Vertical(id=SetupStage.WORKING.value):
            yield StepHeader(WORKING_TITLE, WORKING_BODY)
            yield StepBody(
                Static("", id="working-steps", classes="copy"),
                Static("", id="working-status", classes="status-line muted"),
                StepActions(
                    Button("Choose another method", id="working-redo", variant="default"),
                    Button("Try again", id="working-retry", variant="default"),
                    primary=Button("Show guided setup", id="working-guide", variant="primary"),
                ),
            )

    def _compose_guide(self) -> ComposeResult:
        with Vertical(id=SetupStage.GUIDE.value):
            yield StepHeader(GUIDE_TITLE, GUIDE_BODY)
            yield StepBody(
                Static("", id="guide-instructions", classes="copy"),
                Static("", id="guide-status-panel", classes="copy muted"),
                Input(placeholder="https://gist.github.com/<user>/<id>", id="guide-gist-url"),
                Static("", id="guide-error", classes="status-line error"),
                PendingStatus("", id="guide-pending"),
                StepActions(
                    Button("Exit, continue later", id="guide-exit", variant="default"),
                    Button("Choose another method", id="guide-redo", variant="default"),
                    Button("Open page again", id="guide-open", variant="default"),
                    primary=Button("Check now", id="guide-check", variant="primary"),
                ),
            )

    def _compose_tools(self) -> ComposeResult:
        with Vertical(id=SetupStage.TOOLS.value):
            yield StepHeader(TOOLS_TITLE, TOOLS_BODY)
            yield StepBody(
                Static("", id="tools-detail", classes="copy"),
                Static("", id="tools-status", classes="status-line muted"),
                StepActions(
                    Button("", id="tools-tertiary", variant="default"),
                    Button("", id="tools-secondary", variant="default"),
                    primary=Button("", id="tools-primary", variant="primary"),
                ),
            )

    def _compose_fix(self) -> ComposeResult:
        with Vertical(id=SetupStage.FIX.value):
            yield StepHeader(FIX_TITLE, FIX_BODY)
            yield StepBody(
                Static("", id="fix-error", classes="status-line error"),
                Static(FIX_HELP, id="fix-help", classes="copy muted"),
                StepActions(
                    Button("Choose another method", id="fix-redo", variant="default"),
                    Button("Open GitHub issue", id="fix-open-issue", variant="default"),
                    Button("Back to guide", id="fix-back-guide", variant="default"),
                    primary=Button("Try again", id="fix-retry", variant="primary"),
                ),
            )

    def _compose_settings(self) -> ComposeResult:
        with Vertical(id=SetupStage.SETTINGS.value):
            yield StepHeader(SETTINGS_TITLE, SETTINGS_BODY)
            yield StepBody(DoneBranch(id="done-branch"))

    def on_mount(self) -> None:
        self.query_one("#propose-alternatives", RadioSet).display = False
        self.query_one("#propose-email", Input).display = False
        self.query_one("#propose-email-label", Static).display = False
        self.query_one("#propose-email-help", Static).display = False
        self.query_one("#propose-email-inferred", Static).display = False
        self.query_one("#propose-warning", Static).display = False
        self.query_one("#guide-gist-url", Input).display = False
        self.query_one("#username-input", Input).display = False
        self.query_one("#username-next", Button).display = False
        self.query_one("#username-skip", Button).display = False
        self.query_one("#discover-retry", Button).display = False
        self.set_interval(1.0, self._tick_pending)
        self.set_interval(0.5, self._poll_due)
        self.start_setup()

    def on_unmount(self) -> None:
        if self.verify_worker is not None:
            self.verify_worker.cancel()
        if self.aggregate.working.worker is not None:
            self.aggregate.working.worker.cancel()

    def action_activate_primary(self) -> None:
        with suppress(NoMatches, StopIteration):
            next(self.query(f"#{self.current_stage.value} Button.-primary").results(Button)).press()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def _focus_widget(self, widget: Input | Button | RadioSet) -> None:
        if not widget.display or widget.disabled:
            return
        widget.focus()

    def _focus_step_target(self, stage: SetupStage) -> None:
        if (selector := self.PRIMARY_FOCUS_BY_STAGE.get(stage)) is None:
            return
        with suppress(NoMatches):
            target = self.query_one(selector, Button)
            self._focus_widget(target)

    @work()
    async def start_setup(self) -> None:
        if await self._maybe_resume_pending():
            return
        match await self._verify_saved_state():
            case "ok":
                return
            case "temporary":
                return
            case "none" | "invalid":
                pass
        await self._run_discover_phase()

    async def _maybe_resume_pending(self) -> bool:
        pending_model = self.state.pending_setup
        if pending_model is None:
            return False
        self.aggregate.pending = self._pending_from_model(pending_model)
        await self._enter_guide_for_resume()
        return True

    async def _verify_saved_state(self) -> Literal["ok", "temporary", "none", "invalid"]:
        if self.state.config is None:
            return "none"
        result = await Uploader().probe_credentials(self.state.config)
        match result:
            case AuthOk():
                self._enter_settings_for_saved_config()
                return "ok"
            case AuthUnauthorized():
                self._render_saved_invalid()
                return "invalid"
            case _:
                self._render_saved_temporary()
                return "temporary"

    async def _run_discover_phase(self) -> None:
        self._update_status("discover-status", "Looking for keys, GitHub identity, and tools…")
        username_hint = self._best_known_username()
        result = await anyio.to_thread.run_sync(
            DiscoveryRunner.run, username_hint, self.github_allowed,
        )
        self.aggregate.discovery = result
        self._render_discover_rows(result.rows)
        if (verified := await self._auto_verify(result)) is not None:
            self.aggregate.discovery = self._mark_verify_rows(self.aggregate.discovery, True)
            self._render_discover_rows(self.aggregate.discovery.rows)
            self.state.config = verified
            await anyio.to_thread.run_sync(self.state.save)
            self._update_status("discover-status", DISCOVER_VERIFIED_COPY, Tone.SUCCESS)
            self._enter_settings_for_saved_config()
            return
        self.aggregate.discovery = self._mark_verify_rows(self.aggregate.discovery, False)
        self._render_discover_rows(self.aggregate.discovery.rows)
        self._update_status("discover-status", DISCOVER_NO_MATCH_COPY, Tone.WARNING)
        if result.recommended is None or result.recommended.route_id in (RouteId.INSTALL_TOOLS, RouteId.SIGN_IN_GH):
            self._render_tools(result)
            self.transition_to(SetupStage.TOOLS)
            return
        if not result.identity.github_username and self._needs_username(result):
            self._show_inline_username_prompt()
            return
        await self._enter_propose()

    @staticmethod
    def _mark_verify_rows(discovery: DiscoveryResult, verified: bool) -> DiscoveryResult:
        rows = list(discovery.rows)
        if len(rows) >= 2:
            rows[-2] = replace(rows[-2], state=DiscoverRowState.OK, detail="Checked.")
            rows[-1] = replace(
                rows[-1],
                state=DiscoverRowState.OK if verified else DiscoverRowState.WARNING,
                detail="Verified." if verified else "No match yet.",
            )
        return replace(discovery, rows=tuple(rows))

    def _best_known_username(self) -> str:
        if self.state.github_username:
            return self.state.github_username
        match self.state.config:
            case SSHConfig(contributor_id=cid) | GistConfig(contributor_id=cid) | GistGPGConfig(contributor_id=cid):
                return cid
            case GPGConfig(contributor_type="github", contributor_id=cid):
                return cid
            case _:
                pass
        if self.state.pending_setup is not None and self.state.pending_setup.username:
            return self.state.pending_setup.username
        return ""

    def _needs_username(self, result: DiscoveryResult) -> bool:
        if (route := result.recommended) is None:
            return False
        return route.publish_method in (
            PublishMethod.GIST_AUTO,
            PublishMethod.GIST_MANUAL,
            PublishMethod.GITHUB_SSH,
            PublishMethod.GITHUB_GPG,
        )

    def _show_inline_username_prompt(self) -> None:
        self._update_status("discover-status", "Enter a GitHub username, or pick GPG only.")
        self.query_one("#username-input", Input).display = True
        self.query_one("#username-next", Button).display = True
        self.query_one("#username-skip", Button).display = True
        self.query_one("#discover-retry", Button).display = False
        self.call_after_refresh(lambda: self._focus_widget(self.query_one("#username-input", Input)))

    def _render_discover_rows(self, rows: tuple[DiscoverRow, ...]) -> None:
        markers = {
            DiscoverRowState.WAITING: "·",
            DiscoverRowState.OK: "✓",
            DiscoverRowState.SKIPPED: "—",
            DiscoverRowState.WARNING: "?",
            DiscoverRowState.ERROR: "✗",
        }
        text = "\n".join(
            f"  {markers[row.state]} {row.label}{('  ' + row.detail) if row.detail else ''}"
            for row in rows
        )
        with suppress(NoMatches):
            self.query_one("#discover-rows", Static).update(text)

    def _render_saved_invalid(self) -> None:
        self._update_status(
            "discover-status",
            f"{SAVED_KEY_INVALID_COPY_1}\n{SAVED_KEY_INVALID_COPY_2}",
            Tone.WARNING,
        )
        self.transition_to(SetupStage.DISCOVER)

    def _render_saved_temporary(self) -> None:
        self._update_status("discover-status", SAVED_KEY_TEMPORARY_COPY, Tone.WARNING)
        self.query_one("#discover-retry", Button).display = True
        self.transition_to(SetupStage.DISCOVER)

    async def _auto_verify(self, result: DiscoveryResult) -> Config | None:
        username = result.identity.github_username
        if username:
            for ssh in result.existing_ssh:
                config: Config = SSHConfig(
                    contributor_id=ContributorId(username),
                    key_path=ssh.info.path,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    return config
            for gpg in result.existing_gpg:
                config = GPGConfig(
                    contributor_type="github",
                    contributor_id=ContributorId(username),
                    fpr=gpg.info.fpr,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    return config
            try:
                gist_refs = await anyio.to_thread.run_sync(GistDiscovery.find_cc_sentiment_gists, username)
            except httpx.HTTPError:
                gist_refs = ()
            for ref in gist_refs:
                for ssh in result.existing_ssh:
                    config = GistConfig(
                        contributor_id=ContributorId(ref.owner),
                        key_path=ssh.info.path,
                        gist_id=ref.gist_id,
                    )
                    if isinstance(await Uploader().probe_credentials(config), AuthOk):
                        return config
                for gpg in result.existing_gpg:
                    config = GistGPGConfig(
                        contributor_id=ContributorId(ref.owner),
                        fpr=gpg.info.fpr,
                        gist_id=ref.gist_id,
                    )
                    if isinstance(await Uploader().probe_credentials(config), AuthOk):
                        return config
        for gpg in result.existing_gpg:
            config = GPGConfig(
                contributor_type="gpg",
                contributor_id=ContributorId(gpg.info.fpr),
                fpr=gpg.info.fpr,
            )
            if isinstance(await Uploader().probe_credentials(config), AuthOk):
                return config
        return None

    @on(Button.Pressed, "#discover-retry")
    async def on_discover_retry(self) -> None:
        await self._run_discover_phase()

    @on(Button.Pressed, "#username-next")
    async def on_username_next(self) -> None:
        username = self.query_one("#username-input", Input).value.strip()
        if not username:
            self._update_status("username-status", USERNAME_ERROR_EMPTY, Tone.ERROR)
            return
        self._update_status("username-status", f"Validating {username}…")
        match await anyio.to_thread.run_sync(IdentityProbe.validate_username, username):
            case "not-found":
                self._update_status(
                    "username-status",
                    USERNAME_ERROR_NOT_FOUND.format(user=username),
                    Tone.ERROR,
                )
                return
            case "unreachable":
                self._update_status("username-status", USERNAME_ERROR_UNREACHABLE, Tone.ERROR)
                return
        self.github_allowed = True
        self._set_username(username, UsernameSource.USER)
        self.state.github_username = username
        await anyio.to_thread.run_sync(self.state.save)
        self._hide_inline_username_prompt()
        await self._enter_propose()

    @on(Button.Pressed, "#username-skip")
    async def on_username_skip(self) -> None:
        self.github_allowed = False
        self._set_username("", UsernameSource.NONE)
        self._hide_inline_username_prompt()
        self._update_status("discover-status", USERNAME_SKIP_GPG_ONLY, Tone.MUTED)
        if self.discovery.recommended is None:
            self._render_tools(self.discovery)
            self.transition_to(SetupStage.TOOLS)
            return
        await self._enter_propose()

    def _hide_inline_username_prompt(self) -> None:
        self.query_one("#username-input", Input).display = False
        self.query_one("#username-next", Button).display = False
        self.query_one("#username-skip", Button).display = False

    def _set_username(self, username: str, source: UsernameSource) -> None:
        existing = self.discovery.identity
        new_identity = IdentityDiscovery(
            github_username=username,
            username_source=source,
            github_email=existing.github_email,
            email_source=existing.email_source,
            email_usable=existing.email_usable,
        )
        if username and not new_identity.email_usable:
            email, src, usable = IdentityProbe.mine_email(username)
            new_identity = IdentityDiscovery(
                github_username=username,
                username_source=source,
                github_email=email,
                email_source=src,
                email_usable=usable,
            )
        recommended, alternatives = SetupRoutePlanner.plan(
            self.discovery.capabilities,
            new_identity,
            self.discovery.existing_ssh,
            self.discovery.existing_gpg,
            github_allowed=self.github_allowed,
        )
        self.aggregate.discovery = DiscoveryResult(
            capabilities=self.discovery.capabilities,
            identity=new_identity,
            existing_ssh=self.discovery.existing_ssh,
            existing_gpg=self.discovery.existing_gpg,
            rows=self.discovery.rows,
            recommended=recommended,
            alternatives=alternatives,
        )

    async def _enter_propose(self) -> None:
        result = self.discovery
        if result.recommended is None or result.recommended.route_id in (
            RouteId.INSTALL_TOOLS, RouteId.SIGN_IN_GH,
        ):
            self._render_tools(result)
            self.transition_to(SetupStage.TOOLS)
            return
        self.selected_route = result.recommended
        self._render_propose(result.recommended, result.alternatives)
        self.transition_to(SetupStage.PROPOSE)

    def _render_propose(
        self,
        recommended: SetupRoute,
        alternatives: tuple[SetupRoute, ...],
    ) -> None:
        with suppress(NoMatches):
            self.query_one("#propose-recommendation", Static).update(recommended.title)
        with suppress(NoMatches):
            self.query_one("#propose-detail", Static).update(recommended.detail)
        with suppress(NoMatches):
            self.query_one("#propose-safety", Static).update(recommended.safety_note)
        with suppress(NoMatches):
            warning = self.query_one("#propose-warning", Static)
            warning.display = bool(recommended.account_key_warning)
            warning.update(recommended.account_key_warning)
        with suppress(NoMatches):
            primary = self.query_one("#propose-go", Button)
            primary.label = recommended.primary_label
        with suppress(NoMatches):
            alt_button = self.query_one("#propose-alt", Button)
            alt_button.label = recommended.secondary_label
            alt_button.display = bool(alternatives)

        radio = self.query_one("#propose-alternatives", RadioSet)
        radio.remove_children()
        if alternatives:
            radio.mount_all(RadioButton(self._alternative_label(alt)) for alt in alternatives)
        radio.display = False
        with suppress(NoMatches):
            self.query_one("#propose-alt-header", Static).display = bool(alternatives)

        self._render_email_field(recommended)

    def _alternative_label(self, route: SetupRoute) -> str:
        match route.key_plan:
            case GenerateSSHKey():
                return "Generate cc-sentiment managed key — recommended, only for this app"
            case GenerateGPGKey():
                return "Generate cc-sentiment managed key — recommended, only for this app"
            case ExistingSSHKey(info=info):
                tag = info.comment or info.path.name
                return f"SSH key: {tag} — use only if you recognize it"
            case ExistingGPGKey(info=info):
                tag = info.email or info.fpr[-8:]
                return f"GPG key: {tag} — good for email/keyserver verification"
            case _:
                return route.title

    def _render_email_field(self, route: SetupRoute) -> None:
        identity = self.discovery.identity
        show = route.needs_email and not identity.email_usable and not self._route_email(route)
        for selector in ("#propose-email-label", "#propose-email-help", "#propose-email"):
            with suppress(NoMatches):
                self.query_one(selector).display = show
        with suppress(NoMatches):
            inferred = self.query_one("#propose-email-inferred", Static)
            if show and identity.github_email and not identity.email_usable:
                inferred.update(OPENPGP_EMAIL_INFERRED.format(email=identity.github_email))
                inferred.display = True
            else:
                inferred.display = False
        with suppress(NoMatches):
            email_input = self.query_one("#propose-email", Input)
            if show and identity.github_email:
                email_input.value = identity.github_email

    def _render_tools(self, result: DiscoveryResult) -> None:
        caps = result.capabilities
        primary = self.query_one("#tools-primary", Button)
        secondary = self.query_one("#tools-secondary", Button)
        tertiary = self.query_one("#tools-tertiary", Button)
        if caps.has_gh and not caps.gh_authed:
            self.query_one("#tools-detail", Static).update(TOOLS_GH_AUTH_DETAIL)
            primary.label = "Sign in to GitHub CLI"
            secondary.label = "Continue without GitHub CLI"
            tertiary.display = False
            return
        tertiary.display = True
        if caps.has_brew and sys.platform == "darwin":
            self.query_one("#tools-detail", Static).update(TOOLS_BODY)
            primary.label = "Install GitHub CLI with Homebrew"
            secondary.label = "Install GPG with Homebrew"
            tertiary.label = "Show manual setup options"
            return
        detail = TOOLS_NO_BREW_BREW if sys.platform == "darwin" else TOOLS_NO_BREW_GENERIC
        self.query_one("#tools-detail", Static).update(detail)
        primary.label = "I installed one"
        secondary.label = "Manual setup"
        tertiary.display = False

    @on(Button.Pressed, "#propose-go")
    async def on_propose_go(self) -> None:
        if self.actions.propose_running:
            return
        self.actions.propose_running = True
        try:
            await self._confirm_route(self.selected_route)
        finally:
            self.actions.propose_running = False

    @on(Button.Pressed, "#propose-alt")
    def on_propose_alt(self) -> None:
        radio = self.query_one("#propose-alternatives", RadioSet)
        if not radio.children:
            return
        radio.display = True
        radio.children[0].focus()

    @on(RadioSet.Changed, "#propose-alternatives")
    def on_propose_alt_changed(self, event: RadioSet.Changed) -> None:
        alternatives = self.discovery.alternatives
        idx = event.radio_set.pressed_index
        if idx < 0 or idx >= len(alternatives):
            return
        chosen = alternatives[idx]
        previous = self.selected_route
        rest = tuple(r for r in alternatives if r.route_id != chosen.route_id)
        new_alternatives = (previous, *rest) if previous else rest
        self.aggregate.discovery = DiscoveryResult(
            capabilities=self.discovery.capabilities,
            identity=self.discovery.identity,
            existing_ssh=self.discovery.existing_ssh,
            existing_gpg=self.discovery.existing_gpg,
            rows=self.discovery.rows,
            recommended=chosen,
            alternatives=new_alternatives,
        )
        self.selected_route = chosen
        self._render_propose(chosen, new_alternatives)

    async def _confirm_route(self, route: SetupRoute | None) -> None:
        if route is None:
            return
        if route.needs_email and not self._resolved_email():
            self._update_status("propose-status", OPENPGP_EMAIL_ERROR_EMPTY, Tone.ERROR)
            return
        if route.automated:
            await self._enter_working(route)
        else:
            await self._enter_guide(route)

    def _resolved_email(self) -> str:
        ident = self.discovery.identity
        if ident.email_usable and ident.github_email:
            return ident.github_email
        if self.selected_route is not None and (email := self._route_email(self.selected_route)):
            return email
        with suppress(NoMatches):
            value = self.query_one("#propose-email", Input).value.strip()
            if value:
                return value
        return ""

    @staticmethod
    def _route_email(route: SetupRoute) -> str:
        match route.key_plan:
            case ExistingGPGKey(info=info):
                return info.email
            case _:
                return ""

    def _resolve_key(self, route: SetupRoute) -> ResolvedKey:
        if self.aggregate.resolved_key is not None:
            return self.aggregate.resolved_key
        match route.key_plan:
            case ExistingSSHKey(info=info, managed=managed):
                resolved: ResolvedKey = ResolvedSSHKey(info=info, managed=managed)
            case ExistingGPGKey(info=info, managed=managed):
                resolved = ResolvedGPGKey(info=info, managed=managed)
            case GenerateSSHKey():
                resolved = ResolvedSSHKey(info=KeyDiscovery.generate_managed_ssh_key(), managed=True)
            case GenerateGPGKey():
                email = self._resolved_email()
                identity = self.discovery.identity.github_username or "cc-sentiment"
                resolved = ResolvedGPGKey(
                    info=KeyDiscovery.generate_managed_gpg_key(identity, email),
                    managed=True,
                )
            case _:
                raise AssertionError("route has no key plan")
        self.aggregate.resolved_key = resolved
        return resolved

    async def _enter_working(self, route: SetupRoute) -> None:
        steps = self._build_working_steps(route)
        self.aggregate.working.steps = steps
        self.aggregate.working.failure_text = ""
        self.aggregate.resolved_key = None
        self._render_working()
        self.transition_to(SetupStage.WORKING)
        self.aggregate.working.worker = self.run_working(route)

    def _build_working_steps(self, route: SetupRoute) -> list[WorkStep]:
        match route.publish_method:
            case PublishMethod.GIST_AUTO:
                steps: list[WorkStep] = []
                if isinstance(route.key_plan, GenerateSSHKey):
                    steps.append(WorkStep(label="Creating local cc-sentiment key…"))
                steps.extend([
                    WorkStep(label="Creating public GitHub gist…"),
                    WorkStep(label="Checking that sentiments.cc can read it…"),
                    WorkStep(label="Verifying a test signature…"),
                ])
                return steps
            case PublishMethod.OPENPGP:
                steps = []
                if isinstance(route.key_plan, GenerateGPGKey):
                    steps.append(WorkStep(label="Creating local GPG key…"))
                steps.extend([
                    WorkStep(label="Uploading public key to keys.openpgp.org…"),
                    WorkStep(label="Requesting email verification…"),
                    WorkStep(label="Waiting for keyserver publication…"),
                    WorkStep(label="Verifying a test signature…"),
                ])
                return steps
            case PublishMethod.GITHUB_SSH | PublishMethod.GITHUB_GPG:
                return [
                    WorkStep(label="Adding public key to GitHub account…"),
                    WorkStep(label="Waiting for GitHub public key list…"),
                    WorkStep(label="Verifying a test signature…"),
                ]
            case _:
                return []

    def _render_working(self) -> None:
        markers = {
            WorkStepState.PENDING: "·",
            WorkStepState.RUNNING: "…",
            WorkStepState.SUCCESS: "✓",
            WorkStepState.WARNING: "—",
            WorkStepState.ERROR: "✗",
        }
        text = "\n".join(
            f"  {markers[step.state]} {step.label}{('  ' + step.detail) if step.detail else ''}"
            for step in self.aggregate.working.steps
        )
        with suppress(NoMatches):
            self.query_one("#working-steps", Static).update(text)
        with suppress(NoMatches):
            self.query_one("#working-status", Static).update(
                self.aggregate.working.failure_text or ""
            )
        with suppress(NoMatches):
            failure = bool(self.aggregate.working.failure_text)
            self.query_one("#working-guide", Button).display = failure
            self.query_one("#working-retry", Button).display = failure
            self.query_one("#working-redo", Button).display = failure

    @work(thread=True)
    def run_working(self, route: SetupRoute) -> None:
        call = self.app.call_from_thread
        try:
            self._execute_route(route, call)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError, AssertionError, httpx.HTTPError) as exc:
            call(self._on_working_failure, route, Sanitizer.error(str(exc)))

    def _execute_route(self, route: SetupRoute, call) -> None:
        match route.publish_method:
            case PublishMethod.GIST_AUTO:
                self._execute_gist_auto(route, call)
            case PublishMethod.OPENPGP:
                self._execute_openpgp(route, call)
            case PublishMethod.GITHUB_SSH:
                self._execute_github_ssh(route, call)
            case PublishMethod.GITHUB_GPG:
                self._execute_github_gpg(route, call)
            case _:
                pass

    def _step_running(self, idx: int) -> None:
        self.aggregate.working.steps[idx].state = WorkStepState.RUNNING
        self._render_working()

    def _step_success(self, idx: int, detail: str = "") -> None:
        self.aggregate.working.steps[idx].state = WorkStepState.SUCCESS
        if detail:
            self.aggregate.working.steps[idx].detail = detail
        self._render_working()

    def _execute_gist_auto(self, route: SetupRoute, call) -> None:
        idx = 0
        if isinstance(route.key_plan, GenerateSSHKey | GenerateGPGKey):
            call(self._step_running, idx)
            resolved = self._resolve_key(route)
            detail = (
                "managed"
                if isinstance(resolved, ResolvedSSHKey) and resolved.managed
                else resolved.info.fpr[-8:]
                if isinstance(resolved, ResolvedGPGKey)
                else ""
            )
            call(self._step_success, idx, detail)
            idx += 1
        else:
            self._resolve_key(route)

        resolved = self.aggregate.resolved_key
        assert resolved is not None
        call(self._step_running, idx)
        pub_text = self._public_key_text(resolved)
        gist_id = KeyDiscovery.create_gist_from_text(pub_text)
        call(self._step_success, idx, gist_id[:8])
        idx += 1

        username = self.discovery.identity.github_username or (
            self.aggregate.pending.username if self.aggregate.pending else ""
        )
        if isinstance(resolved, ResolvedSSHKey):
            config: Config = GistConfig(
                contributor_id=ContributorId(username),
                key_path=resolved.info.path,
                gist_id=gist_id,
            )
        else:
            config = GistGPGConfig(
                contributor_id=ContributorId(username),
                fpr=resolved.info.fpr,
                gist_id=gist_id,
            )

        call(self._step_running, idx)
        metadata = GistDiscovery.fetch_metadata(GistRef(owner=username, gist_id=gist_id))
        if metadata is None or metadata.description != GIST_DESCRIPTION:
            raise AssertionError("created gist is not visible yet")
        call(self._step_success, idx)
        idx += 1

        call(self._step_running, idx)
        location = "GitHub gist"
        lookup = f"@{username} · gist {gist_id[:8]}"
        call(self._on_working_complete, route, config, location, lookup)

    def _execute_openpgp(self, route: SetupRoute, call) -> None:
        idx = 0
        if isinstance(route.key_plan, GenerateGPGKey):
            call(self._step_running, idx)
            self._resolve_key(route)
            resolved = self.aggregate.resolved_key
            assert isinstance(resolved, ResolvedGPGKey)
            call(self._step_success, idx, resolved.info.fpr[-8:])
            idx += 1
        else:
            self._resolve_key(route)
            resolved = self.aggregate.resolved_key
            assert isinstance(resolved, ResolvedGPGKey)

        call(self._step_running, idx)
        armor = GPGBackend(fpr=resolved.info.fpr).public_key_text()
        try:
            token, statuses = self._upload_openpgp_key(armor)
        except httpx.HTTPError as exc:
            call(self._on_openpgp_api_failure, route, resolved, armor, Sanitizer.error(str(exc)))
            return
        call(self._step_success, idx)
        idx += 1

        emails = self._openpgp_verification_emails(
            statuses, self._resolved_email() or resolved.info.email,
        )
        call(self._step_running, idx)
        try:
            self._request_openpgp_verification(token, emails)
        except httpx.HTTPError as exc:
            call(self._on_openpgp_api_failure, route, resolved, armor, Sanitizer.error(str(exc)))
            return
        call(self._step_success, idx, ", ".join(emails))
        idx += 1

        config: Config = GPGConfig(
            contributor_type="gpg",
            contributor_id=ContributorId(resolved.info.fpr),
            fpr=resolved.info.fpr,
        )

        location = "keys.openpgp.org"
        lookup = f"GPG {resolved.info.fpr[-8:]}"
        instructions = OPENPGP_AFTER_SEND.format(email=", ".join(emails))
        call(self._step_running, idx)
        call(
            self._on_working_pending,
            route,
            config,
            location,
            lookup,
            instructions,
            PendingSetupStatus.OPENPGP_EMAIL_SENT,
        )

    @staticmethod
    def _upload_openpgp_key(armor: str) -> tuple[str, dict[str, str]]:
        return KeyDiscovery.upload_openpgp_key(armor)

    @staticmethod
    def _openpgp_verification_emails(statuses: dict[str, str], fallback_email: str) -> list[str]:
        return [
            email
            for email in (
                [e for e, status in statuses.items() if status == "unpublished"]
                or [fallback_email]
            )
            if email
        ]

    @staticmethod
    def _request_openpgp_verification(token: str, emails: list[str]) -> None:
        if emails:
            KeyDiscovery.request_openpgp_verify(token, emails)

    def _on_openpgp_api_failure(
        self,
        route: SetupRoute,
        resolved: ResolvedGPGKey,
        armor: str,
        error: str,
    ) -> None:
        self._enter_manual_openpgp_upload(route, resolved, armor, error)

    def _enter_manual_openpgp_upload(
        self,
        route: SetupRoute,
        resolved: ResolvedGPGKey,
        armor: str,
        error: str,
    ) -> None:
        self.aggregate.guide.reset(monotonic())
        self.aggregate.guide.openpgp_email_sent = True
        self.aggregate.fallback.clear()
        self._copy_or_record_fallback(armor)
        self._open_or_record_fallback(OPENPGP_UPLOAD_URL)
        config: Config = GPGConfig(
            contributor_type="gpg",
            contributor_id=ContributorId(resolved.info.fpr),
            fpr=resolved.info.fpr,
        )
        self.aggregate.candidate.stage(
            config,
            "keys.openpgp.org",
            f"GPG {resolved.info.fpr[-8:]}",
        )
        self._persist_pending(
            route,
            "keys.openpgp.org",
            "",
            PendingSetupStatus.MANUAL_OPENPGP_UPLOAD,
            error,
        )
        self._render_guide_instructions(OPENPGP_API_FAILURE.format(error=error))
        self._render_guide_status()
        self.transition_to(SetupStage.GUIDE)
        self._render_guide_buttons(route)
        self.aggregate.verification_poll.restart(monotonic())
        self.verify_server_config()

    def _execute_github_ssh(self, route: SetupRoute, call) -> None:
        self._resolve_key(route)
        resolved = self.aggregate.resolved_key
        assert isinstance(resolved, ResolvedSSHKey)
        idx = 0
        call(self._step_running, idx)
        if not KeyDiscovery.upload_github_ssh_key(resolved.info):
            raise AssertionError("gh ssh-key add failed")
        call(self._step_success, idx)
        idx += 1
        username = self.discovery.identity.github_username
        config: Config = SSHConfig(contributor_id=ContributorId(username), key_path=resolved.info.path)
        call(self._step_running, idx)
        call(self._on_working_complete, route, config, "GitHub SSH keys", f"@{username}")

    def _execute_github_gpg(self, route: SetupRoute, call) -> None:
        self._resolve_key(route)
        resolved = self.aggregate.resolved_key
        assert isinstance(resolved, ResolvedGPGKey)
        idx = 0
        call(self._step_running, idx)
        if not KeyDiscovery.upload_github_gpg_key(resolved.info):
            raise AssertionError("gh gpg-key add failed")
        call(self._step_success, idx)
        idx += 1
        username = self.discovery.identity.github_username
        config: Config = GPGConfig(
            contributor_type="github",
            contributor_id=ContributorId(username),
            fpr=resolved.info.fpr,
        )
        call(self._step_running, idx)
        call(self._on_working_complete, route, config, "GitHub GPG keys", f"@{username}")

    def _on_working_complete(
        self,
        route: SetupRoute,
        config: Config,
        location: str,
        lookup: str,
    ) -> None:
        self._render_working()
        self.aggregate.candidate.stage(config, location, lookup)
        self._persist_pending(
            route,
            location,
            getattr(config, "gist_id", ""),
            PendingSetupStatus.VERIFY_PENDING,
        )
        self.aggregate.verification_poll.restart(monotonic())
        self.verify_server_config()

    def _on_working_pending(
        self,
        route: SetupRoute,
        config: Config,
        location: str,
        lookup: str,
        instructions: str,
        status: PendingSetupStatus = PendingSetupStatus.VERIFY_PENDING,
    ) -> None:
        running = [step for step in self.aggregate.working.steps if step.state is WorkStepState.RUNNING]
        if running:
            running[-1].state = WorkStepState.WARNING
        elif self.aggregate.working.steps:
            self.aggregate.working.steps[-1].state = WorkStepState.WARNING
        self._render_working()
        self.aggregate.candidate.stage(config, location, lookup)
        self._persist_pending(route, location, "", status)
        self.aggregate.verification_poll.restart(monotonic())
        self.aggregate.guide.reset(monotonic())
        self.aggregate.guide.openpgp_email_sent = status in (
            PendingSetupStatus.OPENPGP_EMAIL_SENT,
            PendingSetupStatus.MANUAL_OPENPGP_UPLOAD,
        )
        with suppress(NoMatches):
            self.query_one("#guide-instructions", Static).update(instructions)
        self._render_guide_status()
        self.transition_to(SetupStage.GUIDE)
        self._render_guide_buttons(route)
        self.verify_server_config()

    def _on_working_failure(self, route: SetupRoute, error: str) -> None:
        for step in self.aggregate.working.steps:
            if step.state is WorkStepState.RUNNING:
                step.state = WorkStepState.ERROR
                step.detail = error
        self.aggregate.working.failure_text = WORKING_RECOVERABLE_FAILURE.format(error=error)
        self._render_working()
        self._update_pending(PendingSetupStatus.WORKING_FAILED, error)

    @on(Button.Pressed, "#working-guide")
    async def on_working_guide(self) -> None:
        if self.selected_route is None:
            return
        await self._enter_guide(self.selected_route)

    @on(Button.Pressed, "#working-retry")
    async def on_working_retry(self) -> None:
        if self.selected_route is None:
            return
        await self._enter_working(self.selected_route)

    @on(Button.Pressed, "#working-redo")
    async def on_working_redo(self) -> None:
        self._clear_pending_candidate()
        await self._enter_propose()

    async def _enter_guide(self, route: SetupRoute) -> None:
        self.selected_route = route
        self.aggregate.guide.reset(monotonic())
        self.aggregate.fallback.clear()
        instructions, gist_url_visible = self._guide_instructions(route)
        self._render_guide_instructions(instructions)
        with suppress(NoMatches):
            self.query_one("#guide-gist-url", Input).display = gist_url_visible
        with suppress(NoMatches):
            self.query_one("#guide-error", Static).update("")
        self._render_guide_status()
        self.transition_to(SetupStage.GUIDE)
        await self._run_guide_side_effects(route)
        self._render_guide_instructions(instructions)
        self._guide_apply_temp_config(route)
        self._persist_pending(
            route,
            PUBLIC_LOCATION_LABEL.get(route.publish_method, ""),
            "",
            PendingSetupStatus.AWAITING_USER,
        )
        self._render_guide_buttons(route)
        if route.publish_method is not PublishMethod.OPENPGP:
            self.verify_server_config()

    def _render_guide_buttons(self, route: SetupRoute) -> None:
        with suppress(NoMatches):
            primary = self.query_one("#guide-check", Button)
            if (
                route.publish_method is PublishMethod.OPENPGP
                and not self.aggregate.guide.openpgp_email_sent
            ):
                primary.label = "Send verification email"
            else:
                primary.label = "Check now"

    def _guide_instructions(self, route: SetupRoute) -> tuple[str, bool]:
        match route.publish_method:
            case PublishMethod.GIST_MANUAL:
                steps = "\n".join(MANUAL_GIST_STEPS)
                return f"{MANUAL_GIST_INTRO}\n\n{steps}\n\n{MANUAL_GIST_FOOTER}", False
            case PublishMethod.GITHUB_SSH:
                steps = "\n".join(GITHUB_SSH_GUIDE_STEPS)
                return f"{GITHUB_SSH_GUIDE_INTRO}\n\n{steps}", False
            case PublishMethod.GITHUB_GPG:
                steps = "\n".join(GITHUB_GPG_GUIDE_STEPS)
                return f"{GITHUB_GPG_GUIDE_INTRO}\n\n{steps}", False
            case PublishMethod.OPENPGP:
                email = self._resolved_email()
                return OPENPGP_BEFORE_SEND.format(email=email or "your address"), False
            case _:
                return "", False

    def _render_guide_instructions(self, body: str) -> None:
        with suppress(NoMatches):
            self.query_one("#guide-instructions", Static).update(self._guide_text_with_fallbacks(body))

    def _guide_text_with_fallbacks(self, body: str) -> str:
        fallback = self.aggregate.fallback
        blocks = [body]
        if fallback.browser_failed and fallback.url:
            blocks.append(f"Open this URL manually: {fallback.url}")
        if fallback.clipboard_failed and fallback.public_key:
            blocks.append(f"Copy this public key manually:\n\n{fallback.public_key}")
        return "\n\n".join(block for block in blocks if block)

    async def _run_guide_side_effects(self, route: SetupRoute) -> None:
        match route.publish_method:
            case PublishMethod.GIST_MANUAL:
                resolved = self._resolve_key(route)
                self._copy_or_record_fallback(self._public_key_text(resolved))
                self._open_or_record_fallback(GIST_NEW_URL)
            case PublishMethod.GITHUB_SSH:
                resolved = self._resolve_key(route)
                self._copy_or_record_fallback(self._public_key_text(resolved))
                self._open_or_record_fallback(GITHUB_SSH_NEW_URL)
            case PublishMethod.GITHUB_GPG:
                resolved = self._resolve_key(route)
                self._copy_or_record_fallback(self._public_key_text(resolved))
                self._open_or_record_fallback(GITHUB_GPG_NEW_URL)
            case _:
                pass

    def _copy_or_record_fallback(self, public_key: str) -> None:
        if Clipboard.copy(public_key):
            return
        self.aggregate.fallback.public_key = public_key
        self.aggregate.fallback.clipboard_failed = True

    def _open_or_record_fallback(self, url: str) -> None:
        if Browser.open(url):
            return
        self.aggregate.fallback.url = url
        self.aggregate.fallback.browser_failed = True

    @staticmethod
    def _public_key_text(resolved: ResolvedKey) -> str:
        match resolved:
            case ResolvedSSHKey(info=info):
                return SSHBackend(private_key_path=info.path).public_key_text()
            case ResolvedGPGKey(info=info):
                return GPGBackend(fpr=info.fpr).public_key_text()

    def _guide_apply_temp_config(self, route: SetupRoute) -> None:
        username = self.discovery.identity.github_username
        match route.publish_method:
            case PublishMethod.GITHUB_SSH:
                resolved = self._resolve_key(route)
                assert isinstance(resolved, ResolvedSSHKey)
                self.aggregate.candidate.stage(
                    SSHConfig(
                        contributor_id=ContributorId(username),
                        key_path=resolved.info.path,
                    ),
                    "GitHub SSH keys",
                    f"@{username}",
                )
            case PublishMethod.GITHUB_GPG:
                resolved = self._resolve_key(route)
                assert isinstance(resolved, ResolvedGPGKey)
                self.aggregate.candidate.stage(
                    GPGConfig(
                        contributor_type="github",
                        contributor_id=ContributorId(username),
                        fpr=resolved.info.fpr,
                    ),
                    "GitHub GPG keys",
                    f"@{username}",
                )
            case PublishMethod.OPENPGP:
                resolved = self._resolve_key(route)
                assert isinstance(resolved, ResolvedGPGKey)
                self.aggregate.candidate.stage(
                    GPGConfig(
                        contributor_type="gpg",
                        contributor_id=ContributorId(resolved.info.fpr),
                        fpr=resolved.info.fpr,
                    ),
                    "keys.openpgp.org",
                    f"GPG {resolved.info.fpr[-8:]}",
                )
            case _:
                pass

    def _render_guide_status(self) -> None:
        guide = self.aggregate.guide
        elapsed = max(0, int(monotonic() - guide.started_at)) if guide.started_at else 0
        last_checked = "never"
        if guide.last_checked_at:
            last_checked = "just now" if monotonic() - guide.last_checked_at < 5 else f"{int(monotonic() - guide.last_checked_at)}s ago"
        rows = [
            f"  Public key: {'found' if guide.public_key_found else 'waiting'}",
            f"  sentiments.cc verification: "
            f"{'verified' if guide.server_verified else ('failed' if guide.last_error else 'waiting')}",
            f"  Last checked: {last_checked}",
            f"  Elapsed: {elapsed // 60}:{elapsed % 60:02d}",
        ]
        with suppress(NoMatches):
            self.query_one("#guide-status-panel", Static).update("\n".join(rows))
        with suppress(NoMatches):
            self.query_one("#guide-error", Static).update(
                Sanitizer.error(guide.last_error) if guide.last_error else ""
            )
        with suppress(NoMatches):
            self.query_one("#guide-pending", PendingStatus).label = (
                "Verification verified."
                if guide.server_verified
                else f"Waiting for the public key to propagate… {elapsed // 60}:{elapsed % 60:02d}"
            )

    @on(Button.Pressed, "#guide-check")
    async def on_guide_check(self) -> None:
        if (
            self.selected_route is not None
            and self.selected_route.publish_method is PublishMethod.OPENPGP
            and not self.aggregate.guide.openpgp_email_sent
        ):
            await self._openpgp_send_email()
            return
        if (
            self.selected_route is not None
            and self.selected_route.publish_method is PublishMethod.GIST_MANUAL
            and self.aggregate.candidate.config is None
            and not await self._discover_manual_gist()
        ):
            return
        self.aggregate.guide.last_checked_at = monotonic()
        self.verify_server_config()

    async def _discover_manual_gist(self) -> bool:
        pending_gist = self.aggregate.pending.gist_id if self.aggregate.pending else ""
        username = self.discovery.identity.github_username or (
            self.aggregate.pending.username if self.aggregate.pending else ""
        )
        if pending_gist and username:
            return await self._stage_manual_gist(GistRef(owner=username, gist_id=pending_gist))
        if not username:
            self.query_one("#guide-gist-url", Input).display = True
            self._update_status("guide-error", MANUAL_GIST_NOT_FOUND, Tone.WARNING)
            self._update_pending(PendingSetupStatus.GIST_NOT_FOUND, MANUAL_GIST_NOT_FOUND)
            return False
        try:
            refs = await anyio.to_thread.run_sync(GistDiscovery.find_cc_sentiment_gists, username)
        except httpx.HTTPError:
            self._update_status("guide-error", USERNAME_ERROR_UNREACHABLE, Tone.WARNING)
            self._update_pending(PendingSetupStatus.NETWORK_PENDING, USERNAME_ERROR_UNREACHABLE)
            return False
        for ref in refs:
            if await self._stage_manual_gist(ref):
                return True
        self.query_one("#guide-gist-url", Input).display = True
        self._update_status("guide-error", MANUAL_GIST_NOT_FOUND, Tone.WARNING)
        self._update_pending(PendingSetupStatus.GIST_NOT_FOUND, MANUAL_GIST_NOT_FOUND)
        return False

    async def _stage_manual_gist(self, ref: GistRef) -> bool:
        resolved = self.aggregate.resolved_key
        if resolved is None:
            return False
        try:
            metadata = await anyio.to_thread.run_sync(GistDiscovery.fetch_metadata, ref)
        except httpx.HTTPError:
            self.query_one("#guide-gist-url", Input).display = True
            self._update_status("guide-error", USERNAME_ERROR_UNREACHABLE, Tone.WARNING)
            self._update_pending(PendingSetupStatus.NETWORK_PENDING, USERNAME_ERROR_UNREACHABLE)
            return False
        if metadata is None:
            self.query_one("#guide-gist-url", Input).display = True
            self._update_status("guide-error", MANUAL_GIST_NOT_FOUND, Tone.WARNING)
            self._update_pending(PendingSetupStatus.GIST_NOT_FOUND, MANUAL_GIST_NOT_FOUND)
            return False
        if metadata.description.strip() != GIST_DESCRIPTION:
            self.query_one("#guide-gist-url", Input).display = True
            self._update_status("guide-error", MANUAL_GIST_DESCRIPTION_MISMATCH, Tone.WARNING)
            self._update_pending(
                PendingSetupStatus.GIST_DESCRIPTION_MISMATCH,
                MANUAL_GIST_DESCRIPTION_MISMATCH,
            )
            return False
        if not metadata.public_key:
            self.query_one("#guide-gist-url", Input).display = True
            self._update_status("guide-error", MANUAL_GIST_KEY_MISSING, Tone.WARNING)
            self._update_pending(PendingSetupStatus.GIST_NOT_FOUND, MANUAL_GIST_KEY_MISSING)
            return False
        candidate: Config = (
            GistConfig(
                contributor_id=ContributorId(ref.owner),
                key_path=resolved.info.path,
                gist_id=ref.gist_id,
            )
            if isinstance(resolved, ResolvedSSHKey)
            else GistGPGConfig(
                contributor_id=ContributorId(ref.owner),
                fpr=resolved.info.fpr,
                gist_id=ref.gist_id,
            )
        )
        self.aggregate.candidate.stage(
            candidate, "GitHub gist", f"@{ref.owner} · gist {ref.gist_id[:8]}",
        )
        self.aggregate.guide.public_key_found = True
        self._update_pending(
            PendingSetupStatus.VERIFY_PENDING,
            "",
            ref.gist_id,
            ref.owner,
        )
        return True

    async def _openpgp_send_email(self) -> None:
        route = self.selected_route
        assert route is not None
        resolved = self._resolve_key(route)
        assert isinstance(resolved, ResolvedGPGKey)
        armor = await anyio.to_thread.run_sync(
            lambda: GPGBackend(fpr=resolved.info.fpr).public_key_text()
        )
        try:
            token, statuses = await anyio.to_thread.run_sync(self._upload_openpgp_key, armor)
        except httpx.HTTPError as exc:
            self._enter_manual_openpgp_upload(route, resolved, armor, Sanitizer.error(str(exc)))
            return
        emails = self._openpgp_verification_emails(
            statuses, self._resolved_email() or resolved.info.email,
        )
        try:
            await anyio.to_thread.run_sync(self._request_openpgp_verification, token, emails)
        except httpx.HTTPError as exc:
            self._enter_manual_openpgp_upload(route, resolved, armor, Sanitizer.error(str(exc)))
            return
        self.aggregate.guide.openpgp_email_sent = True
        self._update_pending(PendingSetupStatus.OPENPGP_EMAIL_SENT)
        with suppress(NoMatches):
            self.query_one("#guide-instructions", Static).update(
                OPENPGP_AFTER_SEND.format(email=", ".join(emails))
            )
        self._render_guide_buttons(route)
        self.aggregate.verification_poll.restart(monotonic())
        self.verify_server_config()

    @on(Button.Pressed, "#guide-open")
    def on_guide_open(self) -> None:
        if self.selected_route is None:
            return
        match self.selected_route.publish_method:
            case PublishMethod.GIST_MANUAL:
                Browser.open(GIST_NEW_URL)
            case PublishMethod.GITHUB_SSH:
                Browser.open(GITHUB_SSH_NEW_URL)
            case PublishMethod.GITHUB_GPG:
                Browser.open(GITHUB_GPG_NEW_URL)
            case PublishMethod.OPENPGP if (
                self.aggregate.pending is not None
                and self.aggregate.pending.last_status is PendingSetupStatus.MANUAL_OPENPGP_UPLOAD
            ):
                Browser.open(OPENPGP_UPLOAD_URL)
            case _:
                pass

    @on(Button.Pressed, "#guide-redo")
    async def on_guide_redo(self) -> None:
        self._clear_pending_candidate()
        await self._enter_propose()

    @on(Button.Pressed, "#guide-exit")
    def on_guide_exit(self) -> None:
        self.dismiss(False)

    @on(Input.Submitted, "#guide-gist-url")
    async def on_guide_gist_url(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        fallback_owner = self.discovery.identity.github_username or (
            self.aggregate.pending.username if self.aggregate.pending else ""
        )
        ref = GistDiscovery.parse_ref(url, fallback_owner)
        if ref is None:
            return
        if not await self._stage_manual_gist(ref):
            return
        self.verify_server_config()

    @on(Button.Pressed, "#tools-primary")
    async def on_tools_primary(self) -> None:
        if self.actions.tools_running:
            return
        self.actions.tools_running = True
        try:
            caps = self.discovery.capabilities
            if caps.has_gh and not caps.gh_authed:
                ok = await anyio.to_thread.run_sync(KeyDiscovery.gh_auth_login_interactive)
                if not ok:
                    self._update_status("tools-status", "gh auth login didn't finish.", Tone.ERROR)
                    return
            elif caps.has_brew and sys.platform == "darwin":
                ok, err = await anyio.to_thread.run_sync(KeyDiscovery.install_with_brew, "gh")
                if not ok:
                    self._update_status("tools-status", err or "brew install failed", Tone.ERROR)
                    return
            await self._run_discover_phase()
        finally:
            self.actions.tools_running = False

    @on(Button.Pressed, "#tools-secondary")
    async def on_tools_secondary(self) -> None:
        if self.actions.tools_running:
            return
        self.actions.tools_running = True
        try:
            caps = self.discovery.capabilities
            if caps.has_gh and not caps.gh_authed:
                self.github_allowed = False
                suppressed = replace(caps, has_gh=False, gh_authed=False)
                recommended, alternatives = SetupRoutePlanner.plan(
                    suppressed,
                    self.discovery.identity,
                    self.discovery.existing_ssh,
                    self.discovery.existing_gpg,
                    github_allowed=self.github_allowed,
                )
                self.aggregate.discovery = replace(
                    self.discovery,
                    capabilities=suppressed,
                    recommended=recommended,
                    alternatives=alternatives,
                )
                await self._enter_propose()
                return
            if caps.has_brew and sys.platform == "darwin":
                ok, err = await anyio.to_thread.run_sync(KeyDiscovery.install_with_brew, "gnupg")
                if not ok:
                    self._update_status("tools-status", err or "brew install failed", Tone.ERROR)
                    return
                await self._run_discover_phase()
                return
            await self._enter_propose()
        finally:
            self.actions.tools_running = False

    @on(Button.Pressed, "#tools-tertiary")
    async def on_tools_tertiary(self) -> None:
        await self._enter_propose()

    def _persist_pending(
        self,
        route: SetupRoute,
        location: str,
        gist_id: str,
        status: PendingSetupStatus = PendingSetupStatus.CREATED,
        error: str = "",
    ) -> None:
        if route.key_kind is None or route.publish_method is None:
            return
        resolved = self._resolve_key(route)
        username = self.discovery.identity.github_username
        email = self._resolved_email()
        match resolved:
            case ResolvedSSHKey(info=info, managed=managed):
                key_path = info.path
                key_fpr = None
                key_kind = KeyKind.SSH
                key_managed = managed
            case ResolvedGPGKey(info=info, managed=managed):
                key_path = None
                key_fpr = info.fpr
                key_kind = KeyKind.GPG
                key_managed = managed
        pending = PendingSetup(
            route_id=route.route_id,
            publish_method=route.publish_method,
            key_kind=key_kind,
            key_managed=key_managed,
            key_path=key_path,
            key_fpr=key_fpr,
            username=username,
            email=email,
            public_location=location,
            gist_id=gist_id,
            last_status=status,
            last_error=error,
            started_at=self.aggregate.guide.started_at or monotonic(),
            updated_at=monotonic(),
        )
        self.aggregate.pending = pending
        self._save_pending(pending)

    def _save_pending(self, pending: PendingSetup) -> None:
        self.state.pending_setup = PendingSetupModel(
            route_id=pending.route_id.value,
            publish_method=pending.publish_method.value,
            key_kind=pending.key_kind.value,
            key_managed=pending.key_managed,
            key_path=pending.key_path,
            key_fpr=pending.key_fpr,
            username=pending.username,
            email=pending.email,
            public_location=pending.public_location,
            gist_id=pending.gist_id,
            last_status=pending.last_status.value,
            last_error=pending.last_error,
            started_at=pending.started_at,
            updated_at=pending.updated_at,
        )
        self.state.save()

    def _update_pending(
        self,
        status: PendingSetupStatus,
        error: str = "",
        gist_id: str = "",
        username: str = "",
    ) -> None:
        if self.aggregate.pending is None:
            return
        next_gist_id = gist_id or self.aggregate.pending.gist_id
        next_username = username or self.aggregate.pending.username
        if (
            self.aggregate.pending.last_status == status
            and self.aggregate.pending.last_error == error
            and self.aggregate.pending.gist_id == next_gist_id
            and self.aggregate.pending.username == next_username
        ):
            return
        pending = replace(
            self.aggregate.pending,
            last_status=status,
            last_error=error,
            gist_id=next_gist_id,
            username=next_username,
            updated_at=monotonic(),
        )
        self.aggregate.pending = pending
        self._save_pending(pending)

    @staticmethod
    def _pending_from_model(model: PendingSetupModel) -> PendingSetup:
        return PendingSetup(
            route_id=RouteId(model.route_id),
            publish_method=PublishMethod(model.publish_method),
            key_kind=KeyKind(model.key_kind),
            key_managed=model.key_managed,
            key_path=model.key_path,
            key_fpr=model.key_fpr,
            username=model.username,
            email=model.email,
            public_location=model.public_location,
            gist_id=model.gist_id,
            last_status=PendingSetupStatus(model.last_status),
            last_error=model.last_error,
            started_at=model.started_at,
            updated_at=model.updated_at,
        )

    async def _enter_guide_for_resume(self) -> None:
        pending = self.aggregate.pending
        assert pending is not None
        self.aggregate.guide.reset(monotonic())
        self.aggregate.fallback.clear()
        self.aggregate.discovery = DiscoveryResult(
            identity=IdentityDiscovery(
                github_username=pending.username,
                username_source=UsernameSource.SAVED if pending.username else UsernameSource.NONE,
                github_email=pending.email,
                email_usable=bool(pending.email),
            )
        )
        match pending.key_kind:
            case KeyKind.SSH:
                assert pending.key_path is not None
                self.aggregate.resolved_key = ResolvedSSHKey(
                    info=self._rehydrate_ssh_info(pending.key_path),
                    managed=pending.key_managed,
                )
            case KeyKind.GPG:
                assert pending.key_fpr is not None
                self.aggregate.resolved_key = ResolvedGPGKey(
                    info=self._rehydrate_gpg_info(pending.key_fpr, pending.email),
                    managed=pending.key_managed,
                )
        route = self._route_from_pending(pending)
        self.selected_route = route
        self._stage_pending_candidate(pending)
        route_instructions, gist_visible = self._guide_instructions(route)
        instructions = f"{RESUME_COPY}\n\n{route_instructions}".strip()
        if pending.last_error:
            instructions = (
                f"{instructions}\n\nLast error: {Sanitizer.error(pending.last_error)}"
            )
        self.aggregate.guide.last_error = pending.last_error
        self.aggregate.guide.openpgp_email_sent = pending.last_status in (
            PendingSetupStatus.OPENPGP_EMAIL_SENT,
            PendingSetupStatus.MANUAL_OPENPGP_UPLOAD,
        )
        show_gist_input = (
            pending.last_status in (
                PendingSetupStatus.GIST_NOT_FOUND,
                PendingSetupStatus.GIST_DESCRIPTION_MISMATCH,
                PendingSetupStatus.VERIFY_UNAUTHORIZED,
            )
            and not pending.gist_id
        )
        with suppress(NoMatches):
            self.query_one("#guide-instructions", Static).update(instructions)
            self.query_one("#guide-gist-url", Input).display = (gist_visible or show_gist_input) and not pending.gist_id
        self._render_guide_status()
        self.transition_to(SetupStage.GUIDE)
        self._render_guide_buttons(route)
        if self.aggregate.candidate.config is not None:
            self.verify_server_config()

    def _route_from_pending(self, pending: PendingSetup) -> SetupRoute:
        resolved = self.aggregate.resolved_key
        assert resolved is not None
        key_plan = (
            ExistingSSHKey(info=resolved.info, managed=resolved.managed)
            if isinstance(resolved, ResolvedSSHKey)
            else ExistingGPGKey(info=resolved.info, managed=resolved.managed)
        )
        return SetupRoute(
            route_id=pending.route_id,
            title="Continue setup",
            detail=RESUME_COPY,
            primary_label="Check now",
            secondary_label="Choose another method",
            publish_method=pending.publish_method,
            key_kind=pending.key_kind,
            key_plan=key_plan,
            needs_email=pending.publish_method is PublishMethod.OPENPGP,
            automated=pending.publish_method in (
                PublishMethod.GIST_AUTO,
                PublishMethod.GITHUB_SSH,
                PublishMethod.GITHUB_GPG,
            ),
        )

    def _stage_pending_candidate(self, pending: PendingSetup) -> None:
        resolved = self.aggregate.resolved_key
        assert resolved is not None
        match pending.publish_method:
            case PublishMethod.GIST_AUTO | PublishMethod.GIST_MANUAL if pending.gist_id:
                config: Config = (
                    GistConfig(
                        contributor_id=ContributorId(pending.username),
                        key_path=resolved.info.path,
                        gist_id=pending.gist_id,
                    )
                    if isinstance(resolved, ResolvedSSHKey)
                    else GistGPGConfig(
                        contributor_id=ContributorId(pending.username),
                        fpr=resolved.info.fpr,
                        gist_id=pending.gist_id,
                    )
                )
                self.aggregate.candidate.stage(
                    config, "GitHub gist", f"@{pending.username} · gist {pending.gist_id[:8]}",
                )
                self.aggregate.guide.public_key_found = True
            case PublishMethod.GITHUB_SSH:
                assert isinstance(resolved, ResolvedSSHKey)
                self.aggregate.candidate.stage(
                    SSHConfig(
                        contributor_id=ContributorId(pending.username),
                        key_path=resolved.info.path,
                    ),
                    "GitHub SSH keys",
                    f"@{pending.username}",
                )
            case PublishMethod.GITHUB_GPG:
                assert isinstance(resolved, ResolvedGPGKey)
                self.aggregate.candidate.stage(
                    GPGConfig(
                        contributor_type="github",
                        contributor_id=ContributorId(pending.username),
                        fpr=resolved.info.fpr,
                    ),
                    "GitHub GPG keys",
                    f"@{pending.username}",
                )
            case PublishMethod.OPENPGP:
                assert isinstance(resolved, ResolvedGPGKey)
                self.aggregate.candidate.stage(
                    GPGConfig(
                        contributor_type="gpg",
                        contributor_id=ContributorId(resolved.info.fpr),
                        fpr=resolved.info.fpr,
                    ),
                    "keys.openpgp.org",
                    f"GPG {resolved.info.fpr[-8:]}",
                )
            case _:
                pass

    @staticmethod
    def _rehydrate_ssh_info(key_path: Path) -> SSHKeyInfo:
        return KeyDiscovery.ssh_key_info(key_path) or SSHKeyInfo(
            path=key_path, algorithm="", comment="",
        )

    @staticmethod
    def _rehydrate_gpg_info(fpr: str, fallback_email: str) -> GPGKeyInfo:
        return GPGKeyInfo(fpr=fpr, email=fallback_email, algo="")

    def _enter_settings_for_saved_config(self) -> None:
        location, lookup = self._derive_location(self.state.config)
        self._set_done_branch(location, lookup)
        self.transition_to(SetupStage.SETTINGS)

    def _set_done_branch(self, location: str, lookup: str) -> None:
        with suppress(NoMatches):
            branch = self.query_one("#done-branch", DoneBranch)
            branch.public_location = location
            branch.lookup_value = lookup

    def _derive_location(self, config: Config | None) -> tuple[str, str]:
        match config:
            case SSHConfig(contributor_id=cid):
                return "GitHub SSH keys", f"@{cid}"
            case GistConfig(contributor_id=cid, gist_id=gid):
                return "GitHub gist", f"@{cid} · gist {gid[:8]}"
            case GistGPGConfig(contributor_id=cid, gist_id=gid):
                return "GitHub gist", f"@{cid} · gist {gid[:8]}"
            case GPGConfig(contributor_type="github", contributor_id=cid):
                return "GitHub GPG keys", f"@{cid}"
            case GPGConfig(contributor_type="gpg", fpr=fpr):
                return "keys.openpgp.org", f"GPG {fpr[-8:]}"
            case _:
                return "unknown", ""

    def _tick_pending(self) -> None:
        if self.current_stage is SetupStage.GUIDE:
            self._render_guide_status()

    def _poll_due(self) -> None:
        if self.current_stage not in (
            SetupStage.SETTINGS, SetupStage.GUIDE, SetupStage.WORKING,
        ):
            return
        if self.verify_worker is not None and self.verify_worker.is_running:
            return
        if not self.aggregate.verification_poll.due(monotonic()):
            return
        self.aggregate.verification_poll.clear()
        self.verify_server_config()

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
            target = self.aggregate.candidate.config or self.state.config
            if target is None:
                return
            try:
                result = await Uploader().probe_credentials(target)
            except httpx.HTTPError as e:
                result = AuthUnreachable(detail=str(e))
            self._on_verify_result(result)
        finally:
            self.verify_worker = None

    def _on_verify_result(self, result: AuthResult) -> None:
        match result:
            case AuthOk():
                for step in self.aggregate.working.steps:
                    if step.state is WorkStepState.RUNNING:
                        step.state = WorkStepState.SUCCESS
                self._render_working()
                self.aggregate.verification_poll.clear()
                self.aggregate.guide.public_key_found = True
                self.aggregate.guide.server_verified = True
                self.aggregate.guide.last_error = ""
                self.aggregate.pending = None
                self.state.pending_setup = None
                candidate = self.aggregate.candidate
                if candidate.config is not None:
                    self.state.config = candidate.config
                derived = self._derive_location(self.state.config)
                location = candidate.location or derived[0]
                lookup = candidate.lookup or derived[1]
                candidate.clear()
                self.state.save()
                self._set_done_branch(location, lookup)
                if self.current_stage is not SetupStage.SETTINGS:
                    self.transition_to(SetupStage.SETTINGS)
            case AuthUnauthorized():
                if monotonic() - self.aggregate.verification_poll.started_at < PENDING_PROPAGATION_WINDOW_SECONDS:
                    if (
                        self.aggregate.pending is None
                        or self.aggregate.pending.last_status not in (
                            PendingSetupStatus.OPENPGP_EMAIL_SENT,
                            PendingSetupStatus.MANUAL_OPENPGP_UPLOAD,
                        )
                    ):
                        self._update_pending(PendingSetupStatus.VERIFY_PENDING)
                    self.aggregate.verification_poll.schedule_next(monotonic())
                    self.aggregate.guide.last_checked_at = monotonic()
                    if self.current_stage is SetupStage.GUIDE:
                        self._render_guide_status()
                    if self.current_stage is SetupStage.WORKING:
                        self._render_working()
                else:
                    self.aggregate.verification_poll.clear()
                    if (
                        self.selected_route is not None
                        and self.selected_route.publish_method is PublishMethod.GIST_MANUAL
                        and self.aggregate.candidate.config is None
                    ):
                        self.aggregate.guide.last_error = MANUAL_GIST_NOT_FOUND
                        self._update_status("guide-error", MANUAL_GIST_NOT_FOUND, Tone.WARNING)
                        self._update_pending(PendingSetupStatus.GIST_NOT_FOUND, MANUAL_GIST_NOT_FOUND)
                        return
                    self.aggregate.guide.last_error = "sentiments.cc still couldn't verify the public key."
                    self._update_pending(
                        PendingSetupStatus.VERIFY_UNAUTHORIZED,
                        self.aggregate.guide.last_error,
                    )
                    self._enter_fix(self.aggregate.guide.last_error)
            case AuthUnreachable() | AuthServerError():
                if (
                    self.aggregate.pending is None
                    or self.aggregate.pending.last_status not in (
                        PendingSetupStatus.OPENPGP_EMAIL_SENT,
                        PendingSetupStatus.MANUAL_OPENPGP_UPLOAD,
                    )
                ):
                    self._update_pending(PendingSetupStatus.NETWORK_PENDING)
                self.aggregate.verification_poll.schedule_next(monotonic())
                self.aggregate.guide.last_checked_at = monotonic()
                if self.current_stage is SetupStage.GUIDE:
                    self._render_guide_status()
                if self.current_stage is SetupStage.WORKING:
                    self._render_working()

    def _enter_fix(self, error: str) -> None:
        self.aggregate.fix.last_error = error
        with suppress(NoMatches):
            self.query_one("#fix-error", Static).update(f"Last error: {Sanitizer.error(error)}")
        self.transition_to(SetupStage.FIX)

    @on(Button.Pressed, "#fix-retry")
    async def on_fix_retry(self) -> None:
        if self.selected_route is None:
            return
        self.aggregate.verification_poll.restart(monotonic())
        if self.selected_route.automated:
            await self._enter_working(self.selected_route)
        else:
            await self._enter_guide(self.selected_route)

    @on(Button.Pressed, "#fix-back-guide")
    async def on_fix_back_guide(self) -> None:
        if self.selected_route is None:
            return
        await self._enter_guide(self.selected_route)

    @on(Button.Pressed, "#fix-redo")
    async def on_fix_redo(self) -> None:
        self._clear_pending_candidate()
        await self._enter_propose()

    def _clear_pending_candidate(self) -> None:
        self.aggregate.candidate.clear()
        self.aggregate.pending = None
        self.aggregate.resolved_key = None
        if self.state.pending_setup is not None:
            self.state.pending_setup = None
            self.state.save()

    @on(Button.Pressed, "#fix-open-issue")
    def on_fix_open_issue(self) -> None:
        route = self.selected_route
        location = (
            PUBLIC_LOCATION_LABEL.get(route.publish_method, "unknown")
            if route and route.publish_method
            else "unknown"
        )
        Browser.open(IssueUrl.build(
            route.route_id.value if route else "",
            location,
            self.aggregate.fix.last_error or "no detail",
        ))

    @on(Button.Pressed, "#done-btn")
    def on_done(self) -> None:
        self.dismiss(True)

    def _update_status(self, widget_id: str, text: str, tone: Tone = Tone.MUTED) -> None:
        with suppress(NoMatches):
            widget = self.query_one(f"#{widget_id}", Static)
            for member in Tone:
                widget.remove_class(member.value)
            widget.add_class(tone.value)
            widget.update(text)

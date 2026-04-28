from __future__ import annotations

from typing import TYPE_CHECKING

import anyio
import anyio.to_thread
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Input, Static

from cc_sentiment.tui.screens.setup.copy import (
    USERNAME_ERROR_EMPTY,
    USERNAME_ERROR_NOT_FOUND,
    USERNAME_ERROR_UNREACHABLE,
    USERNAME_NO_GITHUB_LINK,
    USERNAME_PLACEHOLDER,
    USERNAME_SKIP_GPG_ONLY,
    WELCOME_BODY,
    WELCOME_CHECKING,
    WELCOME_CTA,
    WELCOME_TITLE,
)
from cc_sentiment.tui.setup_helpers import IdentityProbe
from cc_sentiment.tui.setup_state import (
    SetupStage,
    Tone,
    UsernameSource,
)
from cc_sentiment.tui.widgets import Card, LinkRow, PendingStatus

if TYPE_CHECKING:
    from cc_sentiment.tui.screens.setup.screen import SetupScreen  # noqa: F401


class WelcomeStageMixin:
    def _compose_welcome(self: "SetupScreen") -> ComposeResult:
        with Vertical(id=SetupStage.WELCOME.value):
            yield Card(
                Static(WELCOME_BODY, classes="copy welcome-body"),
                Static("", id="welcome-username-prompt", classes="copy welcome-username-prompt"),
                Input(placeholder=USERNAME_PLACEHOLDER, id="welcome-username-input"),
                Static("", id="welcome-username-status", classes="status-line"),
                PendingStatus(WELCOME_CHECKING, id="welcome-checking"),
                Static("", id="welcome-status", classes="status-line"),
                Vertical(
                    Button(WELCOME_CTA, id="welcome-go", variant="primary"),
                    classes="welcome-actions",
                ),
                LinkRow(USERNAME_NO_GITHUB_LINK, id="welcome-no-github"),
                title=WELCOME_TITLE,
                id="welcome-card",
            )

    def _welcome_on_mount(self: "SetupScreen") -> None:
        self.query_one("#welcome-username-prompt", Static).display = False
        self.query_one("#welcome-username-input", Input).display = False
        self.query_one("#welcome-username-status", Static).display = False
        self.query_one("#welcome-checking", PendingStatus).display = False
        self.query_one("#welcome-no-github", LinkRow).display = False

    def _show_welcome_busy(self: "SetupScreen", busy: bool) -> None:
        self.query_one("#welcome-checking", PendingStatus).display = busy
        self.query_one("#welcome-go", Button).display = not busy

    def _show_inline_username_prompt(self: "SetupScreen", prefix: str = "") -> None:
        prompt = self.query_one("#welcome-username-prompt", Static)
        prompt.update(prefix.strip())
        prompt.display = bool(prefix.strip())
        self.query_one("#welcome-username-input", Input).display = True
        self.query_one("#welcome-username-status", Static).display = True
        self.query_one("#welcome-checking", PendingStatus).display = False
        self.query_one("#welcome-no-github", LinkRow).display = True
        go = self.query_one("#welcome-go", Button)
        go.label = "Continue"
        go.display = True
        self.call_after_refresh(
            lambda: self._focus_widget(self.query_one("#welcome-username-input", Input))
        )

    def _hide_inline_username_prompt(self: "SetupScreen") -> None:
        self.query_one("#welcome-username-prompt", Static).display = False
        self.query_one("#welcome-username-input", Input).display = False
        self.query_one("#welcome-username-status", Static).display = False
        self.query_one("#welcome-no-github", LinkRow).display = False
        go = self.query_one("#welcome-go", Button)
        go.label = WELCOME_CTA

    @on(Button.Pressed, "#welcome-go")
    async def on_welcome_go(self: "SetupScreen") -> None:
        username_input = self.query_one("#welcome-username-input", Input)
        if username_input.display:
            await self._submit_welcome_username(username_input.value.strip())
            return
        self._show_welcome_busy(True)
        await self.start_setup_flow()

    async def _submit_welcome_username(self: "SetupScreen", username: str) -> None:
        if not username:
            self._update_status("welcome-username-status", USERNAME_ERROR_EMPTY, Tone.ERROR)
            return
        self._update_status("welcome-username-status", f"Validating {username}…")
        match await anyio.to_thread.run_sync(IdentityProbe.validate_username, username):
            case "not-found":
                self._update_status(
                    "welcome-username-status",
                    USERNAME_ERROR_NOT_FOUND.format(user=username),
                    Tone.ERROR,
                )
                return
            case "unreachable":
                self._update_status(
                    "welcome-username-status",
                    USERNAME_ERROR_UNREACHABLE,
                    Tone.ERROR,
                )
                return
        self.github_lookup_allowed = True
        await self._set_username(username, UsernameSource.USER)
        self.state.github_username = username
        await anyio.to_thread.run_sync(self.state.save)
        self._hide_inline_username_prompt()
        self._show_welcome_busy(True)
        await self._continue_after_username()

    @on(LinkRow.Pressed, "#welcome-no-github")
    async def on_welcome_no_github(self: "SetupScreen") -> None:
        self.github_lookup_allowed = False
        await self._set_username("", UsernameSource.NONE)
        self._hide_inline_username_prompt()
        self._update_status("welcome-status", USERNAME_SKIP_GPG_ONLY, Tone.MUTED)
        if not self.discovery.capabilities.has_gpg:
            self._render_blocked(self.discovery)
            self.transition_to(SetupStage.BLOCKED)
            return
        await self._enter_alternate()

    async def _continue_after_username(self: "SetupScreen") -> None:
        await self._silent_replan()

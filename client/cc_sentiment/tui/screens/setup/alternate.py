from __future__ import annotations

from contextlib import suppress
from time import monotonic
from typing import TYPE_CHECKING

import anyio
import anyio.to_thread
import httpx
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Input, Static

from cc_sentiment.models import ContributorId, GPGConfig, PendingSetupStatus
from cc_sentiment.signing import GPGBackend, KeyDiscovery
from cc_sentiment.tui.screens.setup.copy import (
    ALTERNATE_BODY,
    ALTERNATE_CTA,
    ALTERNATE_TITLE,
    OPENPGP_AFTER_SEND,
    OPENPGP_EMAIL_ERROR_EMPTY,
    OPENPGP_NO_EMAIL_NEEDED,
)
from cc_sentiment.tui.setup_helpers import Sanitizer, SetupRoutePlanner
from cc_sentiment.tui.setup_state import (
    PublishMethod,
    ResolvedGPGKey,
    SetupRoute,
    SetupStage,
    Tone,
)
from cc_sentiment.tui.widgets import Card

if TYPE_CHECKING:
    from cc_sentiment.tui.screens.setup.screen import SetupScreen  # noqa: F401


class AlternateStageMixin:
    def _compose_alternate(self: "SetupScreen") -> ComposeResult:
        with Vertical(id=SetupStage.ALTERNATE.value):
            yield Card(
                Static(ALTERNATE_BODY, classes="copy"),
                Input(placeholder="email@example.com", id="alternate-email"),
                Static("", id="alternate-status", classes="status-line"),
                Vertical(
                    Button(ALTERNATE_CTA, id="alternate-go", variant="primary"),
                    classes="alternate-actions",
                ),
                title=ALTERNATE_TITLE,
                id="alternate-card",
            )

    def _alternate_on_mount(self: "SetupScreen") -> None:
        self.query_one("#alternate-status", Static).display = False

    async def _enter_alternate(self: "SetupScreen") -> None:
        self.selected_route = SetupRoutePlanner.alternate_openpgp_route()
        identity = self.discovery.identity
        with suppress(NoMatches):
            self.query_one("#alternate-email", Input).value = (
                identity.github_email if identity.email_usable else ""
            )
        self.transition_to(SetupStage.ALTERNATE)

    @on(Button.Pressed, "#alternate-go")
    async def on_alternate_go(self: "SetupScreen") -> None:
        if (route := self.selected_route) is None or route.publish_method is not PublishMethod.OPENPGP:
            return
        identity = self.discovery.identity
        email = (
            identity.github_email
            if identity.email_usable and identity.github_email
            else self.query_one("#alternate-email", Input).value.strip()
        )
        if not email:
            self._update_status("alternate-status", OPENPGP_EMAIL_ERROR_EMPTY, Tone.ERROR)
            return
        button = self.query_one("#alternate-go", Button)
        button.disabled = True
        try:
            await self._openpgp_send(route, email)
        finally:
            button.disabled = False

    async def _openpgp_send(self: "SetupScreen", route: SetupRoute, email: str) -> None:
        resolved = self._resolve_key(route)
        assert isinstance(resolved, ResolvedGPGKey)
        armor = await anyio.to_thread.run_sync(
            lambda: GPGBackend(fpr=resolved.info.fpr).public_key_text()
        )
        try:
            token, statuses = await anyio.to_thread.run_sync(
                KeyDiscovery.upload_openpgp_key, armor,
            )
            emails = [
                addr for addr, status in statuses.items() if status == "unpublished"
            ] or ([email] if email else [])
            if emails:
                await anyio.to_thread.run_sync(
                    KeyDiscovery.request_openpgp_verify, token, emails,
                )
        except httpx.HTTPError as exc:
            self._update_status(
                "alternate-status",
                f"Couldn't reach keys.openpgp.org: {Sanitizer.error(str(exc))}",
                Tone.ERROR,
            )
            return
        self.aggregate.candidate.stage(
            GPGConfig(
                contributor_type="gpg",
                contributor_id=ContributorId(resolved.info.fpr),
                fpr=resolved.info.fpr,
            ),
            "keys.openpgp.org",
            f"GPG {resolved.info.fpr[-8:]}",
        )
        self._persist_pending(
            route, "keys.openpgp.org", "", PendingSetupStatus.OPENPGP_EMAIL_SENT,
            email=email,
        )
        self._update_status(
            "alternate-status",
            OPENPGP_AFTER_SEND.format(email=", ".join(emails)) if emails else OPENPGP_NO_EMAIL_NEEDED,
            Tone.SUCCESS,
        )
        self.query_one("#alternate-go", Button).label = "Reopen verification"
        self.aggregate.verification_poll.restart(monotonic())
        self.verify_server_config()

from __future__ import annotations

from contextlib import suppress
from time import monotonic
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Static

from cc_sentiment.tui.legacy.setup.copy import (
    TROUBLE_BODY,
    TROUBLE_KEEP_WATCHING,
    TROUBLE_TITLE,
    TROUBLE_TRY_DIFFERENT,
)
from cc_sentiment.tui.legacy.setup_helpers import Sanitizer
from cc_sentiment.tui.legacy.setup_state import PublishMethod, SetupStage
from cc_sentiment.tui.widgets import Card

if TYPE_CHECKING:
    from cc_sentiment.tui.legacy.setup.screen import SetupScreen  # noqa: F401


class TroubleStageMixin:
    def _compose_trouble(self: "SetupScreen") -> ComposeResult:
        with Vertical(id=SetupStage.TROUBLE.value):
            yield Card(
                Static(TROUBLE_BODY, classes="copy"),
                Static("", id="trouble-error", classes="status-line warning"),
                Vertical(
                    Button(TROUBLE_KEEP_WATCHING, id="trouble-keep", variant="primary"),
                    Button(TROUBLE_TRY_DIFFERENT, id="trouble-redo", variant="default"),
                    classes="trouble-actions",
                ),
                title=TROUBLE_TITLE,
                id="trouble-card",
            )

    def _enter_trouble(self: "SetupScreen", error: str) -> None:
        with suppress(NoMatches):
            self.query_one("#trouble-error", Static).update(
                f"Last issue: {Sanitizer.error(error)}" if error else ""
            )
        self.transition_to(SetupStage.TROUBLE)

    @on(Button.Pressed, "#trouble-keep")
    async def on_trouble_keep(self: "SetupScreen") -> None:
        if (route := self.selected_route) is None:
            return
        self.aggregate.verification_poll.restart(monotonic())
        match route.publish_method:
            case PublishMethod.GIST_AUTO:
                await self._enter_working(route)
            case PublishMethod.GIST_MANUAL:
                await self._enter_publish(route)
            case PublishMethod.OPENPGP:
                self.transition_to(SetupStage.ALTERNATE)
                self.verify_server_config()

    @on(Button.Pressed, "#trouble-redo")
    async def on_trouble_redo(self: "SetupScreen") -> None:
        self._clear_pending_candidate()
        await self._silent_replan()

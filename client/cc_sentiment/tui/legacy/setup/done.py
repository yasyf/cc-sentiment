from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Button

from cc_sentiment.models import GistConfig, GistGPGConfig, GPGConfig, SSHConfig
from cc_sentiment.tui.legacy.setup_state import SetupStage
from cc_sentiment.tui.legacy.widgets import DoneBranch

if TYPE_CHECKING:
    from cc_sentiment.tui.legacy.setup.screen import Config, SetupScreen  # noqa: F401


class DoneStageMixin:
    def _compose_done(self: "SetupScreen") -> ComposeResult:
        with Vertical(id=SetupStage.DONE.value):
            yield DoneBranch(id="done-branch")

    def _enter_settings_for_saved_config(self: "SetupScreen") -> None:
        self._set_done_branch(self._derive_verification(self.state.config))
        self.transition_to(SetupStage.DONE)

    def _set_done_branch(self: "SetupScreen", verification: str) -> None:
        with suppress(NoMatches):
            branch = self.query_one("#done-branch", DoneBranch)
            branch.verification = verification

    @staticmethod
    def _derive_verification(config: Config | None) -> str:
        match config:
            case SSHConfig(contributor_id=cid):
                return f"Verification: @{cid} on GitHub"
            case GistConfig(contributor_id=cid):
                return f"Verification: @{cid} via public gist"
            case GistGPGConfig(contributor_id=cid):
                return f"Verification: @{cid} via public gist"
            case GPGConfig(contributor_type="github", contributor_id=cid):
                return f"Verification: @{cid} on GitHub"
            case GPGConfig(contributor_type="gpg", fpr=fpr):
                return f"Verification: GPG {fpr[-8:]}"
            case _:
                return "Verification: ready"

    @on(Button.Pressed, "#done-btn")
    def on_done(self: "SetupScreen") -> None:
        self.dismiss(True)

from __future__ import annotations

from pathlib import Path

import sentry_sdk
from textual.app import App

from cc_sentiment.models import AppState
from cc_sentiment.tui.dashboard.screen import DashboardScreen
from cc_sentiment.tui.legacy import SetupScreen


class CCSentimentApp(App[None]):
    CSS = """
    Dialog { background: $surface; }
    StatShareScreen { background: $background 60%; }
    """

    def __init__(
        self,
        state: AppState,
        model_repo: str | None = None,
        db_path: Path | None = None,
        setup_only: bool = False,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self.theme = "tokyo-night"
        self.state = state
        self.setup_only = setup_only
        self.dashboard = DashboardScreen(
            state=state, model_repo=model_repo, db_path=db_path, debug=debug,
        )

    def _handle_exception(self, error: Exception) -> None:
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("source", "textual")
            sentry_sdk.capture_exception(error)
        super()._handle_exception(error)

    async def on_mount(self) -> None:
        self.title = "cc-sentiment"
        if self.setup_only:
            await self.push_screen(SetupScreen(self.state), lambda _: self.exit())
            return
        await self.push_screen(self.dashboard)

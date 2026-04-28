from __future__ import annotations

import subprocess
from contextlib import suppress
from time import monotonic
from typing import TYPE_CHECKING

import httpx
from textual import work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Static

from cc_sentiment.models import (
    ContributorId,
    GistConfig,
    GistGPGConfig,
    PendingSetupStatus,
)
from cc_sentiment.signing import KeyDiscovery
from cc_sentiment.tui.screens.setup.copy import WORKING_BODY, WORKING_TITLE
from cc_sentiment.tui.setup_helpers import GistDiscovery, GistRef, Sanitizer
from cc_sentiment.tui.setup_state import (
    GenerateGPGKey,
    GenerateSSHKey,
    PublishMethod,
    ResolvedSSHKey,
    SetupRoute,
    SetupStage,
)
from cc_sentiment.tui.widgets import Card, PendingStatus

if TYPE_CHECKING:
    from cc_sentiment.tui.screens.setup.screen import Config, SetupScreen  # noqa: F401


class WorkingStageMixin:
    def _compose_working(self: "SetupScreen") -> ComposeResult:
        with Vertical(id=SetupStage.WORKING.value):
            yield Card(
                PendingStatus(WORKING_BODY, id="working-spinner"),
                Static("", id="working-detail", classes="copy"),
                title=WORKING_TITLE,
                id="working-card",
            )

    def _working_on_mount(self: "SetupScreen") -> None:
        self.query_one("#working-detail", Static).display = False

    async def _enter_working(self: "SetupScreen", route: SetupRoute) -> None:
        assert route.publish_method is PublishMethod.GIST_AUTO
        self.selected_route = route
        self.aggregate.resolved_key = None
        self.transition_to(SetupStage.WORKING)
        self.aggregate.working.worker = self.run_working(route)

    def _set_working_label(self: "SetupScreen", text: str) -> None:
        with suppress(NoMatches):
            self.query_one("#working-spinner", PendingStatus).label = text

    @work(thread=True)
    def run_working(self: "SetupScreen", route: SetupRoute) -> None:
        call = self.app.call_from_thread
        try:
            self._execute_gist_auto(route, call)
        except (
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            FileNotFoundError,
            OSError,
            AssertionError,
            httpx.HTTPError,
        ) as exc:
            call(self._on_working_failure, Sanitizer.error(str(exc)))

    def _execute_gist_auto(self: "SetupScreen", route: SetupRoute, call) -> None:
        if isinstance(route.key_plan, GenerateSSHKey | GenerateGPGKey):
            call(self._set_working_label, "Creating cc-sentiment key…")
        self._resolve_key(route)
        resolved = self.aggregate.resolved_key
        assert resolved is not None
        call(self._set_working_label, "Creating GitHub gist…")
        pub_text = self._public_key_text(resolved)
        gist_id = KeyDiscovery.create_gist_from_text(pub_text)
        username = self.discovery.identity.github_username
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
        metadata = GistDiscovery.fetch_metadata(GistRef(owner=username, gist_id=gist_id))
        if metadata is None or not any(
            pub_text.strip() in (content or "")
            for content in metadata.file_contents.values()
        ):
            raise AssertionError("created gist is not visible yet")
        call(self._set_working_label, "Verifying upload…")
        call(self._on_working_complete, route, config, gist_id, username)

    def _on_working_complete(
        self: "SetupScreen",
        route: SetupRoute,
        config: Config,
        gist_id: str,
        username: str,
    ) -> None:
        self.aggregate.candidate.stage(
            config, "GitHub gist", f"@{username} · gist {gist_id[:8]}",
        )
        self._persist_pending(
            route, "GitHub gist", gist_id, PendingSetupStatus.VERIFY_PENDING,
        )
        self.aggregate.verification_poll.restart(monotonic())
        self.verify_server_config()

    def _on_working_failure(self: "SetupScreen", error: str) -> None:
        self._enter_trouble(error)

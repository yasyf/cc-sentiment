from __future__ import annotations

from contextlib import suppress
from time import monotonic
from typing import TYPE_CHECKING

import anyio
import anyio.to_thread
import httpx
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Static

from cc_sentiment.models import (
    ContributorId,
    GistConfig,
    GistGPGConfig,
    PendingSetupStatus,
)
from cc_sentiment.tui.legacy.setup.copy import (
    MANUAL_GIST_INTRO_NO_CLIPBOARD,
    PUBLISH_BODY,
    PUBLISH_COPY_AGAIN_LABEL,
    PUBLISH_KEY_PREVIEW_TITLE,
    PUBLISH_NO_GITHUB_LINK,
    PUBLISH_OPEN_LABEL,
    PUBLISH_TITLE,
    PUBLISH_WATCH_LABEL,
)
from cc_sentiment.tui.legacy.setup_helpers import (
    GIST_NEW_URL,
    GistDiscovery,
    GistRef,
)
from cc_sentiment.tui.legacy.system import Browser, Clipboard
from cc_sentiment.tui.legacy.setup_state import (
    PENDING_PROPAGATION_WINDOW_SECONDS,
    PublishMethod,
    ResolvedKey,
    ResolvedSSHKey,
    SetupRoute,
    SetupStage,
    UsernameSource,
)
from cc_sentiment.tui.widgets import Card, LinkRow, PendingStatus

if TYPE_CHECKING:
    from cc_sentiment.tui.legacy.setup.screen import Config, SetupScreen  # noqa: F401


class PublishStageMixin:
    def _compose_publish(self: "SetupScreen") -> ComposeResult:
        with Vertical(id=SetupStage.PUBLISH.value):
            yield Card(
                Static(PUBLISH_BODY, classes="copy publish-body"),
                Card(
                    Static("", id="publish-key-preview", classes="publish-key-text"),
                    title=PUBLISH_KEY_PREVIEW_TITLE,
                    id="publish-key-card",
                ),
                Static("", id="publish-fallback-key", classes="copy publish-fallback"),
                Vertical(
                    Button(PUBLISH_OPEN_LABEL, id="publish-open", variant="primary"),
                    classes="publish-actions",
                ),
                LinkRow(PUBLISH_COPY_AGAIN_LABEL, id="publish-copy-again"),
                LinkRow(PUBLISH_NO_GITHUB_LINK, id="publish-no-github"),
                PendingStatus(PUBLISH_WATCH_LABEL, id="publish-watch"),
                title=PUBLISH_TITLE,
                id="publish-card",
            )

    def _publish_on_mount(self: "SetupScreen") -> None:
        self.query_one("#publish-fallback-key", Static).display = False

    async def _enter_publish(self: "SetupScreen", route: SetupRoute) -> None:
        assert route.publish_method is PublishMethod.GIST_MANUAL
        self.selected_route = route
        resolved = self._resolve_key(route)
        public_key = self._public_key_text(resolved).strip()
        with suppress(NoMatches):
            self.query_one("#publish-key-preview", Static).update(public_key)
        clipboard_ok = Clipboard.copy(public_key)
        with suppress(NoMatches):
            fallback = self.query_one("#publish-fallback-key", Static)
            if not clipboard_ok:
                fallback.update(f"{MANUAL_GIST_INTRO_NO_CLIPBOARD}\n\n{public_key}")
                fallback.display = True
            else:
                fallback.display = False
        Browser.open(GIST_NEW_URL)
        self._persist_pending(
            route, "GitHub gist", "", PendingSetupStatus.AWAITING_USER,
        )
        self.transition_to(SetupStage.PUBLISH)
        self._start_gist_watcher()
        self.verify_server_config()

    def _start_gist_watcher(self: "SetupScreen") -> None:
        if self.gist_watch_worker is not None and self.gist_watch_worker.is_running:
            return
        self.gist_watch_worker = self._watch_for_gist()

    @work(group="gist-watch")
    async def _watch_for_gist(self: "SetupScreen") -> None:
        if (route := self.selected_route) is None:
            return
        if route.publish_method is not PublishMethod.GIST_MANUAL:
            return
        resolved = self.aggregate.resolved_key
        if resolved is None:
            return
        public_key = self._public_key_text(resolved).strip()
        username = self.discovery.identity.github_username or (
            self.aggregate.pending.username if self.aggregate.pending else ""
        )
        gh_authed = self.discovery.capabilities.gh_authed
        interval = 5.0 if gh_authed else 30.0
        started = monotonic()
        while (
            self.current_stage is SetupStage.PUBLISH
            and monotonic() - started < PENDING_PROPAGATION_WINDOW_SECONDS
        ):
            if username:
                ref = await self._safe_find_gist(username, public_key)
                if ref is not None and await self._stage_manual_gist(ref):
                    self.verify_server_config()
                    return
            await anyio.sleep(interval)
        if self.current_stage is SetupStage.PUBLISH:
            self._enter_trouble("")

    async def _stage_manual_gist(self: "SetupScreen", ref: GistRef) -> bool:
        resolved = self.aggregate.resolved_key
        if resolved is None:
            return False
        try:
            metadata = await anyio.to_thread.run_sync(GistDiscovery.fetch_metadata, ref)
        except httpx.HTTPError:
            return False
        if metadata is None:
            return False
        public_key = self._public_key_text(resolved).strip()
        if not any(public_key in (content or "") for content in metadata.file_contents.values()):
            return False
        config = self._gist_config_for(resolved, ref)
        self.aggregate.candidate.stage(
            config, "GitHub gist", f"@{ref.owner} · gist {ref.gist_id[:8]}",
        )
        self._update_pending(
            PendingSetupStatus.VERIFY_PENDING, "", ref.gist_id, ref.owner,
        )
        return True

    @staticmethod
    def _gist_config_for(resolved: ResolvedKey, ref: GistRef) -> "Config":
        if isinstance(resolved, ResolvedSSHKey):
            return GistConfig(
                contributor_id=ContributorId(ref.owner),
                key_path=resolved.info.path,
                gist_id=ref.gist_id,
            )
        return GistGPGConfig(
            contributor_id=ContributorId(ref.owner),
            fpr=resolved.info.fpr,
            gist_id=ref.gist_id,
        )

    @on(Button.Pressed, "#publish-open")
    def on_publish_open(self: "SetupScreen") -> None:
        Browser.open(GIST_NEW_URL)

    @on(LinkRow.Pressed, "#publish-copy-again")
    def on_publish_copy_again(self: "SetupScreen") -> None:
        if (route := self.selected_route) is None:
            return
        resolved = self._resolve_key(route)
        Clipboard.copy(self._public_key_text(resolved))

    @on(LinkRow.Pressed, "#publish-no-github")
    async def on_publish_no_github(self: "SetupScreen") -> None:
        self._clear_pending_candidate()
        self.github_lookup_allowed = False
        await self._set_username("", UsernameSource.NONE)
        await self._enter_alternate()

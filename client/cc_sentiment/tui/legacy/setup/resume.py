from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Literal

import anyio
import anyio.to_thread

from cc_sentiment.signing import KeyDiscovery
from cc_sentiment.tui.legacy.setup_helpers import DiscoveryRunner
from cc_sentiment.tui.legacy.setup_state import (
    IdentityDiscovery,
    KeyKind,
    PublishMethod,
    ResolvedGPGKey,
    ResolvedSSHKey,
    UsernameSource,
)
from cc_sentiment.upload import (
    AuthOk,
    AuthUnauthorized,
    Uploader,
)

if TYPE_CHECKING:
    from cc_sentiment.tui.legacy.setup.screen import SetupScreen  # noqa: F401


class ResumeMixin:
    async def _maybe_resume_pending(self: "SetupScreen") -> bool:
        pending_model = self.state.pending_setup
        if pending_model is None:
            return False
        pending = self._pending_from_model(pending_model)
        if pending.key_kind is KeyKind.SSH and (
            pending.key_path is None
            or KeyDiscovery.ssh_key_info(pending.key_path) is None
        ):
            self.state.pending_setup = None
            await anyio.to_thread.run_sync(self.state.save)
            return False
        self.aggregate.pending = pending
        await self._enter_resume()
        return True

    async def _verify_saved_state(
        self: "SetupScreen",
    ) -> Literal["ok", "temporary", "none", "invalid"]:
        if self.state.config is None:
            return "none"
        result = await Uploader().probe_credentials(self.state.config)
        match result:
            case AuthOk():
                self._enter_settings_for_saved_config()
                return "ok"
            case AuthUnauthorized():
                return "invalid"
            case _:
                return "temporary"

    async def _enter_resume(self: "SetupScreen") -> None:
        pending = self.aggregate.pending
        assert pending is not None
        discovered = await anyio.to_thread.run_sync(
            DiscoveryRunner.run,
            pending.username,
            bool(pending.username) or self.github_lookup_allowed,
        )
        self.aggregate.discovery = replace(
            discovered,
            identity=IdentityDiscovery(
                github_username=pending.username or discovered.identity.github_username,
                username_source=(
                    UsernameSource.SAVED
                    if pending.username
                    else discovered.identity.username_source
                ),
                github_email=pending.email or discovered.identity.github_email,
                email_source=discovered.identity.email_source,
                email_usable=bool(pending.email) or discovered.identity.email_usable,
            ),
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
        match pending.publish_method:
            case PublishMethod.GIST_MANUAL:
                await self._enter_publish(route)
            case PublishMethod.OPENPGP:
                await self._enter_alternate()
            case _:
                self.aggregate.pending = None
                self.state.pending_setup = None
                await anyio.to_thread.run_sync(self.state.save)
                await self._silent_replan()

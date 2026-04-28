from __future__ import annotations

from time import monotonic
from typing import TYPE_CHECKING

import anyio
import anyio.to_thread
import httpx

from cc_sentiment.models import (
    ContributorId,
    GistConfig,
    GistGPGConfig,
    GPGConfig,
    PendingSetupStatus,
    SSHConfig,
)
from cc_sentiment.tui.legacy.setup_helpers import GistDiscovery
from cc_sentiment.tui.legacy.setup_state import (
    PENDING_PROPAGATION_WINDOW_SECONDS,
    DiscoveryResult,
    ResolvedGPGKey,
    ResolvedSSHKey,
    SetupStage,
)
from cc_sentiment.upload import (
    AuthOk,
    AuthResult,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    Uploader,
)

if TYPE_CHECKING:
    from cc_sentiment.tui.legacy.setup.screen import Config, SetupScreen  # noqa: F401


class VerifyMixin:
    async def _auto_verify(self: "SetupScreen", result: DiscoveryResult) -> "Config | None":
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
            for ssh in result.existing_ssh:
                public_key = self._public_key_text(
                    ResolvedSSHKey(info=ssh.info, managed=ssh.managed)
                ).strip()
                ref = await self._safe_find_gist(username, public_key)
                if ref is None:
                    continue
                config = GistConfig(
                    contributor_id=ContributorId(ref.owner),
                    key_path=ssh.info.path,
                    gist_id=ref.gist_id,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    return config
            for gpg in result.existing_gpg:
                public_key = self._public_key_text(
                    ResolvedGPGKey(info=gpg.info, managed=gpg.managed)
                ).strip()
                ref = await self._safe_find_gist(username, public_key)
                if ref is None:
                    continue
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

    @staticmethod
    async def _safe_find_gist(username: str, public_key: str):
        try:
            return await anyio.to_thread.run_sync(
                GistDiscovery.find_gist_with_public_key, username, public_key,
            )
        except (httpx.HTTPError, OSError):
            return None

    def _poll_due(self: "SetupScreen") -> None:
        if self.current_stage not in (
            SetupStage.DONE,
            SetupStage.PUBLISH,
            SetupStage.WORKING,
            SetupStage.ALTERNATE,
        ):
            return
        if self.verify_worker is not None and self.verify_worker.is_running:
            return
        if not self.aggregate.verification_poll.due(monotonic()):
            return
        self.aggregate.verification_poll.clear()
        self.verify_server_config()

    def verify_server_config(self: "SetupScreen") -> None:
        if self.verify_worker is not None and self.verify_worker.is_running:
            return
        self.verify_worker = self.run_worker(
            self._verify_server_config(),
            name=f"setup-verify-{monotonic()}",
            exit_on_error=False,
        )

    async def _verify_server_config(self: "SetupScreen") -> None:
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

    def _on_verify_result(self: "SetupScreen", result: AuthResult) -> None:
        match result:
            case AuthOk():
                self.aggregate.verification_poll.clear()
                self.aggregate.pending = None
                self.state.pending_setup = None
                candidate = self.aggregate.candidate
                if candidate.config is not None:
                    self.state.config = candidate.config
                candidate.clear()
                self.state.save()
                self._set_done_branch(self._derive_verification(self.state.config))
                if self.current_stage is not SetupStage.DONE:
                    self.transition_to(SetupStage.DONE)
            case AuthUnauthorized():
                if monotonic() - self.aggregate.verification_poll.started_at < PENDING_PROPAGATION_WINDOW_SECONDS:
                    if (
                        self.aggregate.pending is None
                        or self.aggregate.pending.last_status is not PendingSetupStatus.OPENPGP_EMAIL_SENT
                    ):
                        self._update_pending(PendingSetupStatus.VERIFY_PENDING)
                    self.aggregate.verification_poll.schedule_next(monotonic())
                else:
                    self.aggregate.verification_poll.clear()
                    error = "sentiments.cc still couldn't verify this key."
                    self._update_pending(PendingSetupStatus.VERIFY_UNAUTHORIZED, error)
                    self._enter_trouble(error)
            case AuthUnreachable() | AuthServerError():
                if (
                    self.aggregate.pending is None
                    or self.aggregate.pending.last_status is not PendingSetupStatus.OPENPGP_EMAIL_SENT
                ):
                    self._update_pending(PendingSetupStatus.NETWORK_PENDING)
                self.aggregate.verification_poll.schedule_next(monotonic())

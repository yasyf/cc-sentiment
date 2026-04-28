from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from cc_sentiment.models import (
    ContributorId,
    GistConfig,
    GistGPGConfig,
    GPGConfig,
    PendingSetupModel,
    PendingSetupStatus,
)
from cc_sentiment.signing import GPGKeyInfo, KeyDiscovery, SSHKeyInfo
from cc_sentiment.tui.setup_state import (
    ExistingGPGKey,
    ExistingSSHKey,
    KeyKind,
    PendingSetup,
    PublishMethod,
    ResolvedGPGKey,
    ResolvedSSHKey,
    RouteId,
    SetupRoute,
)

if TYPE_CHECKING:
    from cc_sentiment.tui.screens.setup.screen import Config, SetupScreen  # noqa: F401


class PendingLifecycleMixin:
    def _persist_pending(
        self: "SetupScreen",
        route: SetupRoute,
        location: str,
        gist_id: str,
        status: PendingSetupStatus = PendingSetupStatus.CREATED,
        error: str = "",
        email: str = "",
    ) -> None:
        resolved = self._resolve_key(route)
        username = self.discovery.identity.github_username
        match resolved:
            case ResolvedSSHKey(info=info, managed=managed):
                key_path: Path | None = info.path
                key_fpr: str | None = None
                key_kind = KeyKind.SSH
                key_managed = managed
            case ResolvedGPGKey(info=info, managed=managed):
                key_path = None
                key_fpr = info.fpr
                key_kind = KeyKind.GPG
                key_managed = managed
        now = monotonic()
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
            started_at=self.aggregate.pending.started_at if self.aggregate.pending else now,
            updated_at=now,
        )
        self.aggregate.pending = pending
        self._save_pending(pending)

    def _save_pending(self: "SetupScreen", pending: PendingSetup) -> None:
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
        self: "SetupScreen",
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

    def _clear_pending_candidate(self: "SetupScreen") -> None:
        self.aggregate.candidate.clear()
        self.aggregate.pending = None
        self.aggregate.resolved_key = None
        if self.state.pending_setup is not None:
            self.state.pending_setup = None
            self.state.save()

    def _route_from_pending(self: "SetupScreen", pending: PendingSetup) -> SetupRoute:
        resolved = self.aggregate.resolved_key
        assert resolved is not None
        key_plan = (
            ExistingSSHKey(info=resolved.info, managed=resolved.managed)
            if isinstance(resolved, ResolvedSSHKey)
            else ExistingGPGKey(info=resolved.info, managed=resolved.managed)
        )
        return SetupRoute(
            route_id=pending.route_id,
            publish_method=pending.publish_method,
            key_kind=pending.key_kind,
            key_plan=key_plan,
        )

    def _stage_pending_candidate(self: "SetupScreen", pending: PendingSetup) -> None:
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
                    config,
                    pending.public_location or "GitHub gist",
                    f"@{pending.username} · gist {pending.gist_id[:8]}",
                )
            case PublishMethod.OPENPGP:
                assert isinstance(resolved, ResolvedGPGKey)
                self.aggregate.candidate.stage(
                    GPGConfig(
                        contributor_type="gpg",
                        contributor_id=ContributorId(resolved.info.fpr),
                        fpr=resolved.info.fpr,
                    ),
                    pending.public_location or "keys.openpgp.org",
                    f"GPG {resolved.info.fpr[-8:]}",
                )
            case _:
                pass

    @staticmethod
    def _rehydrate_ssh_info(key_path: Path) -> SSHKeyInfo:
        info = KeyDiscovery.ssh_key_info(key_path)
        assert info is not None
        return info

    @staticmethod
    def _rehydrate_gpg_info(fpr: str, fallback_email: str) -> GPGKeyInfo:
        return GPGKeyInfo(fpr=fpr, email=fallback_email, algo="")

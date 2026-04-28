from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from textual.worker import Worker

from cc_sentiment.models import (
    GistGPGConfig,
    GistConfig,
    GPGConfig,
    PendingSetupStatus,
    SSHConfig,
)
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo

CandidateConfig = SSHConfig | GPGConfig | GistConfig | GistGPGConfig

PENDING_PROPAGATION_WINDOW_SECONDS = 600.0
PENDING_RETRY_SECONDS = 10.0


class SetupStage(StrEnum):
    WELCOME = "step-welcome"
    ALTERNATE = "step-alternate"
    WORKING = "step-working"
    PUBLISH = "step-publish"
    BLOCKED = "step-blocked"
    TROUBLE = "step-trouble"
    DONE = "step-done"


class Tone(StrEnum):
    MUTED = "muted"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class RouteId(StrEnum):
    MANAGED_SSH_GIST = "managed-ssh-gist"
    MANAGED_GPG_OPENPGP = "managed-gpg-openpgp"
    MANAGED_SSH_MANUAL_GIST = "managed-ssh-manual-gist"


class SetupIntervention(StrEnum):
    NONE = "none"
    USERNAME = "username"
    BLOCKED = "blocked"


class PublishMethod(StrEnum):
    GIST_AUTO = "gist-auto"
    GIST_MANUAL = "gist-manual"
    OPENPGP = "openpgp"


class KeyKind(StrEnum):
    SSH = "ssh"
    GPG = "gpg"


class EmailSource(StrEnum):
    NONE = "none"
    GH = "gh"
    COMMIT = "commit"
    USER = "user"


class UsernameSource(StrEnum):
    NONE = "none"
    GH = "gh"
    SAVED = "saved"
    USER = "user"


@dataclass(frozen=True, slots=True)
class ToolCapabilities:
    has_gh: bool = False
    gh_authed: bool = False
    has_gpg: bool = False
    has_ssh_keygen: bool = False
    has_brew: bool = False
    can_clipboard: bool = False
    can_open_browser: bool = True


@dataclass(frozen=True, slots=True)
class IdentityDiscovery:
    github_username: str = ""
    username_source: UsernameSource = UsernameSource.NONE
    github_email: str = ""
    email_source: EmailSource = EmailSource.NONE
    email_usable: bool = False


@dataclass(frozen=True, slots=True)
class ExistingSSHKey:
    info: SSHKeyInfo
    managed: bool = False


@dataclass(frozen=True, slots=True)
class ExistingGPGKey:
    info: GPGKeyInfo
    managed: bool = False


@dataclass(frozen=True, slots=True)
class GenerateSSHKey:
    pass


@dataclass(frozen=True, slots=True)
class GenerateGPGKey:
    pass


KeyPlan = ExistingSSHKey | ExistingGPGKey | GenerateSSHKey | GenerateGPGKey


@dataclass(frozen=True, slots=True)
class ResolvedSSHKey:
    info: SSHKeyInfo
    managed: bool


@dataclass(frozen=True, slots=True)
class ResolvedGPGKey:
    info: GPGKeyInfo
    managed: bool


ResolvedKey = ResolvedSSHKey | ResolvedGPGKey


@dataclass(frozen=True, slots=True)
class SetupRoute:
    route_id: RouteId
    publish_method: PublishMethod
    key_kind: KeyKind
    key_plan: KeyPlan


@dataclass(frozen=True, slots=True)
class SetupPlan:
    recommended: SetupRoute | None = None
    intervention: SetupIntervention = SetupIntervention.NONE


@dataclass(slots=True)
class CandidateState:
    config: CandidateConfig | None = None
    location: str = ""
    lookup: str = ""

    def stage(self, config: CandidateConfig, location: str, lookup: str) -> None:
        self.config = config
        self.location = location
        self.lookup = lookup

    def clear(self) -> None:
        self.config = None
        self.location = ""
        self.lookup = ""


@dataclass(slots=True)
class DiscoveryResult:
    capabilities: ToolCapabilities = field(default_factory=ToolCapabilities)
    identity: IdentityDiscovery = field(default_factory=IdentityDiscovery)
    existing_ssh: tuple[ExistingSSHKey, ...] = ()
    existing_gpg: tuple[ExistingGPGKey, ...] = ()
    plan: SetupPlan = field(default_factory=SetupPlan)


@dataclass(slots=True)
class PendingSetup:
    route_id: RouteId
    publish_method: PublishMethod
    key_kind: KeyKind
    key_managed: bool
    key_path: Path | None = None
    key_fpr: str | None = None
    username: str = ""
    email: str = ""
    public_location: str = ""
    gist_id: str = ""
    last_status: PendingSetupStatus = PendingSetupStatus.CREATED
    last_error: str = ""
    started_at: float = 0.0
    updated_at: float = 0.0


@dataclass(slots=True)
class VerificationPollState:
    started_at: float
    next_retry_at: float | None = None

    def restart(self, now: float) -> None:
        self.started_at = now
        self.next_retry_at = None

    def schedule_next(self, now: float) -> None:
        self.next_retry_at = now + PENDING_RETRY_SECONDS

    def clear(self) -> None:
        self.next_retry_at = None

    def due(self, now: float) -> bool:
        return self.next_retry_at is not None and now >= self.next_retry_at


@dataclass(slots=True)
class WorkingPlanState:
    worker: Worker[None] | None = None


@dataclass(slots=True)
class SetupAggregate:
    discovery: DiscoveryResult = field(default_factory=DiscoveryResult)
    selected_route: SetupRoute | None = None
    resolved_key: ResolvedKey | None = None
    pending: PendingSetup | None = None
    working: WorkingPlanState = field(default_factory=WorkingPlanState)
    candidate: CandidateState = field(default_factory=CandidateState)
    verification_poll: VerificationPollState = field(
        default_factory=lambda: VerificationPollState(started_at=0.0)
    )

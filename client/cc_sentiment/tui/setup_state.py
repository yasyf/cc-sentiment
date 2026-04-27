from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Literal

from textual.worker import Worker

from cc_sentiment.models import GistConfig, GPGConfig, SSHConfig
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo

CandidateConfig = SSHConfig | GPGConfig | GistConfig

PENDING_PROPAGATION_WINDOW_SECONDS = 600.0
PENDING_RETRY_SECONDS = 10.0


class SetupStage(StrEnum):
    DISCOVER = "step-discover"
    PROPOSE = "step-propose"
    WORKING = "step-working"
    GUIDE = "step-guide"
    TOOLS = "step-tools"
    FIX = "step-fix"
    SETTINGS = "step-settings"


class Tone(StrEnum):
    MUTED = "muted"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class WorkStepState(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class DiscoverRowState(StrEnum):
    WAITING = "waiting"
    OK = "ok"
    SKIPPED = "skipped"
    WARNING = "warning"
    ERROR = "error"


class RouteId(StrEnum):
    MANAGED_SSH_GIST = "managed-ssh-gist"
    MANAGED_GPG_OPENPGP = "managed-gpg-openpgp"
    MANAGED_SSH_MANUAL_GIST = "managed-ssh-manual-gist"
    EXISTING_SSH_GIST = "existing-ssh-gist"
    EXISTING_SSH_GITHUB = "existing-ssh-github"
    EXISTING_SSH_MANUAL_GIST = "existing-ssh-manual-gist"
    EXISTING_GPG_GIST = "existing-gpg-gist"
    EXISTING_GPG_OPENPGP = "existing-gpg-openpgp"
    EXISTING_GPG_GITHUB = "existing-gpg-github"
    INSTALL_TOOLS = "install-tools"
    SIGN_IN_GH = "sign-in-gh"


class PublishMethod(StrEnum):
    GIST_AUTO = "gist-auto"
    GIST_MANUAL = "gist-manual"
    GITHUB_SSH = "github-ssh"
    GITHUB_GPG = "github-gpg"
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
    email: str = ""


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
    title: str
    detail: str
    primary_label: str
    secondary_label: str
    publish_method: PublishMethod | None
    key_kind: KeyKind | None
    key_plan: KeyPlan | None = None
    needs_email: bool = False
    automated: bool = True
    safety_note: str = ""
    account_key_warning: str = ""


@dataclass(frozen=True, slots=True)
class DiscoverRow:
    label: str
    state: DiscoverRowState = DiscoverRowState.WAITING
    detail: str = ""


@dataclass(slots=True)
class WorkStep:
    label: str
    state: WorkStepState = WorkStepState.PENDING
    detail: str = ""


@dataclass(slots=True)
class GuideStatus:
    public_key_found: bool = False
    server_verified: bool = False
    last_checked_at: float = 0.0
    started_at: float = 0.0
    last_error: str = ""
    retry_count: int = 0
    openpgp_email_sent: bool = False

    def reset(self, now: float) -> None:
        self.public_key_found = False
        self.server_verified = False
        self.last_checked_at = 0.0
        self.started_at = now
        self.last_error = ""
        self.retry_count = 0
        self.openpgp_email_sent = False


@dataclass(slots=True)
class FixState:
    last_error: str = ""


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
    rows: tuple[DiscoverRow, ...] = ()
    recommended: SetupRoute | None = None
    alternatives: tuple[SetupRoute, ...] = ()


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
    last_status: str = ""
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
    steps: list[WorkStep] = field(default_factory=list)
    failure_text: str = ""
    worker: Worker[None] | None = None

    def reset(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker = None
        self.steps = []
        self.failure_text = ""


@dataclass(slots=True)
class SetupActionState:
    propose_running: bool = False
    tools_running: bool = False


VerifySource = Literal["github-ssh", "github-gpg", "gist", "openpgp"]


@dataclass(slots=True)
class SetupAggregate:
    actions: SetupActionState = field(default_factory=SetupActionState)
    discovery: DiscoveryResult = field(default_factory=DiscoveryResult)
    selected_route: SetupRoute | None = None
    resolved_key: ResolvedKey | None = None
    pending: PendingSetup | None = None
    working: WorkingPlanState = field(default_factory=WorkingPlanState)
    guide: GuideStatus = field(default_factory=GuideStatus)
    fix: FixState = field(default_factory=FixState)
    candidate: CandidateState = field(default_factory=CandidateState)
    verification_poll: VerificationPollState = field(
        default_factory=lambda: VerificationPollState(started_at=0.0)
    )

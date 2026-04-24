from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from textual.worker import Worker

from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo

PENDING_PROPAGATION_WINDOW_SECONDS = 300.0
PENDING_RETRY_SECONDS = 10.0


class SetupStage(StrEnum):
    LOADING = "step-loading"
    USERNAME = "step-username"
    DISCOVERY = "step-discovery"
    REMOTE = "step-remote"
    UPLOAD = "step-upload"
    DONE = "step-done"


class VerificationState(StrEnum):
    VERIFIED = "verified"
    PENDING = "pending"
    FAILED = "failed"


class VerificationAction(StrEnum):
    MANUAL = "manual"
    OPENPGP = "openpgp"
    GITHUB_SSH = "github-ssh"
    GITHUB_GPG = "github-gpg"
    GIST = "gist"


class RetryTarget(StrEnum):
    UPLOAD = "upload"


class GenerationMode(StrEnum):
    GIST = "gist"
    SSH = "ssh"
    GPG = "gpg"


class Tone(StrEnum):
    MUTED = "muted"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass(slots=True)
class SetupActionState:
    username_validation_running: bool = False
    discovery_action_running: bool = False
    remote_action_running: bool = False
    upload_running: bool = False


@dataclass(frozen=True, slots=True)
class UploadOption:
    action: VerificationAction
    label: str


@dataclass(frozen=True, slots=True)
class RemoteCheckRow:
    glyph: str
    check: str
    detail: str
    tone: Tone


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
class DiscoveryState:
    username_status_snapshot: str = ""
    discovered_keys: list[SSHKeyInfo | GPGKeyInfo] = field(default_factory=list)
    generation_mode: GenerationMode | None = None
    generation_radio_index: int | None = None

    def reset(self) -> None:
        self.discovered_keys = []
        self.generation_mode = None
        self.generation_radio_index = None


@dataclass(slots=True)
class RemoteCheckState:
    key_on_remote: bool = False
    key_on_openpgp: bool = False
    generation: int = 0
    worker: Worker[None] | None = None

    def cancel(self) -> None:
        if self.worker is not None:
            self.worker.cancel()
            self.worker = None
        self.generation += 1

    def reset(self) -> None:
        self.cancel()
        self.key_on_remote = False
        self.key_on_openpgp = False


@dataclass(slots=True)
class UploadPlanState:
    actions: list[VerificationAction] = field(default_factory=list)

    def reset(self) -> None:
        self.actions = []


@dataclass(slots=True)
class DoneDisplayState:
    summary_text: str = ""
    identify_text: str = ""
    process_text: str = ""
    eta_text: str = ""
    verification_detail: str = ""
    verification_action: VerificationAction | None = None
    upload_failure_text: str = ""
    failed_retry_target: RetryTarget | None = None

    def clear_failure(self) -> None:
        self.upload_failure_text = ""
        self.failed_retry_target = None
        self.verification_detail = ""

    def reset(self) -> None:
        self.summary_text = ""
        self.identify_text = ""
        self.process_text = ""
        self.eta_text = ""
        self.verification_detail = ""
        self.verification_action = None
        self.upload_failure_text = ""
        self.failed_retry_target = None

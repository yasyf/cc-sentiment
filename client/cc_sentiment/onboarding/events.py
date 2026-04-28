from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from cc_sentiment.models import ClientConfig

from .state import (
    ExistingKey,
    ExistingKeys,
    Identity,
    KeySource,
    SshMethod,
    VerifyErrorCode,
)


class Event:
    pass


@dataclass(frozen=True, slots=True)
class ResumePendingGist(Event):
    pass


@dataclass(frozen=True, slots=True)
class ResumePendingEmail(Event):
    pass


@dataclass(frozen=True, slots=True)
class SavedConfigChecked(Event):
    result: Literal["ok", "invalid", "unreachable"]


@dataclass(frozen=True, slots=True)
class NoSavedConfig(Event):
    pass


@dataclass(frozen=True, slots=True)
class DiscoveryComplete(Event):
    identity: Identity
    existing_keys: ExistingKeys
    auto_verified: bool = False
    auto_verified_config: ClientConfig | None = None


@dataclass(frozen=True, slots=True)
class UsernameSubmitted(Event):
    username: str


@dataclass(frozen=True, slots=True)
class NoGitHubChosen(Event):
    pass


@dataclass(frozen=True, slots=True)
class KeyPicked(Event):
    source: KeySource
    key: ExistingKey | None = None


@dataclass(frozen=True, slots=True)
class MethodPicked(Event):
    method: SshMethod


@dataclass(frozen=True, slots=True)
class WorkingSucceeded(Event):
    pass


@dataclass(frozen=True, slots=True)
class WorkingFailed(Event):
    pass


@dataclass(frozen=True, slots=True)
class GistVerified(Event):
    pass


@dataclass(frozen=True, slots=True)
class GistTimedOut(Event):
    pass


@dataclass(frozen=True, slots=True)
class GhAddVerified(Event):
    pass


@dataclass(frozen=True, slots=True)
class GhAddFailed(Event):
    pass


@dataclass(frozen=True, slots=True)
class EmailSent(Event):
    pass


@dataclass(frozen=True, slots=True)
class VerificationOk(Event):
    pass


@dataclass(frozen=True, slots=True)
class VerificationTimedOut(Event):
    error_code: VerifyErrorCode = "unknown"


@dataclass(frozen=True, slots=True)
class TroubleEditUsername(Event):
    new_username: str


@dataclass(frozen=True, slots=True)
class TroubleChoseEmail(Event):
    pass


@dataclass(frozen=True, slots=True)
class TroubleRestart(Event):
    pass


@dataclass(frozen=True, slots=True)
class SavedRetryRestart(Event):
    pass


@dataclass(frozen=True, slots=True)
class QuitOnboarding(Event):
    pass


@dataclass(frozen=True, slots=True)
class StartProcessing(Event):
    pass


@dataclass(frozen=True, slots=True)
class RecheckRequested(Event):
    pass

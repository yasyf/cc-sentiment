from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Stage(StrEnum):
    INITIAL = "initial"
    SAVED_RETRY = "saved-retry"
    WELCOME = "welcome"
    USER_FORM = "user-form"
    KEY_PICK = "key-pick"
    SSH_METHOD = "ssh-method"
    WORKING = "working"
    PUBLISH = "publish"
    GH_ADD = "gh-add"
    EMAIL = "email"
    INBOX = "inbox"
    TROUBLE = "trouble"
    BLOCKED = "blocked"
    DONE = "done"


class TroubleReason(StrEnum):
    GIST_TIMEOUT = "gist-timeout"
    VERIFY_TIMEOUT = "verify-timeout"


class SshMethod(StrEnum):
    GIST = "gist"
    GH_ADD = "gh-add"


class KeySource(StrEnum):
    EXISTING_SSH = "existing-ssh"
    EXISTING_GPG = "existing-gpg"
    MANAGED = "managed"


@dataclass(frozen=True, slots=True)
class Identity:
    github_username: str = ""
    email: str = ""
    email_usable: bool = False

    @property
    def has_username(self) -> bool:
        return bool(self.github_username)


@dataclass(frozen=True, slots=True)
class ExistingKey:
    fingerprint: str
    label: str
    managed: bool = False


@dataclass(frozen=True, slots=True)
class ExistingKeys:
    ssh: tuple[ExistingKey, ...] = ()
    gpg: tuple[ExistingKey, ...] = ()

    @property
    def any_usable(self) -> bool:
        return bool(self.ssh) or bool(self.gpg)


@dataclass(frozen=True, slots=True)
class SelectedKey:
    source: KeySource
    key: ExistingKey | None = None


@dataclass(frozen=True, slots=True)
class State:
    stage: Stage = Stage.INITIAL
    identity: Identity = field(default_factory=Identity)
    existing_keys: ExistingKeys = field(default_factory=ExistingKeys)
    has_saved_config: bool = False
    selected: SelectedKey | None = None
    github_lookup_allowed: bool = True
    trouble_reason: TroubleReason | None = None

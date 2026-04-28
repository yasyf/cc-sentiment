from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, Discriminator, Tag, model_validator

ContributorId = NewType("ContributorId", str)

ContributorType = Literal["github", "gpg", "gist"]

RouteIdLiteral = Literal[
    "managed-ssh-gist",
    "managed-gpg-openpgp",
    "managed-ssh-manual-gist",
]

PublishMethodLiteral = Literal[
    "gist-auto",
    "gist-manual",
    "openpgp",
]

KeyKindLiteral = Literal["ssh", "gpg"]


class PendingSetupStatus(StrEnum):
    CREATED = "created"
    AWAITING_USER = "awaiting-user"
    GIST_NOT_FOUND = "gist-not-found"
    OPENPGP_EMAIL_SENT = "openpgp-email-sent"
    VERIFY_PENDING = "verify-pending"
    VERIFY_UNAUTHORIZED = "verify-unauthorized"
    NETWORK_PENDING = "network-pending"


class SSHConfig(BaseModel, frozen=True):
    key_type: Literal["ssh"] = "ssh"
    contributor_type: Literal["github"] = "github"
    contributor_id: ContributorId
    key_path: Path


class GPGConfig(BaseModel, frozen=True):
    key_type: Literal["gpg"] = "gpg"
    contributor_type: ContributorType
    contributor_id: ContributorId
    fpr: str


class GistConfig(BaseModel, frozen=True):
    key_type: Literal["gist"] = "gist"
    contributor_type: Literal["gist"] = "gist"
    contributor_id: ContributorId
    key_path: Path
    gist_id: str


class GistGPGConfig(BaseModel, frozen=True):
    key_type: Literal["gist-gpg"] = "gist-gpg"
    contributor_type: Literal["gist"] = "gist"
    contributor_id: ContributorId
    fpr: str
    gist_id: str


ClientConfig = Annotated[
    Annotated[SSHConfig, Tag("ssh")]
    | Annotated[GPGConfig, Tag("gpg")]
    | Annotated[GistConfig, Tag("gist")]
    | Annotated[GistGPGConfig, Tag("gist-gpg")],
    Discriminator(lambda v: v.get("key_type", "ssh") if isinstance(v, dict) else v.key_type),
]


class PendingSetupModel(BaseModel, frozen=True):
    model_config = {"extra": "forbid"}

    route_id: RouteIdLiteral
    publish_method: PublishMethodLiteral
    key_kind: KeyKindLiteral
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

    @model_validator(mode="after")
    def _validate_key_fields(self) -> PendingSetupModel:
        match self.key_kind:
            case "ssh":
                if self.key_path is None:
                    raise ValueError("ssh route requires key_path")
                if self.key_fpr is not None:
                    raise ValueError("ssh route forbids key_fpr")
            case "gpg":
                if self.key_fpr is None:
                    raise ValueError("gpg route requires key_fpr")
                if self.key_path is not None:
                    raise ValueError("gpg route forbids key_path")
        return self


class AppState(BaseModel):
    model_config = {"extra": "ignore"}

    config: ClientConfig | None = None
    pending_setup: PendingSetupModel | None = None
    has_celebrated_first_upload: bool = False
    github_username: str = ""

    @classmethod
    def state_path(cls) -> Path:
        return Path.home() / ".cc-sentiment" / "state.json"

    @classmethod
    def load(cls) -> AppState:
        path = cls.state_path()
        if not path.exists():
            return cls()
        return cls.model_validate_json(path.read_text())

    def save(self) -> None:
        path = self.state_path()
        created = not path.parent.exists()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if created:
            path.parent.chmod(0o700)
        path.write_text(self.model_dump_json(indent=2))

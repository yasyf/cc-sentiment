from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, Discriminator, Tag

ContributorId = NewType("ContributorId", str)

ContributorType = Literal["github", "gpg", "gist"]

KeySourceLiteral = Literal["existing-ssh", "existing-gpg", "managed"]

ResumeTargetLiteral = Literal["gist", "gh_add", "email"]


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


class PendingSelectedKey(BaseModel, frozen=True):
    source: KeySourceLiteral
    fingerprint: str = ""
    label: str = ""
    managed: bool = False
    path: Path | None = None
    algorithm: str = ""


class PendingSetupModel(BaseModel, frozen=True):
    model_config = {"extra": "forbid"}

    selected: PendingSelectedKey
    username: str = ""
    email: str = ""
    email_usable: bool = False
    target: ResumeTargetLiteral
    started_at: float = 0.0


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

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, Discriminator, Tag

ContributorId = NewType("ContributorId", str)

ContributorType = Literal["github", "gpg", "gist"]


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


ClientConfig = Annotated[
    Annotated[SSHConfig, Tag("ssh")]
    | Annotated[GPGConfig, Tag("gpg")]
    | Annotated[GistConfig, Tag("gist")],
    Discriminator(lambda v: v.get("key_type", "ssh") if isinstance(v, dict) else v.key_type),
]


class AppState(BaseModel):
    model_config = {"extra": "ignore"}

    config: ClientConfig | None = None

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.model_dump_json(indent=2))

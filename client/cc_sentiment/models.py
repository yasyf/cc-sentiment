from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

SessionId = NewType("SessionId", str)
BucketIndex = NewType("BucketIndex", int)
SentimentScore = NewType("SentimentScore", int)
PromptVersion = NewType("PromptVersion", str)

PROMPT_VERSION = PromptVersion("v1")
DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"
CLIENT_VERSION = "0.1.0"


class TranscriptMessage(BaseModel, frozen=True):
    role: str
    content: str
    timestamp: datetime
    session_id: SessionId
    uuid: str


class ConversationBucket(BaseModel, frozen=True):
    session_id: SessionId
    bucket_index: BucketIndex
    bucket_start: datetime
    messages: tuple[TranscriptMessage, ...]


class SentimentRecord(BaseModel, frozen=True):
    model_config = ConfigDict(populate_by_name=True)

    time: datetime
    conversation_id: SessionId
    bucket_index: BucketIndex
    sentiment_score: SentimentScore
    prompt_version: PromptVersion = PROMPT_VERSION
    inference_model: str = Field(default=DEFAULT_MODEL, validation_alias="model_id", serialization_alias="model_id")
    client_version: str = CLIENT_VERSION


class UploadPayload(BaseModel, frozen=True):
    github_username: str
    signature: str
    records: tuple[SentimentRecord, ...]


class SSHConfig(BaseModel, frozen=True):
    key_type: Literal["ssh"] = "ssh"
    github_username: str
    key_path: Path


class GPGConfig(BaseModel, frozen=True):
    key_type: Literal["gpg"] = "gpg"
    github_username: str
    fpr: str


ClientConfig = Annotated[
    Annotated[SSHConfig, Tag("ssh")] | Annotated[GPGConfig, Tag("gpg")],
    Discriminator(lambda v: v.get("key_type", "ssh") if isinstance(v, dict) else v.key_type),
]


class ProcessedFile(BaseModel, frozen=True):
    mtime: float


class ProcessedSession(BaseModel, frozen=True):
    records: tuple[SentimentRecord, ...]
    uploaded: bool = False


class AppState(BaseModel):
    processed_files: dict[str, ProcessedFile] = Field(default_factory=dict)
    sessions: dict[SessionId, ProcessedSession] = Field(default_factory=dict)
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

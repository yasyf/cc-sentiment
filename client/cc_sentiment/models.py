from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import NewType

from pydantic import BaseModel, ConfigDict, Field

SessionId = NewType("SessionId", str)
BucketIndex = NewType("BucketIndex", int)
SentimentScore = NewType("SentimentScore", int)
PromptVersion = NewType("PromptVersion", str)

PROMPT_VERSION = PromptVersion("v1")
MODEL_ID = "gemma-4-e4b-it-4bit"
CLIENT_VERSION = "0.1.0"
DEFAULT_MODEL_REPO = "unsloth/gemma-4-E4B-it-UD-MLX-4bit"


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
    inference_model: str = Field(default=MODEL_ID, validation_alias="model_id", serialization_alias="model_id")
    client_version: str = CLIENT_VERSION


class UploadPayload(BaseModel, frozen=True):
    github_username: str
    signature: str
    records: tuple[SentimentRecord, ...]


class ClientConfig(BaseModel, frozen=True):
    github_username: str
    key_path: Path


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

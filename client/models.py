from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import NewType

from pydantic import BaseModel, Field

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
    time: datetime
    conversation_id: SessionId
    bucket_index: BucketIndex
    sentiment_score: SentimentScore
    prompt_version: PromptVersion = PROMPT_VERSION
    model_id: str = MODEL_ID
    client_version: str = CLIENT_VERSION


class UploadPayload(BaseModel, frozen=True):
    github_username: str
    signature: str
    records: tuple[SentimentRecord, ...]


class ClientConfig(BaseModel, frozen=True):
    github_username: str
    key_path: Path


class ProcessedSession(BaseModel, frozen=True):
    mtime: float
    buckets: int
    uploaded: bool = False


class AppState(BaseModel):
    processed: dict[SessionId, ProcessedSession] = Field(default_factory=dict)
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

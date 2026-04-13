from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag

SessionId = NewType("SessionId", str)
BucketIndex = NewType("BucketIndex", int)
SentimentScore = NewType("SentimentScore", int)
PromptVersion = NewType("PromptVersion", str)
ContributorId = NewType("ContributorId", str)

ContributorType = Literal["github", "gpg"]

PROMPT_VERSION = PromptVersion("v1")
DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"
CLIENT_VERSION = "0.1.0"


class TranscriptMessage(BaseModel, frozen=True):
    role: str
    content: str
    timestamp: datetime
    session_id: SessionId
    uuid: str
    tool_names: tuple[str, ...]
    thinking_chars: int
    cc_version: str


class BucketMetrics(BaseModel, frozen=True):
    tool_counts: dict[str, int]
    read_edit_ratio: float | None
    turn_count: int
    thinking_present: bool
    thinking_chars: int
    cc_version: str

    @staticmethod
    def from_messages(messages: tuple[TranscriptMessage, ...]) -> BucketMetrics:
        tool_counts: dict[str, int] = {}
        total_thinking_chars = 0
        thinking_present = False
        turn_count = 0
        cc_version = ""

        for msg in messages:
            if msg.role == "user":
                turn_count += 1
                if msg.cc_version:
                    cc_version = msg.cc_version
            for name in msg.tool_names:
                tool_counts[name] = tool_counts.get(name, 0) + 1
            if msg.thinking_chars > 0:
                thinking_present = True
                total_thinking_chars += msg.thinking_chars

        read_ops = sum(tool_counts.get(t, 0) for t in ("Read", "Grep", "Glob"))
        write_ops = sum(tool_counts.get(t, 0) for t in ("Edit", "Write"))
        read_edit_ratio = read_ops / write_ops if write_ops > 0 else None

        return BucketMetrics(
            tool_counts=tool_counts,
            read_edit_ratio=read_edit_ratio,
            turn_count=turn_count,
            thinking_present=thinking_present,
            thinking_chars=total_thinking_chars,
            cc_version=cc_version,
        )


class ConversationBucket(BaseModel, frozen=True):
    session_id: SessionId
    bucket_index: BucketIndex
    bucket_start: datetime
    messages: tuple[TranscriptMessage, ...]

    @property
    def metrics(self) -> BucketMetrics:
        return BucketMetrics.from_messages(self.messages)


class SentimentRecord(BaseModel, frozen=True):
    model_config = ConfigDict(populate_by_name=True)

    time: datetime
    conversation_id: SessionId
    bucket_index: BucketIndex
    sentiment_score: SentimentScore
    prompt_version: PromptVersion = PROMPT_VERSION
    inference_model: str = Field(default=DEFAULT_MODEL, validation_alias="model_id", serialization_alias="model_id")
    client_version: str = CLIENT_VERSION
    read_edit_ratio: float | None
    turn_count: int
    thinking_present: bool
    thinking_chars: int
    cc_version: str


class UploadPayload(BaseModel, frozen=True):
    contributor_type: ContributorType
    contributor_id: str
    signature: str
    records: tuple[SentimentRecord, ...]


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


ClientConfig = Annotated[
    Annotated[SSHConfig, Tag("ssh")] | Annotated[GPGConfig, Tag("gpg")],
    Discriminator(lambda v: v.get("key_type", "ssh") if isinstance(v, dict) else v.key_type),
]


class BucketKey(BaseModel, frozen=True):
    session_id: SessionId
    bucket_index: BucketIndex


class ProcessedFile(BaseModel, frozen=True):
    mtime: float
    scored_buckets: frozenset[BucketKey] = frozenset()


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

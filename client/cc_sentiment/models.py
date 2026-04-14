from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, NewType

from pydantic import BaseModel, Discriminator, Field, Tag

SessionId = NewType("SessionId", str)
BucketIndex = NewType("BucketIndex", int)
SentimentScore = NewType("SentimentScore", int)
PromptVersion = NewType("PromptVersion", str)
ContributorId = NewType("ContributorId", str)

ContributorType = Literal["github", "gpg"]

PROMPT_VERSION = PromptVersion("v1")
CLIENT_VERSION = "0.1.0"


class BaseMessage(BaseModel, frozen=True):
    content: str
    timestamp: datetime
    session_id: SessionId
    uuid: str
    tool_names: tuple[str, ...]
    thinking_chars: int


class UserMessage(BaseMessage, frozen=True):
    role: Literal["user"] = "user"
    cc_version: str


class AssistantMessage(BaseMessage, frozen=True):
    role: Literal["assistant"] = "assistant"
    claude_model: str


TranscriptMessage = Annotated[
    Annotated[UserMessage, Tag("user")] | Annotated[AssistantMessage, Tag("assistant")],
    Discriminator("role"),
]


class BucketMetrics(BaseModel, frozen=True):
    tool_counts: dict[str, int]
    read_edit_ratio: float | None
    turn_count: int
    thinking_present: bool
    thinking_chars: int
    cc_version: str
    claude_model: str

    @staticmethod
    def from_messages(messages: tuple[TranscriptMessage, ...]) -> BucketMetrics:
        users = tuple(m for m in messages if isinstance(m, UserMessage))
        assistants = tuple(m for m in messages if isinstance(m, AssistantMessage))
        if not users or not assistants:
            raise ValueError("bucket must have both user and assistant messages")

        tool_counts = Counter(name for m in messages for name in m.tool_names)
        read_ops = sum(tool_counts[t] for t in ("Read", "Grep", "Glob"))
        write_ops = sum(tool_counts[t] for t in ("Edit", "Write"))
        thinking = sum(m.thinking_chars for m in messages)

        return BucketMetrics(
            tool_counts=dict(tool_counts),
            read_edit_ratio=read_ops / write_ops if write_ops else None,
            turn_count=len(users),
            thinking_present=thinking > 0,
            thinking_chars=thinking,
            cc_version=users[-1].cc_version,
            claude_model=assistants[-1].claude_model,
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
    time: datetime
    conversation_id: SessionId
    bucket_index: BucketIndex
    sentiment_score: SentimentScore
    prompt_version: PromptVersion = PROMPT_VERSION
    claude_model: str
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

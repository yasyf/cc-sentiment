from __future__ import annotations

from datetime import datetime
from importlib.metadata import version

from pydantic import BaseModel

from .bucket import PROMPT_VERSION, BucketIndex, PromptVersion, SentimentScore, SessionId
from .config import ContributorType

CLIENT_VERSION = version("cc-sentiment")


class SentimentRecord(BaseModel, frozen=True):
    time: datetime
    conversation_id: SessionId
    bucket_index: BucketIndex
    sentiment_score: SentimentScore
    prompt_version: PromptVersion = PROMPT_VERSION
    claude_model: str
    client_version: str = CLIENT_VERSION
    read_edit_ratio: float | None
    edits_without_prior_read_ratio: float | None
    write_edit_ratio: float | None
    tool_calls_per_turn: float
    subagent_count: int
    turn_count: int
    thinking_present: bool
    thinking_chars: int
    cc_version: str


class UploadPayload(BaseModel, frozen=True):
    contributor_type: ContributorType
    contributor_id: str
    signature: str
    records: tuple[SentimentRecord, ...]

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

__all__ = [
    "SentimentRecord",
    "UploadPayload",
    "UploadResponse",
    "VerifyRequest",
    "StatusResponse",
    "TimelinePoint",
    "DistributionPoint",
    "TrendComparison",
    "ModelBreakdown",
    "DataResponse",
    "MyStatResponse",
    "DaemonEvent",
    "DaemonEventPayload",
    "ShareMintPayload",
    "ShareMintRequest",
    "ShareMintResponse",
    "ShareRecord",
]


class SentimentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime
    conversation_id: str = Field(min_length=1)
    bucket_index: int = Field(ge=0)
    sentiment_score: int = Field(ge=1, le=5)
    prompt_version: str = Field(min_length=1)
    claude_model: str = Field(min_length=1)
    client_version: str = Field(min_length=1)
    read_edit_ratio: float | None
    edits_without_prior_read_ratio: float | None
    write_edit_ratio: float | None
    tool_calls_per_turn: float = Field(ge=0)
    subagent_count: int = Field(ge=0)
    turn_count: int = Field(ge=0)
    thinking_present: bool
    thinking_chars: int = Field(ge=0)
    cc_version: str


class UploadPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    contributor_type: Literal["github", "gpg", "gist"]
    contributor_id: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    records: list[SentimentRecord] = Field(min_length=1, max_length=10_000)


class UploadResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = "ok"
    ingested: int


class VerifyRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    contributor_type: Literal["github", "gpg", "gist"]
    contributor_id: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    test_payload: str = Field(min_length=1)


class StatusResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = "ok"


class TimelinePoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime
    avg_score: float
    count: int
    avg_read_edit_ratio: float | None
    avg_edits_without_prior_read_ratio: float | None
    avg_tool_calls_per_turn: float | None


class DistributionPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: int
    count: int


class TrendComparison(BaseModel):
    model_config = ConfigDict(frozen=True)

    sentiment_current: float
    sentiment_previous: float
    sessions_current: int
    sessions_previous: int
    read_edit_current: float | None
    read_edit_previous: float | None


class ModelBreakdown(BaseModel):
    model_config = ConfigDict(frozen=True)

    claude_model: str
    avg_score: float
    count: int
    avg_read_edit_ratio: float | None
    avg_write_edit_ratio: float | None
    avg_subagent_count: float | None


class DataResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeline: list[TimelinePoint]
    distribution: list[DistributionPoint]
    total_records: int
    total_sessions: int
    total_contributors: int
    last_updated: datetime
    trend: TrendComparison
    model_breakdown: list[ModelBreakdown]
    avg_read_edit_ratio: float | None
    avg_edits_without_prior_read_ratio: float | None
    avg_tool_calls_per_turn: float | None
    avg_write_edit_ratio: float | None
    avg_subagent_count: float | None


class MyStatResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str
    percentile: int = Field(ge=0, le=100)
    text: str = Field(min_length=1)
    tweet_text: str = Field(min_length=1)
    total_contributors: int = Field(ge=1)


class DaemonEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["install", "uninstall"]
    client_version: str = Field(min_length=1)
    time: datetime


class DaemonEventPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    contributor_type: Literal["github", "gpg", "gist"]
    contributor_id: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    event: DaemonEvent


class ShareMintPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    issued_at: AwareDatetime


class ShareMintRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    contributor_type: Literal["github", "gpg", "gist"]
    contributor_id: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    payload: ShareMintPayload


class ShareRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    contributor_type: Literal["github", "gpg", "gist"]
    contributor_id: str = Field(min_length=1)
    avatar_url: str | None
    created_at: datetime


class ShareMintResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    url: str = Field(min_length=1)

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "SentimentRecord",
    "UploadPayload",
    "UploadResponse",
    "VerifyRequest",
    "StatusResponse",
    "TimelinePoint",
    "HourlyPoint",
    "WeekdayPoint",
    "DistributionPoint",
    "DataResponse",
]


class SentimentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime
    conversation_id: str = Field(min_length=1)
    bucket_index: int = Field(ge=0)
    sentiment_score: int = Field(ge=1, le=5)
    prompt_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    client_version: str = Field(min_length=1)


class UploadPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    github_username: str = Field(min_length=1)
    signature: str = Field(min_length=1)
    records: list[SentimentRecord] = Field(min_length=1, max_length=10_000)


class UploadResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: str = "ok"
    ingested: int


class VerifyRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    github_username: str = Field(min_length=1)
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


class HourlyPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    hour: int
    avg_score: float
    count: int


class WeekdayPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    dow: int
    avg_score: float
    count: int


class DistributionPoint(BaseModel):
    model_config = ConfigDict(frozen=True)

    score: int
    count: int


class DataResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    timeline: list[TimelinePoint]
    hourly: list[HourlyPoint]
    weekday: list[WeekdayPoint]
    distribution: list[DistributionPoint]
    total_records: int
    last_updated: datetime

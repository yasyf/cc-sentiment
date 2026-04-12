from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SentimentRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: datetime
    conversation_id: str
    bucket_index: int
    sentiment_score: int
    prompt_version: str
    model_id: str
    client_version: str


class UploadPayload(BaseModel):
    model_config = ConfigDict(frozen=True)

    github_username: str
    signature: str
    records: list[SentimentRecord]


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

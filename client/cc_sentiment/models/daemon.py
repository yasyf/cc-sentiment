from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from cc_sentiment.models.config import ContributorType

DaemonEventType = Literal["install", "uninstall"]


class DaemonEvent(BaseModel, frozen=True):
    event_type: DaemonEventType
    client_version: str
    time: datetime


class DaemonEventPayload(BaseModel, frozen=True):
    contributor_type: ContributorType
    contributor_id: str
    signature: str
    event: DaemonEvent

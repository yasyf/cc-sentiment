from __future__ import annotations

from pydantic import BaseModel


class MyStat(BaseModel, frozen=True):
    kind: str
    percentile: int
    text: str
    tweet_text: str
    total_contributors: int

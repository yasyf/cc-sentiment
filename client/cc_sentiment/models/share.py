from __future__ import annotations

from pydantic import AwareDatetime, BaseModel

from cc_sentiment.models.config import ContributorType


class ShareMintPayload(BaseModel, frozen=True):
    issued_at: AwareDatetime


class ShareMintRequest(BaseModel, frozen=True):
    contributor_type: ContributorType
    contributor_id: str
    signature: str
    payload: ShareMintPayload


class ShareMintResponse(BaseModel, frozen=True):
    id: str
    url: str

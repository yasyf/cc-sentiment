from __future__ import annotations

import orjson

from cc_sentiment.models import SentimentRecord
from cc_sentiment.signing.backends import SigningBackend


class PayloadSigner:
    @staticmethod
    def canonical_json(records: list[SentimentRecord]) -> str:
        return orjson.dumps(
            [r.model_dump(mode="json", by_alias=True) for r in records],
            option=orjson.OPT_SORT_KEYS,
        ).decode()

    @staticmethod
    async def sign(data: str, backend: SigningBackend) -> str:
        return await backend.sign(data)

    @classmethod
    async def sign_records(cls, records: list[SentimentRecord], backend: SigningBackend) -> str:
        return await cls.sign(cls.canonical_json(records), backend)

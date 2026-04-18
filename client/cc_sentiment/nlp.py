from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import anyio.to_thread

if TYPE_CHECKING:
    import spacy.language

MODEL_NAME = "en_core_web_sm"
DISABLED_PIPES = ["parser", "lemmatizer"]


class NLP:
    model: ClassVar[spacy.language.Language | None] = None
    locks_by_loop: ClassVar[dict[int, asyncio.Lock]] = {}

    @classmethod
    def get(cls) -> spacy.language.Language | None:
        return cls.model

    @classmethod
    async def ensure_ready(cls) -> spacy.language.Language:
        if cls.model is not None:
            return cls.model
        loop_id = id(asyncio.get_running_loop())
        lock = cls.locks_by_loop.setdefault(loop_id, asyncio.Lock())
        async with lock:
            if cls.model is None:
                cls.model = await anyio.to_thread.run_sync(cls.load_or_download)
        return cls.model

    @staticmethod
    def load_or_download() -> spacy.language.Language:
        import spacy
        from spacy.cli.download import download as spacy_download

        try:
            return spacy.load(MODEL_NAME, disable=DISABLED_PIPES)
        except OSError:
            spacy_download(MODEL_NAME)
            return spacy.load(MODEL_NAME, disable=DISABLED_PIPES)

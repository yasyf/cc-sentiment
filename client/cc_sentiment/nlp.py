from __future__ import annotations

from typing import ClassVar

import anyio
import anyio.to_thread
import spacy
from spacy.cli.download import download as spacy_download

MODEL_NAME = "en_core_web_sm"
DISABLED_PIPES = ["parser", "lemmatizer"]


class NLP:
    model: ClassVar[spacy.language.Language | None] = None
    ready: ClassVar[anyio.Event | None] = None

    @classmethod
    def get(cls) -> spacy.language.Language | None:
        return cls.model

    @classmethod
    async def ensure_ready(cls) -> spacy.language.Language:
        if cls.ready is None:
            event = anyio.Event()
            cls.ready = event
            cls.model = await anyio.to_thread.run_sync(cls.load_or_download)
            event.set()
        else:
            await cls.ready.wait()
        assert cls.model is not None
        return cls.model

    @staticmethod
    def load_or_download() -> spacy.language.Language:
        try:
            return spacy.load(MODEL_NAME, disable=DISABLED_PIPES)
        except OSError:
            spacy_download(MODEL_NAME)
            return spacy.load(MODEL_NAME, disable=DISABLED_PIPES)

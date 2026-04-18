from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock

import pytest


@pytest.fixture(autouse=True)
def no_network_warmup(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        "cc_sentiment.tui.app.CCSentimentApp._maybe_prewarm", lambda self: None
    )
    monkeypatch.setattr(
        "cc_sentiment.nlp.NLP.ensure_ready", AsyncMock(return_value=None)
    )
    yield

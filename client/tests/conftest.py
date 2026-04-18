from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def no_prewarm(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(
        "cc_sentiment.tui.app.CCSentimentApp._maybe_prewarm", lambda self: None
    )
    yield

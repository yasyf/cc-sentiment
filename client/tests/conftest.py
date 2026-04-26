from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from cc_sentiment.models import AppState


@pytest.fixture(autouse=True)
def isolated_state_path(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    sandbox = tmp_path_factory.mktemp("cc-sentiment-state") / "state.json"
    monkeypatch.setattr(AppState, "state_path", classmethod(lambda cls: sandbox))
    yield sandbox


@pytest.fixture(autouse=True)
def no_network_warmup(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    if "slow" in request.keywords:
        yield
        return
    monkeypatch.setattr(
        "cc_sentiment.tui.app.CCSentimentApp._maybe_prewarm", lambda self: None
    )
    monkeypatch.setattr(
        "cc_sentiment.nlp.NLP.ensure_ready", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "cc_sentiment.lexicon.Lexicon.ensure_ready", AsyncMock(return_value=None)
    )
    classifier = MagicMock()
    classifier.score = AsyncMock(return_value=[])
    classifier.close = AsyncMock()
    monkeypatch.setattr(
        "cc_sentiment.tui.app.EngineFactory.build",
        AsyncMock(return_value=classifier),
    )
    monkeypatch.setattr(
        "cc_sentiment.headless.EngineFactory.build",
        AsyncMock(return_value=classifier),
    )
    yield

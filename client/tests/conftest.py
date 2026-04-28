from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_sentiment.models import AppState
from cc_sentiment.tui.dashboard import DashboardScreen
from cc_sentiment.upload import AuthOk, AuthUnauthorized


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
        "cc_sentiment.tui.dashboard.screen.DashboardScreen._maybe_prewarm", lambda self: None
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
        "cc_sentiment.tui.dashboard.screen.EngineFactory.build",
        AsyncMock(return_value=classifier),
    )
    monkeypatch.setattr(
        "cc_sentiment.headless.EngineFactory.build",
        AsyncMock(return_value=classifier),
    )
    yield


@pytest.fixture
def auth_ok() -> Iterator[None]:
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthOk(),
    ):
        yield


@pytest.fixture
def auth_unauthorized() -> Iterator[None]:
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthUnauthorized(status=401),
    ):
        yield


@pytest.fixture
def no_stat_share() -> Iterator[None]:
    async def _noop(self, config, push_share):
        return None
    with patch.object(DashboardScreen, "_fetch_card", new=_noop):
        yield

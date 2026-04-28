from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import Label

from cc_sentiment.models import AppState, ContributorId, SSHConfig
from cc_sentiment.tui import CCSentimentApp
from tests.helpers import make_scan


async def test_set_total_renders_eta_when_hardware_estimates(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=10.0):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(1200, "mlx", 0)
            label_text = str(app.query_one("#progress-label", Label).render())
            assert "00:02:00" in label_text
            assert app.status_text == ""


async def test_set_total_omits_eta_when_hardware_unknown(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(500, "mlx", 0)
            label_text = str(app.query_one("#progress-label", Label).render())
            assert "00:00:00" in label_text
            assert app.status_text == ""


async def test_add_buckets_updates_progress(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(100, "mlx", 0)
            app._add_buckets(5)
            app._add_buckets(3)
            assert app.scored == 8

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import Button, Static

from cc_sentiment.models import (
    AppState,
    ContributorId,
    MyStat,
    SSHConfig,
)
from cc_sentiment.tui import CCSentimentApp
from tests.helpers import make_scan


GITHUB_CONFIG = SSHConfig(
    contributor_id=ContributorId("testuser"),
    key_path=Path("/home/.ssh/id_ed25519"),
)

STAT = MyStat(
    kind="kindness",
    percentile=72,
    text="nicer to Claude than 72% of developers",
    tweet_text="I'm nicer to Claude than 72% of developers.",
    total_contributors=100,
)


async def test_cta_shows_schedule_when_daemon_not_installed(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.dashboard.screen.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.dashboard.screen.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app.dashboard.view.cta.schedule_available is True
            assert app.dashboard.view.cta.showing == "schedule"
            section = pilot.app.dashboard.query_one("#cta-section")
            assert "inactive" not in section.classes
            button = pilot.app.dashboard.query_one("#cta-action", Button)
            assert str(button.label) == "Run daily"


async def test_cta_hides_when_daemon_installed_and_no_tweet(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.dashboard.screen.LaunchAgent.is_installed", return_value=True), \
         patch("cc_sentiment.tui.dashboard.screen.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app.dashboard.view.cta.schedule_available is False
            assert app.dashboard.view.cta.has_tweet() is False
            section = pilot.app.dashboard.query_one("#cta-section")
            assert "inactive" in section.classes


async def test_cta_rotates_between_tweet_and_schedule(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.dashboard.screen.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.dashboard.screen.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            app.dashboard.view.set_tweet(GITHUB_CONFIG, STAT)
            await pilot.pause()
            assert app.dashboard.view.cta.showing == "schedule"
            button = pilot.app.dashboard.query_one("#cta-action", Button)
            assert str(button.label) == "Run daily"

            app.dashboard.view.rotate_cta()
            await pilot.pause()
            assert app.dashboard.view.cta.showing == "tweet"
            assert str(button.label) == "Share on X"
            title = pilot.app.dashboard.query_one("#cta-title", Static)
            assert "nicer to Claude" in str(title.render())

            app.dashboard.view.rotate_cta()
            await pilot.pause()
            assert app.dashboard.view.cta.showing == "schedule"


async def test_cta_pins_to_tweet_after_install_succeeds(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.dashboard.screen.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.dashboard.screen.LaunchAgent.install") as mock_install, \
         patch("cc_sentiment.tui.dashboard.screen.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            app.dashboard.view.set_tweet(GITHUB_CONFIG, STAT)
            await pilot.pause()
            assert app.dashboard.view.cta.showing == "schedule"

            await pilot.click("#cta-action")
            await pilot.pause(delay=0.2)

            mock_install.assert_called_once()
            assert app.dashboard.view.cta.schedule_available is False
            assert app.dashboard.view.cta.showing == "tweet"

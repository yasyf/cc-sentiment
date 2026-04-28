from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, patch

import anyio
from textual.app import App
from textual.widgets import Button

from cc_sentiment.models import (
    ContributorId,
    GistConfig,
    GPGConfig,
    MyStat,
    ShareMintResponse,
    SSHConfig,
)
from cc_sentiment.tui.dashboard.popovers import StatShareScreen


STAT = MyStat(
    kind="kindness",
    percentile=72,
    text="nicer to Claude than 72% of developers",
    tweet_text="I'm nicer to Claude than 72% of developers.",
    total_contributors=100,
)

GITHUB_CONFIG = SSHConfig(
    contributor_id=ContributorId("testuser"),
    key_path=Path("/home/.ssh/id_ed25519"),
)
GPG_CONFIG = GPGConfig(
    contributor_type="gpg",
    contributor_id=ContributorId("gpg-user-id"),
    fpr="ABCDEF0123456789",
)


class StatShareHarness(App[None]):
    def __init__(self, config: SSHConfig | GPGConfig | GistConfig, stat: MyStat) -> None:
        super().__init__()
        self.config = config
        self.stat = stat

    def on_mount(self) -> None:
        self.push_screen(StatShareScreen(self.config, self.stat))


def stub_mint_share(share_id: str = "sh-abc123") -> AsyncMock:
    return AsyncMock(return_value=ShareMintResponse(
        id=share_id,
        url=f"https://sentiments.cc/share/{share_id}",
    ))


async def test_stat_share_renders_stat_text():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.dashboard.popovers.stat_share.Uploader.mint_share", new=stub_mint_share()):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            text = " ".join(
                str(w.render()) for w in pilot.app.screen.query("Label, Static")
            )
            assert "nicer to Claude than 72% of developers" in text


async def test_stat_share_tweet_button_opens_share_url():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.dashboard.popovers.stat_share.Uploader.mint_share", new=stub_mint_share("sh-xyz789")):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.click("#stat-tweet")
            await pilot.pause()

    harness.open_url.assert_called_once()
    url = harness.open_url.call_args[0][0]
    assert "twitter.com/intent/tweet" in url
    assert "share%2Fsh-xyz789" in url or "share/sh-xyz789" in url
    assert "nicer+to+Claude" in url or "nicer%20to%20Claude" in url


async def test_stat_share_tweet_button_disabled_until_mint_resolves():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    mint_event = anyio.Event()

    async def slow_mint(self, config):
        await mint_event.wait()
        return ShareMintResponse(id="sh-late", url="https://sentiments.cc/share/sh-late")

    with patch("cc_sentiment.tui.dashboard.popovers.stat_share.Uploader.mint_share", new=slow_mint):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.1)
            tweet = pilot.app.screen.query_one("#stat-tweet", Button)
            assert tweet.disabled is True
            await pilot.click("#stat-tweet")
            await pilot.pause()
            assert not harness.open_url.called

            mint_event.set()
            await pilot.pause(delay=0.3)
            assert tweet.disabled is False


async def test_stat_share_surfaces_signing_failure_on_label():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    failing_mint = AsyncMock(
        side_effect=subprocess.CalledProcessError(1, ["ssh-keygen"])
    )
    with patch("cc_sentiment.tui.dashboard.popovers.stat_share.Uploader.mint_share", new=failing_mint):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            tweet = pilot.app.screen.query_one("#stat-tweet", Button)
            assert str(tweet.label) == "Share unavailable"
            assert tweet.disabled is True


async def test_stat_share_surfaces_signing_timeout_on_label():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    failing_mint = AsyncMock(side_effect=TimeoutError())
    with patch("cc_sentiment.tui.dashboard.popovers.stat_share.Uploader.mint_share", new=failing_mint):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            tweet = pilot.app.screen.query_one("#stat-tweet", Button)
            assert str(tweet.label) == "Share unavailable"
            assert tweet.disabled is True


async def test_stat_share_skip_dismisses_without_opening_browser():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.dashboard.popovers.stat_share.Uploader.mint_share", new=stub_mint_share()):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.click("#stat-skip")
            await pilot.pause()

    harness.open_url.assert_not_called()


async def test_stat_share_escape_dismisses():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.dashboard.popovers.stat_share.Uploader.mint_share", new=stub_mint_share()):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.press("escape")
            await pilot.pause()

    harness.open_url.assert_not_called()

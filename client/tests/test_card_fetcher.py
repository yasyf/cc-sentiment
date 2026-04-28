from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from cc_sentiment.models import ContributorId, MyStat, SSHConfig
from cc_sentiment.tui.dashboard.popovers.stat_share import CardFetcher


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


async def test_card_fetcher_invokes_on_ready_when_stat_arrives():
    calls: list[MyStat] = []
    states: list[tuple[str, float, str | None]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        return_value=STAT,
    ):
        await CardFetcher(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda s, e, stop: states.append((s, e, stop)),
        ).run()

    assert calls == [STAT]
    assert any(state[2] == "ready" for state in states)


async def test_card_fetcher_reports_no_card_on_404():
    calls: list[MyStat] = []
    states: list[tuple[str, float, str | None]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await CardFetcher(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda s, e, stop: states.append((s, e, stop)),
        ).run()

    assert calls == []
    assert states[-1] == ("http 404", states[-1][1], "no card")


async def test_card_fetcher_reports_error_on_network_failure():
    calls: list[MyStat] = []
    states: list[tuple[str, float, str | None]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("no net"),
    ):
        await CardFetcher(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda s, e, stop: states.append((s, e, stop)),
        ).run()

    assert calls == []
    assert any(state[2] == "error" for state in states)

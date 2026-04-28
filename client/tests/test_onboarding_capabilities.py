from __future__ import annotations

from unittest.mock import AsyncMock, patch

import anyio
import pytest

from cc_sentiment.onboarding import Capabilities


def has_only(*names: str):
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in names else None
    return which


@pytest.fixture(autouse=True)
async def fresh_cache():
    await Capabilities.invalidate()
    yield
    await Capabilities.invalidate()


class TestGet:
    async def test_returns_dataclass_with_expected_fields(self):
        with (
            patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("ssh-keygen", "gpg")),
            patch(
                "cc_sentiment.signing.discovery.KeyDiscovery.gh_authenticated",
                new=AsyncMock(return_value=False),
            ),
        ):
            caps = await Capabilities.get()
        assert caps.has_ssh_keygen is True
        assert caps.has_gpg is True
        assert caps.has_gh is False
        assert caps.gh_authenticated is False
        assert caps.has_brew is False

    async def test_skips_gh_check_when_no_gh(self):
        gh_mock = AsyncMock(return_value=True)
        with (
            patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None),
            patch("cc_sentiment.signing.discovery.KeyDiscovery.gh_authenticated", new=gh_mock),
        ):
            caps = await Capabilities.get()
        assert caps.gh_authenticated is False
        gh_mock.assert_not_called()

    async def test_runs_gh_check_when_gh_present(self):
        gh_mock = AsyncMock(return_value=True)
        with (
            patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("gh")),
            patch("cc_sentiment.signing.discovery.KeyDiscovery.gh_authenticated", new=gh_mock),
        ):
            caps = await Capabilities.get()
        assert caps.has_gh is True
        assert caps.gh_authenticated is True
        gh_mock.assert_awaited_once()


class TestCacheBehavior:
    async def test_repeated_get_returns_same_instance(self):
        with (
            patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None),
            patch(
                "cc_sentiment.signing.discovery.KeyDiscovery.gh_authenticated",
                new=AsyncMock(return_value=False),
            ),
        ):
            first = await Capabilities.get()
            second = await Capabilities.get()
        assert first is second

    async def test_concurrent_get_calls_build_only_once(self):
        which_mock = AsyncMock(return_value=False)
        with (
            patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("gh")),
            patch("cc_sentiment.signing.discovery.KeyDiscovery.gh_authenticated", new=which_mock),
        ):
            results: list[Capabilities] = []

            async def collect() -> None:
                results.append(await Capabilities.get())

            async with anyio.create_task_group() as tg:
                for _ in range(5):
                    tg.start_soon(collect)
        assert len({id(c) for c in results}) == 1
        assert which_mock.await_count == 1

    async def test_invalidate_clears_cache(self):
        with (
            patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None),
            patch(
                "cc_sentiment.signing.discovery.KeyDiscovery.gh_authenticated",
                new=AsyncMock(return_value=False),
            ),
        ):
            first = await Capabilities.get()
            await Capabilities.invalidate()
            second = await Capabilities.get()
        assert first is not second

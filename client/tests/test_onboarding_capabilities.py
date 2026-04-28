from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from cc_sentiment.onboarding import Capabilities


def has_only(*names: str):
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in names else None
    return which


@pytest.fixture(autouse=True)
def fresh_singleton():
    Capabilities.reset()
    yield
    Capabilities.reset()


class TestSingleton:
    def test_repeated_construction_returns_same_instance(self):
        assert Capabilities() is Capabilities()

    def test_reset_creates_new_instance(self):
        first = Capabilities()
        Capabilities.reset()
        assert Capabilities() is not first


class TestProperties:
    async def test_has_ssh_keygen_true_when_present(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("ssh-keygen")):
            assert await Capabilities().has_ssh_keygen is True

    async def test_has_ssh_keygen_false_when_absent(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None):
            assert await Capabilities().has_ssh_keygen is False

    async def test_gh_authenticated_short_circuits_when_no_gh(self):
        run_mock = AsyncMock()
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None), \
             patch("cc_sentiment.onboarding.capabilities.anyio.run_process", new=run_mock):
            assert await Capabilities().gh_authenticated is False
        run_mock.assert_not_awaited()

    async def test_gh_authenticated_runs_subprocess_when_gh_present(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("gh")), \
             patch(
                 "cc_sentiment.onboarding.capabilities.anyio.run_process",
                 new=AsyncMock(return_value=type("R", (), {"returncode": 0})()),
             ):
            assert await Capabilities().gh_authenticated is True


class TestCaching:
    async def test_property_evaluated_once_then_memoized(self):
        with patch(
            "cc_sentiment.onboarding.capabilities.shutil.which",
            return_value="/usr/bin/ssh-keygen",
        ) as which:
            caps = Capabilities()
            await caps.has_ssh_keygen
            await caps.has_ssh_keygen
            await caps.has_ssh_keygen
        assert which.call_count == 1


class TestSeed:
    async def test_seed_short_circuits_property(self):
        Capabilities.seed(has_ssh_keygen=True, has_gh=False, gh_authenticated=False)
        assert await Capabilities().has_ssh_keygen is True
        assert await Capabilities().has_gh is False
        assert await Capabilities().gh_authenticated is False

from __future__ import annotations

from unittest.mock import patch

import pytest

from cc_sentiment.onboarding import Capabilities


def has_only(*names: str):
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in names else None
    return which


@pytest.fixture(autouse=True)
def reset_singleton():
    Capabilities.reset()
    yield
    Capabilities.reset()


class TestSingleton:
    def test_repeated_construction_returns_same_instance(self):
        assert Capabilities() is Capabilities()

    def test_reset_yields_fresh_instance(self):
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

    async def test_gh_authenticated_skips_subprocess_when_no_gh(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None), \
             patch("cc_sentiment.onboarding.capabilities.subprocess.run") as run:
            assert await Capabilities().gh_authenticated is False
        run.assert_not_called()

    async def test_gh_authenticated_runs_subprocess_when_gh_present(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("gh")), \
             patch("cc_sentiment.onboarding.capabilities.subprocess.run") as run:
            run.return_value.returncode = 0
            assert await Capabilities().gh_authenticated is True

    async def test_clipboard_macos_uses_pbcopy(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("pbcopy")), \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "darwin"):
            assert await Capabilities().can_clipboard is True

    async def test_clipboard_linux_accepts_any_of_three(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("xsel")), \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "linux"):
            assert await Capabilities().can_clipboard is True

    async def test_browser_falls_back_to_false_on_error(self):
        with patch(
            "cc_sentiment.onboarding.capabilities.webbrowser.get",
            side_effect=__import__("webbrowser").Error("none"),
        ):
            assert await Capabilities().can_open_browser is False


class TestCaching:
    async def test_property_evaluated_once_then_memoized(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value="/usr/bin/ssh-keygen") as which:
            caps = Capabilities()
            await caps.has_ssh_keygen
            await caps.has_ssh_keygen
            await caps.has_ssh_keygen
        assert which.call_count == 1

    async def test_distinct_properties_evaluated_independently(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=lambda n: f"/usr/bin/{n}") as which:
            caps = Capabilities()
            await caps.has_ssh_keygen
            await caps.has_gpg
        assert {c.args[0] for c in which.call_args_list} == {"ssh-keygen", "gpg"}

    async def test_reset_clears_cache(self):
        results = iter([True, False])
        with patch(
            "cc_sentiment.onboarding.capabilities.shutil.which",
            side_effect=lambda _: "/usr/bin/x" if next(results) else None,
        ):
            assert await Capabilities().has_ssh_keygen is True
            Capabilities.reset()
            assert await Capabilities().has_ssh_keygen is False


class TestSeed:
    async def test_seed_overrides_detection(self):
        Capabilities.seed(has_ssh_keygen=True, has_gpg=False)
        with patch("cc_sentiment.onboarding.capabilities.shutil.which") as which:
            assert await Capabilities().has_ssh_keygen is True
            assert await Capabilities().has_gpg is False
        which.assert_not_called()

    async def test_seed_partial_leaves_others_lazy(self):
        Capabilities.seed(has_ssh_keygen=True)
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None):
            assert await Capabilities().has_ssh_keygen is True
            assert await Capabilities().has_gpg is False

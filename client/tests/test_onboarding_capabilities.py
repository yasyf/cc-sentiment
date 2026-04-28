from __future__ import annotations

from unittest.mock import patch

import pytest

from cc_sentiment.onboarding import Capabilities, CapabilityCache, CapabilityProbe


def all_tools_present(name: str) -> str:
    return f"/usr/bin/{name}"


def no_tools_present(_name: str) -> None:
    return None


def has_only(*names: str):
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in names else None
    return which


# ===========================================================================
# CapabilityProbe.detect()
# ===========================================================================


class TestCapabilityProbe:
    async def test_detects_all_tools_when_all_present_and_gh_authed(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=all_tools_present), \
             patch("cc_sentiment.onboarding.capabilities.subprocess.run") as run, \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "linux"), \
             patch("cc_sentiment.onboarding.capabilities.webbrowser.get", return_value=object()):
            run.return_value.returncode = 0
            caps = await CapabilityProbe.detect()
        assert caps == Capabilities(
            has_ssh_keygen=True, has_gpg=True, has_gh=True,
            gh_authenticated=True, has_brew=True,
            can_clipboard=True, can_open_browser=True,
        )

    async def test_no_gh_skips_auth_check(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("ssh-keygen")), \
             patch("cc_sentiment.onboarding.capabilities.subprocess.run") as run, \
             patch("cc_sentiment.onboarding.capabilities.webbrowser.get", return_value=None):
            caps = await CapabilityProbe.detect()
        assert caps.has_gh is False
        assert caps.gh_authenticated is False
        run.assert_not_called()

    async def test_gh_present_but_not_authed(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("gh")), \
             patch("cc_sentiment.onboarding.capabilities.subprocess.run") as run, \
             patch("cc_sentiment.onboarding.capabilities.webbrowser.get", return_value=None):
            run.return_value.returncode = 1
            caps = await CapabilityProbe.detect()
        assert caps.has_gh is True
        assert caps.gh_authenticated is False

    async def test_clipboard_macos_uses_pbcopy(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("pbcopy")), \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "darwin"), \
             patch("cc_sentiment.onboarding.capabilities.webbrowser.get", return_value=None):
            caps = await CapabilityProbe.detect()
        assert caps.can_clipboard is True

    async def test_clipboard_linux_uses_any_of_three(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("xsel")), \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "linux"), \
             patch("cc_sentiment.onboarding.capabilities.webbrowser.get", return_value=None):
            caps = await CapabilityProbe.detect()
        assert caps.can_clipboard is True

    async def test_clipboard_absent_when_no_tools(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=no_tools_present), \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "linux"), \
             patch("cc_sentiment.onboarding.capabilities.webbrowser.get", return_value=None):
            caps = await CapabilityProbe.detect()
        assert caps.can_clipboard is False
        assert caps.can_open_browser is False


# ===========================================================================
# CapabilityCache: lazy + idempotent + invalidatable
# ===========================================================================


class TestCapabilityCache:
    @pytest.fixture
    def stub_caps(self) -> Capabilities:
        return Capabilities(
            has_ssh_keygen=True, has_gpg=False, has_gh=False,
            gh_authenticated=False, has_brew=False,
            can_clipboard=True, can_open_browser=True,
        )

    async def test_first_get_calls_detect(self, stub_caps: Capabilities):
        cache = CapabilityCache()
        with patch.object(CapabilityProbe, "detect", return_value=stub_caps) as detect:
            result = await cache.get()
        assert result == stub_caps
        detect.assert_called_once()

    async def test_second_get_returns_cached_without_redetect(self, stub_caps: Capabilities):
        cache = CapabilityCache()
        with patch.object(CapabilityProbe, "detect", return_value=stub_caps) as detect:
            await cache.get()
            await cache.get()
        assert detect.call_count == 1

    async def test_invalidate_forces_redetect(self, stub_caps: Capabilities):
        cache = CapabilityCache()
        with patch.object(CapabilityProbe, "detect", return_value=stub_caps) as detect:
            await cache.get()
            cache.invalidate()
            await cache.get()
        assert detect.call_count == 2

from __future__ import annotations

from unittest.mock import patch

import pytest

from cc_sentiment.onboarding import Capabilities


def has_only(*names: str):
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in names else None
    return which


@pytest.fixture(autouse=True)
def fresh_singleton(monkeypatch):
    monkeypatch.setattr(Capabilities, "_instance", None)


class TestSingleton:
    def test_repeated_construction_returns_same_instance(self):
        assert Capabilities() is Capabilities()


class TestProperties:
    def test_has_ssh_keygen_true_when_present(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("ssh-keygen")):
            assert Capabilities().has_ssh_keygen is True

    def test_has_ssh_keygen_false_when_absent(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None):
            assert Capabilities().has_ssh_keygen is False

    def test_gh_authenticated_skips_subprocess_when_no_gh(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value=None), \
             patch("cc_sentiment.onboarding.capabilities.subprocess.run") as run:
            assert Capabilities().gh_authenticated is False
        run.assert_not_called()

    def test_gh_authenticated_runs_subprocess_when_gh_present(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("gh")), \
             patch("cc_sentiment.onboarding.capabilities.subprocess.run") as run:
            run.return_value.returncode = 0
            assert Capabilities().gh_authenticated is True

    def test_clipboard_macos_uses_pbcopy(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("pbcopy")), \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "darwin"):
            assert Capabilities().can_clipboard is True

    def test_clipboard_linux_accepts_any_of_three(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=has_only("xsel")), \
             patch("cc_sentiment.onboarding.capabilities.sys.platform", "linux"):
            assert Capabilities().can_clipboard is True

    def test_browser_falls_back_to_false_on_error(self):
        import webbrowser
        with patch(
            "cc_sentiment.onboarding.capabilities.webbrowser.get",
            side_effect=webbrowser.Error("none"),
        ):
            assert Capabilities().can_open_browser is False


class TestCaching:
    def test_property_evaluated_once_then_memoized(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", return_value="/usr/bin/ssh-keygen") as which:
            caps = Capabilities()
            caps.has_ssh_keygen
            caps.has_ssh_keygen
            caps.has_ssh_keygen
        assert which.call_count == 1

    def test_distinct_properties_evaluated_independently(self):
        with patch("cc_sentiment.onboarding.capabilities.shutil.which", side_effect=lambda n: f"/usr/bin/{n}") as which:
            caps = Capabilities()
            caps.has_ssh_keygen
            caps.has_gpg
        assert {c.args[0] for c in which.call_args_list} == {"ssh-keygen", "gpg"}

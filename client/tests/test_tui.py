from __future__ import annotations

from collections.abc import Callable
from html import unescape
from pathlib import Path
import re
import subprocess
from time import monotonic as wall_monotonic, sleep
from unittest.mock import AsyncMock, Mock, patch

import anyio
import httpx
import pytest
from textual.app import App
from textual.pilot import Pilot
from textual.containers import Vertical
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Label, RadioButton, RadioSet, Static

from cc_sentiment.models import AppState, ContributorId, GistConfig, GPGConfig, MyStat, SSHConfig
from cc_sentiment.repo import Repository
from cc_sentiment.signing import GPGKeyInfo, KeyDiscovery, SSHBackend, SSHKeyInfo
from cc_sentiment.engines import (
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeStatus,
    ClaudeUnavailable,
)
from cc_sentiment.tui import CCSentimentApp
from cc_sentiment.tui.moments_view import MomentsView
from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.screens import (
    CostReviewScreen,
    PlatformErrorScreen,
    SetupScreen,
    StatShareScreen,
)
from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.screens.setup import (
    SetupStage,
    VerificationState,
)
from cc_sentiment.tui.setup_state import (
    RetryTarget,
    VerificationAction,
)
from cc_sentiment.tui.stages import (
    Discovering,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    RescanConfirm,
    Scoring,
    Stage,
    Uploading,
)
from cc_sentiment.tui.widgets import (
    Card,
    CommandBox,
    HourlyChart,
    KeyPreview,
    PendingStatus,
    StepActions,
    StepBody,
    StepHeader,
)
from cc_sentiment.upload import (
    DASHBOARD_URL,
    AuthOk,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    UploadPool,
    UploadProgress,
    Uploader,
)
from tests.helpers import make_record, make_scan


class SetupHarness(App[None]):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.dismissed: bool | None = None

    def on_mount(self) -> None:
        self.push_screen(SetupScreen(self.state), self._capture)

    def _capture(self, result: bool | None) -> None:
        self.dismissed = result


class PrimitiveHarness(App[None]):
    CSS = Dialog.DEFAULT_CSS

    def __init__(self, *widgets) -> None:
        super().__init__()
        self.widgets = widgets

    def compose(self):
        yield from self.widgets


class KeyPreviewHarness(App[None]):
    CSS = Dialog.DEFAULT_CSS

    def compose(self):
        yield Button("Back", id="before")
        yield KeyPreview(
            "\n".join(
                [
                    "-----BEGIN PGP PUBLIC KEY BLOCK-----",
                    *[f"line-{index:02d}" for index in range(20)],
                    "-----END PGP PUBLIC KEY BLOCK-----",
                ]
            ),
            id="preview",
        )
        yield Button("Next", id="after")


def current_step_actions(screen: SetupScreen) -> StepActions:
    step = screen.query_one(f"#{screen.query_one(ContentSwitcher).current}", Vertical)
    return step.query_one(StepActions)


def primary_button(screen: SetupScreen) -> Button:
    buttons = list(current_step_actions(screen).query(Button))
    return next(button for button in buttons if button.variant == "primary")


def visible_step_ids(screen: SetupScreen) -> list[str]:
    switcher = screen.query_one(ContentSwitcher)
    return [widget.id for widget in switcher.displayed_and_visible_children]


def radio_labels(radio: RadioSet) -> list[str]:
    return [str(button.label) for button in radio.query(RadioButton)]


def cell_text(cell: object) -> str:
    return cell.plain if hasattr(cell, "plain") else str(cell)


def table_rows(table: DataTable) -> list[tuple[str, ...]]:
    return [
        tuple(cell_text(cell) for cell in table.get_row_at(index))
        for index in range(table.row_count)
    ]


def step_header_texts(step: Vertical) -> tuple[str, str]:
    header = step.query_one(StepHeader)
    title = cell_text(header.query_one(".step-title", Static).render())
    explainer_widget = next(iter(header.query(".step-explainer").results(Static)), None)
    return title, (cell_text(explainer_widget.render()) if explainer_widget is not None else "")


def screenshot_text(app: App[None]) -> str:
    return unescape(app.export_screenshot())


def pending_branch_signature(screen: SetupScreen) -> tuple[object, ...]:
    return (
        screen.current_stage.value,
        screen.verification_state.value,
        screen.verification_ok,
        str(screen.query_one("#done-summary", Static).render()),
        str(screen.query_one("#done-verify", Static).render()),
        str(screen.query_one("#done-instructions", Static).render()),
        screen.query_one("#pending-status", PendingStatus).label,
        tuple(
            (button.id, button.variant, button.label.plain)
            for button in current_step_actions(screen).query(Button)
        ),
    )


async def wait_for_condition(
    pilot: Pilot[None],
    predicate: Callable[[], bool],
    failure_message: Callable[[], str],
    timeout: float = 2.0,
) -> None:
    deadline = wall_monotonic() + timeout
    while wall_monotonic() < deadline:
        await pilot.pause()
        if predicate():
            return
    pytest.fail(failure_message())


class FakeMonotonic:
    def __init__(self, value: float = 0.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@pytest.fixture
def no_auto_setup():
    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.find_git_username", return_value=None), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new_callable=AsyncMock, return_value=AuthOk()):
        yield


@pytest.fixture
def auth_ok():
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthOk(),
    ):
        yield


@pytest.fixture
def no_stat_share():
    with patch.object(CCSentimentApp, "_poll_card", new=Mock(side_effect=lambda _config: lambda: None)):
        yield


async def test_setup_mounts_all_steps(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        for step in ("step-loading", "step-username", "step-discovery", "step-remote", "step-upload", "step-done"):
            assert pilot.app.screen.query_one(f"#{step}") is not None


async def test_setup_starts_on_loading_then_falls_to_username(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        assert pilot.app.screen.query_one(ContentSwitcher).current == "step-username"


async def test_setup_screen_state_is_encapsulated_in_dataclasses(no_auto_setup):
    class BaselineDialog(Dialog[bool]):
        def compose(self):
            yield Static("baseline")

    class BaselineHarness(App[None]):
        def on_mount(self) -> None:
            self.push_screen(BaselineDialog())

    async with BaselineHarness().run_test() as baseline_pilot:
        await baseline_pilot.pause()
        baseline_private = {
            name
            for name in vars(baseline_pilot.app.screen)
            if re.match(r"^_[a-z]+(_[a-z]+)*$", name)
        }

    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen
        private_attrs = {
            name
            for name in vars(screen)
            if re.match(r"^_[a-z]+(_[a-z]+)*$", name)
        }
        reactive_attrs = {name for name in private_attrs if name.startswith("_reactive_")}
        screen_private = private_attrs - baseline_private - reactive_attrs
        dir_private = {
            name
            for name in dir(screen)
            if (
                re.match(r"^_[a-z]+(_[a-z]+)*$", name)
                and name in vars(screen)
                and not callable(getattr(screen, name))
            )
        } - baseline_private - reactive_attrs

        assert screen_private == set()
        assert dir_private == set()


async def test_setup_empty_username_blocked(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        pilot.app.screen.query_one("#username-input", Input).value = ""
        await pilot.click("#username-next")
        await pilot.pause()
        assert pilot.app.screen.query_one(ContentSwitcher).current == "step-username"


async def test_setup_auto_detect_prepopulates_username():
    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, "testuser")), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.find_git_username", return_value="testuser"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            assert pilot.app.screen.query_one("#username-input", Input).value == "testuser"


async def test_setup_auto_success_jumps_to_done():
    state = AppState()

    async def fake_run(self) -> tuple[bool, str | None]:
        self.state.config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        return True, "testuser"

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new=fake_run), \
         patch.object(AppState, "save"):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert pilot.app.screen.query_one(ContentSwitcher).current == "step-done"


async def test_auto_setup_run_short_circuits_after_matching_ssh():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter

    config = SSHConfig(
        contributor_id=ContributorId("Alice-01"),
        key_path=Path("/home/.ssh/id_ed25519"),
    )
    setup = AutoSetup(AppState(), StatusEmitter(Static()))

    with patch.object(AutoSetup, "detect_username", new=AsyncMock(return_value="Alice-01")), \
         patch.object(AutoSetup, "try_github_ssh", new=AsyncMock(return_value=config)) as try_github_ssh, \
         patch.object(AutoSetup, "probe_and_save", new=AsyncMock(return_value=True)) as probe_and_save, \
         patch.object(AutoSetup, "try_github_gpg", new=AsyncMock()) as try_github_gpg, \
         patch.object(AutoSetup, "try_existing_gist", new=AsyncMock()) as try_existing_gist, \
         patch.object(AutoSetup, "find_local_gpg", new=AsyncMock(return_value=())) as find_local_gpg:
        assert await setup.run() == (True, "Alice-01")
        try_github_ssh.assert_awaited_once_with("Alice-01")
        probe_and_save.assert_awaited_once_with(config)
        try_github_gpg.assert_not_called()
        try_existing_gist.assert_not_called()
        find_local_gpg.assert_not_called()


async def test_auto_setup_run_tries_openpgp_without_github_username():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter

    key = GPGKeyInfo(
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        email="test@example.com",
        algo="rsa4096",
    )
    config = GPGConfig(
        contributor_type="gpg",
        contributor_id=ContributorId(key.fpr),
        fpr=key.fpr,
    )
    setup = AutoSetup(AppState(), StatusEmitter(Static()))

    with patch.object(AutoSetup, "detect_username", new=AsyncMock(return_value=None)), \
         patch.object(AutoSetup, "find_local_gpg", new=AsyncMock(return_value=(key,))), \
         patch.object(AutoSetup, "try_openpgp", new=AsyncMock(return_value=config)) as try_openpgp, \
         patch.object(AutoSetup, "probe_and_save", new=AsyncMock(return_value=True)) as probe_and_save:
        assert await setup.run() == (True, None)
        try_openpgp.assert_awaited_once_with(key, None)
        probe_and_save.assert_awaited_once_with(config)


async def test_setup_auto_success_openpgp_without_username_uses_short_gpg_label():
    fingerprint = "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"
    state = AppState()

    async def fake_run(self) -> tuple[bool, str | None]:
        self.state.config = GPGConfig(
            contributor_type="gpg",
            contributor_id=ContributorId(fingerprint),
            fpr=fingerprint,
        )
        return True, None

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new=fake_run), \
         patch.object(AppState, "save"):
        async with SetupHarness(state).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            summary = str(pilot.app.screen.query_one("#done-summary", Static).render())

            assert "Signed in as GPG A700D73A" in summary
            assert fingerprint not in summary


async def test_setup_username_flow_auto_detected_username_survives_discovery_round_trip():
    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, "Alice-01")), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen

            assert screen.query_one("#username-input", Input).value == "Alice-01"
            assert str(screen.query_one("#username-status", Static).render()) == "Auto-detected: Alice-01"

            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)
            screen.on_discovery_back()
            await pilot.pause()

            assert screen.query_one("#username-input", Input).value == "Alice-01"
            assert str(screen.query_one("#username-status", Static).render()) == "Auto-detected: Alice-01"


async def test_setup_discovery_single_radioset_no_datatable(no_auto_setup):
    ssh_keys = (SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host"),)

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=ssh_keys), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            radio = screen.query_one("#key-select", RadioSet)

            assert list(screen.query("#step-discovery DataTable")) == []
            assert radio.display is True
            assert radio_labels(radio) == ["SSH · id_ed25519 · ssh-ed25519"]
            assert radio.styles.max_height.value == 12
            assert screen.query_one("#discovery-next", Button).disabled is False


async def test_setup_no_keys_without_gpg_disables_next(no_auto_setup):
    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert list(screen.query("#step-discovery DataTable")) == []
            assert radio_labels(screen.query_one("#key-select", RadioSet)) == []
            assert screen.discovery.generation_mode is None


async def test_setup_discovery_no_gh_username_hides_ssh_keys(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen

            await pilot.click("#username-skip")
            await pilot.pause(delay=0.3)

            assert radio_labels(screen.query_one("#key-select", RadioSet)) == [
                "GPG · ABCD EF12 3456 7890 · test@example.com",
            ]


async def test_setup_tooling_absent_shows_install_hints_and_disables_next(no_auto_setup):
    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "alice"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            assert str(screen.query_one("#discovery-status", Static).render()) == "No signing keys found on your machine."
            assert "install the GitHub CLI" in str(screen.query_one("#discovery-help", Static).render())
            assert "brew install gnupg" in str(screen.query_one("#discovery-help", Static).render())
            assert screen.query_one("#discovery-next", Button).disabled is True


async def test_setup_remote_check_ssh_found(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 AAAA key1",)), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-remote"
            screen.check_remotes()
            await pilot.pause(delay=0.5)

            assert screen.remote_check.key_on_remote is True
            assert screen.query_one("#remote-next", Button).disabled is False


async def test_setup_remote_check_ssh_not_found(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 BBBB other",)), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-remote"
            screen.check_remotes()
            await pilot.pause(delay=0.5)

            assert screen.remote_check.key_on_remote is False


async def test_setup_remote_elision_skips_visible_remote_step(no_auto_setup):
    gpg_key = GPGKeyInfo(
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        email="test@example.com",
        algo="ed25519",
    )

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_openpgp_key", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"), \
         patch.object(AppState, "save"), \
         patch("cc_sentiment.tui.screens.setup.TranscriptDiscovery.find_transcripts", return_value=()):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            screen.on_discovery_next()
            await pilot.pause(delay=0.6)

            assert screen.current_stage.value == "step-done"
            assert "step-remote" not in [stage.value for stage in screen.transition_history]
            assert visible_step_ids(screen) == ["step-done"]
            assert "Ready to upload" in cell_text(screen.query_one("#done-verify", Static).render())


async def test_setup_not_linked_header_uses_warning_copy(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 BBBB other",)), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            screen.on_discovery_next()
            await pilot.pause(delay=0.6)

            step = screen.query_one("#step-remote", Vertical)
            title, explainer = step_header_texts(step)

            assert screen.current_stage.value == "step-remote"
            assert "Your key isn't linked yet" in title
            assert "Verifying your key" not in title
            assert "Verifying your key" not in explainer
            assert "warning" in step.query_one(".step-title", Static).classes


async def test_setup_check_results_datatable_uses_columns_and_row_tones(no_auto_setup):
    request = httpx.Request("GET", "https://github.com/testuser.gpg")
    gpg_key = GPGKeyInfo(
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        email="test@example.com",
        algo="ed25519",
    )

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gpg_key_on_github", side_effect=httpx.ConnectError("boom", request=request)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_openpgp_key", return_value=""):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            screen.on_discovery_next()
            await pilot.pause(delay=0.6)

            table = screen.query_one("#remote-checks", DataTable)

            assert [cell_text(column.label) for column in table.ordered_columns] == ["glyph", "check", "detail"]
            assert table_rows(table) == [
                ("?", "GitHub", "Couldn't reach GitHub"),
                ("—", "keys.openpgp.org", "Not on keys.openpgp.org yet"),
            ]
            assert [getattr(cell, "style", "") for cell in table.get_row_at(0)] == ["dim", "dim", "dim"]
            assert [getattr(cell, "style", "") for cell in table.get_row_at(1)] == ["yellow", "yellow", "yellow"]


async def test_setup_key_preview_gpg_long_ascii_armor_stays_within_dialog(no_auto_setup):
    long_key = "\n".join(
        (
            "-----BEGIN PGP PUBLIC KEY BLOCK-----",
            *["A" * 64 for _ in range(40)],
            "-----END PGP PUBLIC KEY BLOCK-----",
        )
    )
    gpg_key = GPGKeyInfo(
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        email="test@example.com",
        algo="ed25519",
    )

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value=long_key):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.selected_key = gpg_key
            screen.transition_to(screen.current_stage.__class__.UPLOAD)
            await screen._populate_upload_options()
            await pilot.pause()

            dialog = screen.query_one("#dialog-box", Vertical)
            preview = screen.query_one("#upload-key-text", KeyPreview)

            assert preview.region.y + preview.region.height <= dialog.region.y + dialog.region.height
            assert preview.region.height <= 5
            assert "-----BEGIN PGP PUBLIC KEY BLOCK-----" in preview.text
            assert "-----END PGP PUBLIC KEY BLOCK-----" in preview.text
            assert preview.max_scroll_y > 0

            before = preview.scroll_y
            preview.scroll_end(animate=False)
            await pilot.pause()

            assert preview.scroll_y > before
            assert preview.scroll_y == preview.max_scroll_y


async def test_setup_save_ssh_config(tmp_path: Path, no_auto_setup):
    state = AppState()
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch.object(AppState, "state_path", return_value=state_file):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause()

            assert isinstance(state.config, SSHConfig)
            assert state.config.contributor_id == "testuser"
            assert state.config.key_path == Path("/home/.ssh/id_ed25519")


async def test_setup_save_gpg_config(tmp_path: Path, no_auto_setup):
    state = AppState()
    state_file = tmp_path / "state.json"
    gpg_key = GPGKeyInfo(fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A", email="test@example.com", algo="rsa4096")

    with patch.object(AppState, "state_path", return_value=state_file):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = gpg_key
            screen._save_and_finish()
            await pilot.pause()

            assert isinstance(state.config, GPGConfig)
            assert state.config.fpr == "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"


async def test_setup_persist_at_commit_before_probe_returns(tmp_path: Path):
    state = AppState()
    state_file = tmp_path / "state.json"
    probe_started = anyio.Event()
    probe_release = anyio.Event()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    async def probe(_: Uploader, config: SSHConfig) -> AuthUnauthorized:
        assert config.key_path == ssh_key.path
        probe_started.set()
        await probe_release.wait()
        return AuthUnauthorized(status=401)

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new=probe):
        async with SetupHarness(state).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await probe_started.wait()
            await pilot.pause()

            assert state_file.exists()
            assert AppState.model_validate_json(state_file.read_text()).config == SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=ssh_key.path,
            )
            assert screen.current_stage.value == "step-done"

            probe_release.set()
            await pilot.pause(delay=0.3)


def test_app_state_first_run_mkdir_creates_dir_with_0700(tmp_path: Path):
    state_file = tmp_path / ".cc-sentiment" / "state.json"
    config = SSHConfig(
        contributor_id=ContributorId("testuser"),
        key_path=Path("/home/.ssh/id_ed25519"),
    )

    with patch.object(AppState, "state_path", return_value=state_file):
        AppState(config=config).save()

    assert state_file.exists()
    assert state_file.parent.is_dir()
    assert state_file.parent.stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize(
    "config",
    [
        SSHConfig(
            contributor_id=ContributorId("ssh-user"),
            key_path=Path("/home/.ssh/id_ed25519"),
        ),
        GPGConfig(
            contributor_type="github",
            contributor_id=ContributorId("github-user"),
            fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        ),
        GPGConfig(
            contributor_type="gpg",
            contributor_id=ContributorId("F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"),
            fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        ),
        GistConfig(
            contributor_id=ContributorId("gist-user"),
            key_path=Path("/home/.cc-sentiment/keys/id_ed25519"),
            gist_id="abc123def456",
        ),
    ],
)
def test_app_state_roundtrip_terminal_configs(tmp_path: Path, config):
    state_file = tmp_path / "state.json"

    with patch.object(AppState, "state_path", return_value=state_file):
        AppState(config=config).save()
        loaded = AppState.load()

    assert loaded.config is not None
    assert loaded.config.model_dump() == config.model_dump()


async def test_setup_idempotent_verified_saved_config_short_circuits_loading(tmp_path: Path):
    state_file = tmp_path / "state.json"
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("PRIVATE")
    key_path.with_suffix(".pub").write_text("ssh-ed25519 AAAA saved@test")
    config = SSHConfig(
        contributor_id=ContributorId("testuser"),
        key_path=key_path,
    )

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new=AsyncMock(side_effect=AssertionError("resume should not rerun auto setup"))), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch("cc_sentiment.tui.screens.setup.TranscriptDiscovery.find_transcripts", return_value=()), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new_callable=AsyncMock, return_value=AuthOk()) as probe:
        state = AppState(config=config)
        state.save()
        async with SetupHarness(AppState.load()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.5)
            screen = pilot.app.screen

            assert screen.current_stage.value == "step-done"
            assert screen.verification_state is VerificationState.VERIFIED
            assert visible_step_ids(screen) == ["step-done"]
            assert list(screen.query("#done-btn")) != []
            probe.assert_awaited_once_with(config)


async def test_setup_resume_pending_from_saved_config_skips_username(tmp_path: Path):
    state_file = tmp_path / "state.json"
    config = GPGConfig(
        contributor_type="github",
        contributor_id=ContributorId("testuser"),
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
    )
    matching_key = GPGKeyInfo(
        fpr=config.fpr,
        email="test@example.com",
        algo="ed25519",
    )

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new=AsyncMock(side_effect=AssertionError("resume should not rerun auto setup"))), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(matching_key,)), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnauthorized(status=401),
         ) as probe:
        state = AppState(config=config)
        state.save()
        async with SetupHarness(AppState.load()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.5)
            screen = pilot.app.screen

            assert screen.current_stage.value == "step-done"
            assert screen.verification_state is VerificationState.PENDING
            assert visible_step_ids(screen) == ["step-done"]
            assert list(screen.query("#done-btn")) == []
            assert screen.query_one("#pending-status", PendingStatus) is not None
            probe.assert_awaited_once_with(config)


async def test_setup_stale_config_missing_key_falls_back_to_username(tmp_path: Path):
    state_file = tmp_path / "state.json"
    missing_key = tmp_path / "missing-key"
    config = SSHConfig(
        contributor_id=ContributorId("testuser"),
        key_path=missing_key,
    )

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)) as auto_setup, \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new_callable=AsyncMock) as probe:
        state = AppState(config=config)
        state.save()
        async with SetupHarness(AppState.load()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.5)
            screen = pilot.app.screen

            assert screen.current_stage.value == "step-username"
            auto_setup.assert_awaited_once()
            probe.assert_not_called()


async def test_setup_escape_loading_dismisses_without_partial_save(tmp_path: Path):
    state_file = tmp_path / "state.json"
    harness = SetupHarness(AppState())

    async def slow_run(*_) -> tuple[bool, str | None]:
        await anyio.sleep(0.5)
        return False, None

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new=slow_run), \
         patch.object(AppState, "state_path", return_value=state_file):
        async with harness.run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.05)

            assert pilot.app.screen.current_stage.value == "step-loading"

            await pilot.press("escape")
            await pilot.pause(delay=0.1)
            await pilot.pause(delay=0.6)

            assert harness.dismissed is False
            assert not state_file.exists()


async def test_setup_done_button_dismisses_true(tmp_path: Path, no_auto_setup):
    state = AppState()
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch.object(AppState, "state_path", return_value=state_file):
        harness = SetupHarness(state)
        async with harness.run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause()

            await pilot.click("#done-btn")
            await pilot.pause()

            assert harness.dismissed is True


async def test_setup_honest_end_state_verified_branch_uses_payload_card_and_contribute_visibility(
    tmp_path: Path,
):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.tui.screens.setup.TranscriptDiscovery.find_transcripts", return_value=()):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.state.config = SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=ssh_key.path,
            )
            screen.done_display.summary_text = "Signed in as testuser using SSH key id_ed25519."
            screen.transition_to(screen.current_stage.__class__.DONE)
            screen._set_verification_branch(VerificationState.VERIFIED)
            await pilot.pause(delay=0.2)

            assert screen.verification_state is VerificationState.VERIFIED
            assert screen.verification_ok is True
            assert "success" in screen.query_one("#done-summary-card", Card).classes
            assert screen.query_one("#done-btn", Button).variant == "primary"
            assert screen.query_one("#done-payload-card", Card).border_title == "What actually gets sent"
            assert "one row per conversation" in str(screen.query_one("#done-payload-lead", Static).render())
            assert "sentiment_score" in screen.query_one("#done-payload", Static).content.code
            assert list(screen.query("#pending-retry, #failed-retry")) == []


async def test_setup_honest_end_state_pending_branch_on_unreachable_hides_contribute_visibility(
    tmp_path: Path,
):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
            return_value=AuthUnreachable(detail="boom"),
         ):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.4)

            assert screen.verification_state is VerificationState.PENDING
            assert screen.verification_ok is False
            assert "warning" in screen.query_one("#done-summary-card", Card).classes
            assert list(screen.query("#done-btn")) == []
            assert screen.query_one("#pending-status", PendingStatus) is not None
            assert screen.query_one("#pending-exit", Button).label.plain == "Exit, continue later"
            assert screen.query_one("#pending-retry", Button).label.plain == "Retry now"


async def test_setup_manual_to_pending_routes_without_contribute_cta(tmp_path: Path):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.tui.screens.setup.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
            return_value=AuthUnauthorized(status=401),
         ):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            await pilot.click("#upload-go")
            await pilot.pause(delay=0.4)

            assert screen.current_stage.value == "step-done"
            assert screen.verification_state is VerificationState.PENDING
            assert list(screen.query("#done-btn")) == []
            assert screen.query_one("#pending-status", PendingStatus) is not None
            assert "Paste your public key at" in str(screen.query_one("#done-instructions", Static).render())


async def test_setup_honest_end_state_failed_branch_retry_button_contract(tmp_path: Path):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.tui.screens.setup.TranscriptDiscovery.find_transcripts", return_value=()):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.state.config = SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=ssh_key.path,
            )
            screen.done_display.summary_text = "Signed in as testuser using SSH key id_ed25519."
            screen.transition_to(screen.current_stage.__class__.DONE)
            screen._set_verification_branch(VerificationState.FAILED)
            await pilot.pause(delay=0.2)

            buttons = list(current_step_actions(screen).query(Button))

            assert screen.verification_state is VerificationState.FAILED
            assert screen.verification_ok is False
            assert "error" in screen.query_one("#done-summary-card", Card).classes
            assert list(screen.query("#done-btn")) == []
            assert screen.query_one("#failed-exit", Button).variant == "default"
            assert screen.query_one("#failed-retry", Button).variant == "primary"
            assert buttons[-1].id == "failed-retry"


async def test_setup_verification_ok_reactive_contribute_visibility(
    tmp_path: Path,
):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.tui.screens.setup.TranscriptDiscovery.find_transcripts", return_value=()):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.state.config = SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=ssh_key.path,
            )
            screen.done_display.summary_text = "Signed in as testuser using SSH key id_ed25519."
            screen.transition_to(screen.current_stage.__class__.DONE)
            screen._set_verification_branch(VerificationState.VERIFIED)
            await pilot.pause(delay=0.2)

            assert screen.query_one("#done-btn", Button) is not None

            screen.verification_state = VerificationState.PENDING
            screen.verification_ok = False
            await pilot.pause()

            assert list(screen.query("#done-btn")) == []
            assert screen.query_one("#pending-retry", Button).label.plain == "Retry now"

            screen.verification_state = VerificationState.VERIFIED
            screen.verification_ok = True
            await pilot.pause()

            assert screen.query_one("#done-btn", Button).variant == "primary"


async def test_setup_rapid_toggle_cta_never_visible_on_non_verified_branch(
    tmp_path: Path,
):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.tui.screens.setup.TranscriptDiscovery.find_transcripts", return_value=()):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.state.config = SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=ssh_key.path,
            )
            screen.done_display.summary_text = "Signed in as testuser using SSH key id_ed25519."
            screen.transition_to(screen.current_stage.__class__.DONE)
            screen._set_verification_branch(VerificationState.VERIFIED)
            await pilot.pause(delay=0.2)

            for state_value in (
                VerificationState.FAILED,
                VerificationState.VERIFIED,
                VerificationState.PENDING,
                VerificationState.VERIFIED,
                VerificationState.FAILED,
            ):
                screen.verification_state = state_value
                screen.verification_ok = state_value is VerificationState.VERIFIED
                await pilot.pause()

                assert bool(list(screen.query("#done-btn"))) is (state_value is VerificationState.VERIFIED)


async def test_setup_pending_elapsed_ticks_pending_elapsed(tmp_path: Path):
    fake_clock = FakeMonotonic(100.0)
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.monotonic", new=fake_clock), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnauthorized(status=401),
         ):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)

            pending = screen.query_one("#pending-status", PendingStatus)

            assert pending.label == "Waiting for your key to propagate… 0:00"

            fake_clock.advance(61.0)
            screen._refresh_pending_status()
            await pilot.pause()

            assert pending.label == "Waiting for your key to propagate… 1:01"


async def test_setup_pending_retry_cadence_auto_polls_pending_retry_cadence(tmp_path: Path):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
    calls: list[float] = []

    async def probe(*_) -> AuthUnauthorized:
        calls.append(wall_monotonic())
        return AuthUnauthorized(status=401)

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.setup_state.PENDING_RETRY_SECONDS", 0.2), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new=probe):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.9)

            assert len(calls) >= 3
            assert all(0.12 <= later - earlier <= 0.45 for earlier, later in zip(calls, calls[1:]))


async def test_setup_pending_propagation_window_transitions_to_failed_propagation_window(tmp_path: Path):
    fake_clock = FakeMonotonic()
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.monotonic", new=fake_clock), \
         patch("cc_sentiment.tui.setup_state.PENDING_PROPAGATION_WINDOW_SECONDS", 5.0), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnauthorized(status=401),
         ):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)

            assert screen.verification_state is VerificationState.PENDING

            fake_clock.advance(4.9)
            screen.verify_server_config()
            await pilot.pause(delay=0.3)
            assert screen.verification_state is VerificationState.PENDING

            fake_clock.advance(0.2)
            screen.verify_server_config()
            await pilot.pause(delay=0.3)

            assert screen.verification_state is VerificationState.FAILED
            assert screen.query_one("#failed-exit", Button).variant == "default"
            assert screen.query_one("#failed-retry", Button).variant == "primary"


@pytest.mark.parametrize(
    ("action", "selected_key", "expected"),
    [
        (VerificationAction.MANUAL, SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment=""), ("Paste your public key at", "github.com/settings/ssh/new")),
        (VerificationAction.OPENPGP, GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096"), ("verification link", "keys.openpgp.org")),
        (VerificationAction.GITHUB_SSH, SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment=""), ("GitHub", "propagate")),
        (VerificationAction.GITHUB_GPG, GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096"), ("GitHub", "propagate")),
        (VerificationAction.GIST, None, ("gist", "retry")),
    ],
)
async def test_setup_pending_verification_action_instructions_cover_surviving_actions(
    tmp_path: Path,
    action: VerificationAction,
    selected_key: SSHKeyInfo | GPGKeyInfo | None,
    expected: tuple[str, str],
):
    state = AppState()

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.selected_key = selected_key
            screen.done_display.verification_action = action
            screen.transition_to(screen.current_stage.__class__.DONE)
            screen._set_verification_branch(VerificationState.PENDING)
            await pilot.pause(delay=0.2)

            instructions = str(screen.query_one("#done-instructions", Static).render()).lower()

            assert expected[0].lower() in instructions
            assert expected[1].lower() in instructions


async def test_setup_pending_exit_preserves_state_exit_preserves(tmp_path: Path):
    state = AppState()
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
    harness = SetupHarness(state)

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnauthorized(status=401),
         ):
        async with harness.run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)
            await wait_for_condition(
                pilot,
                predicate=lambda: screen.verification_state is VerificationState.PENDING,
                failure_message=lambda: f"expected pending verification state, got {screen.verification_state}",
            )
            assert len(list(screen.query("#pending-exit"))) == 1

            before = state_file.read_text()

            await pilot.click("#pending-exit")
            await pilot.pause()

            assert harness.dismissed is False
            assert state_file.read_text() == before


async def test_setup_pending_retry_immediate_does_not_reset_elapsed_retry_immediate(tmp_path: Path):
    fake_clock = FakeMonotonic()
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
    calls: list[float] = []

    async def probe(*_) -> AuthUnauthorized:
        calls.append(fake_clock())
        return AuthUnauthorized(status=401)

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.monotonic", new=fake_clock), \
         patch("cc_sentiment.tui.setup_state.PENDING_RETRY_SECONDS", 60.0), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new=probe):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)

            fake_clock.advance(5.0)
            screen._refresh_pending_status()
            await pilot.pause()

            assert screen.query_one("#pending-status", PendingStatus).label.endswith("0:05")
            assert len(list(screen.query("#pending-retry"))) == 1

            await pilot.click("#pending-retry")
            await pilot.pause(delay=0.3)

            fake_clock.advance(1.0)
            screen._refresh_pending_status()
            await pilot.pause()

            assert calls == [0.0, 5.0]
            assert screen.query_one("#pending-status", PendingStatus).label.endswith("0:06")


async def test_setup_pending_network_drop_pending_keeps_pending_network_drop_pending(tmp_path: Path):
    request = httpx.Request("POST", "https://sentiments.cc/verify")
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new=AsyncMock(side_effect=[AuthUnauthorized(status=401), httpx.ConnectError("boom", request=request)]),
         ):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)
            await wait_for_condition(
                pilot,
                predicate=lambda: screen.verification_state is VerificationState.PENDING,
                failure_message=lambda: f"expected pending verification state, got {screen.verification_state}",
            )
            assert len(list(screen.query("#pending-retry"))) == 1

            await pilot.click("#pending-retry")
            await pilot.pause(delay=0.3)

            assert screen.verification_state is VerificationState.PENDING
            assert screen.query_one("#pending-status", PendingStatus) is not None
            assert "temporarily unreachable" in str(screen.query_one("#done-instructions", Static).render()).lower()


async def test_setup_pending_monotonic_clock_ignores_wall_time_skew_monotonic_clock(tmp_path: Path):
    fake_clock = FakeMonotonic(50.0)
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.monotonic", new=fake_clock), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnauthorized(status=401),
         ):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)

            fake_clock.advance(30.0)
            screen._refresh_pending_status()
            await pilot.pause()
            label = screen.query_one("#pending-status", PendingStatus).label

            sleep(0.1)
            screen._refresh_pending_status()
            await pilot.pause()

            assert label.endswith("0:30")
            assert screen.query_one("#pending-status", PendingStatus).label == label


async def test_setup_verify_result_maps_five_xx_and_network_drop_to_identical_pending_unreachable(
    tmp_path: Path,
):
    fake_clock = FakeMonotonic(100.0)
    state = AppState()

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.monotonic", new=fake_clock), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.transition_to(SetupStage.DONE)
            screen.done_display.summary_text = "Signed in as testuser using SSH key id_ed25519."
            screen.done_display.verification_action = VerificationAction.GITHUB_SSH
            screen.verification_poll.restart(fake_clock())

            screen._on_verify_result(AuthServerError(status=502))
            await pilot.pause()
            five_xx_signature = pending_branch_signature(screen)
            five_xx_retry_at = screen.verification_poll.next_retry_at

            screen.verification_poll.restart(fake_clock())
            screen._verification_detail = ""
            screen._on_verify_result(AuthUnreachable(detail="no net"))
            await pilot.pause()

            assert pending_branch_signature(screen) == five_xx_signature
            assert five_xx_retry_at == fake_clock() + 10.0
            assert screen.verification_poll.next_retry_at == fake_clock() + 10.0
            assert "temporarily unreachable" in str(screen.query_one("#done-instructions", Static).render()).lower()


async def test_setup_pending_five_xx_retry_can_recover_to_verified(tmp_path: Path):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
    probe = AsyncMock(return_value=AuthServerError(status=502))

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new=probe):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)
            await wait_for_condition(
                pilot,
                predicate=lambda: screen.verification_state is VerificationState.PENDING,
                failure_message=lambda: f"expected pending verification state, got {screen.verification_state}",
            )
            assert len(list(screen.query("#pending-retry"))) == 1

            before_retry_calls = probe.await_count
            probe.return_value = AuthOk()
            await pilot.click("#pending-retry")
            await wait_for_condition(
                pilot,
                predicate=lambda: probe.await_count > before_retry_calls,
                failure_message=lambda: f"expected retry probe after click, got {probe.await_count} total probes",
            )
            await wait_for_condition(
                pilot,
                predicate=lambda: screen.verification_state is VerificationState.VERIFIED,
                failure_message=lambda: f"expected verified state, got {screen.verification_state}",
            )
            assert screen.verification_state is VerificationState.VERIFIED
            assert screen.verification_ok is True
            assert screen.query_one("#done-btn", Button).label.plain == "Contribute my stats"


async def test_setup_pending_sentiments_five_xx_unreachable_uses_pending_copy_five_xx_unreachable(
    tmp_path: Path,
):
    state = AppState()
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch.object(AppState, "state_path", return_value=tmp_path / "state.json"), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthServerError(status=502),
         ):
        async with SetupHarness(state).run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause(delay=0.3)

            assert screen.verification_state is VerificationState.PENDING
            assert list(screen.query("#done-btn")) == []
            assert "temporarily unreachable" in str(screen.query_one("#done-instructions", Static).render()).lower()


async def test_setup_remote_openpgp_five_xx_unreachable_marks_row_warning_five_xx_unreachable(
    no_auto_setup,
):
    request = httpx.Request("GET", "https://keys.openpgp.org/vks/v1/by-fingerprint/F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A")
    response = httpx.Response(503, request=request)
    gpg_key = GPGKeyInfo(
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        email="test@example.com",
        algo="ed25519",
    )

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gpg_key_on_github", return_value=False), \
         patch(
             "cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_openpgp_key",
             side_effect=httpx.HTTPStatusError("boom", request=request, response=response),
         ):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            screen.on_discovery_next()
            await pilot.pause(delay=0.6)

            assert table_rows(screen.query_one("#remote-checks", DataTable)) == [
                ("—", "GitHub", "Not on GitHub yet"),
                ("?", "keys.openpgp.org", "Couldn't reach keys.openpgp.org"),
            ]


async def test_setup_cancel_dismisses_false(no_auto_setup):
    harness = SetupHarness(AppState())
    async with harness.run_test() as pilot:
        await pilot.pause(delay=0.3)
        await pilot.press("escape")
        await pilot.pause()
        assert harness.dismissed is False


async def test_setup_link_my_key_collapses_to_one_radioset_and_no_manual_button(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.discovery.discovered_keys = [ssh_key]
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            assert len(list(screen.query("#step-upload RadioSet"))) == 1
            assert list(screen.query("#upload-skip Button")) == []
            assert screen.upload_plan.actions == ["github-ssh", "manual"]
            assert radio_labels(screen.query_one("#upload-options", RadioSet)) == [
                "Link via GitHub (gh)",
                "Show me the key; I'll add it myself",
            ]
            assert screen.query_one("#upload-go", Button).disabled is False
            assert screen.query_one("#upload-go", Button).label.plain == "Link my key"


async def test_setup_hide_gh_link_option_when_cli_missing(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            radio = screen.query_one("#upload-options", RadioSet)

            assert screen.upload_plan.actions == ["manual"]
            assert radio.display is False
            assert radio_labels(radio) == ["Show me the key; I'll add it myself"]
            assert all("GitHub" not in label for label in radio_labels(radio))
            assert not any(button.disabled for button in radio.query(RadioButton))
            assert screen.query_one("#upload-go", Button).label.plain == "Show me the key"


async def test_setup_hide_gh_link_option_when_unauthed(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            radio = screen.query_one("#upload-options", RadioSet)

            assert screen.upload_plan.actions == ["manual"]
            assert radio.display is False
            assert radio_labels(radio) == ["Show me the key; I'll add it myself"]
            assert all("GitHub" not in label for label in radio_labels(radio))
            assert not any(button.disabled for button in radio.query(RadioButton))


async def test_setup_pre_selected_best_link_option(no_auto_setup):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = gpg_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            radio = screen.query_one("#upload-options", RadioSet)

            assert screen.upload_plan.actions == ["github-gpg", "openpgp", "manual"]
            assert radio.pressed_index == 0
            assert radio_labels(radio) == [
                "Link via GitHub (gh)",
                "Publish to keys.openpgp.org",
                "Show me the key; I'll add it myself",
            ]


async def test_setup_pre_selected_best_link_option_without_gh_uses_openpgp(no_auto_setup):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = ""
            screen.selected_key = gpg_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            radio = screen.query_one("#upload-options", RadioSet)

            assert screen.upload_plan.actions == ["openpgp", "manual"]
            assert radio.pressed_index == 0
            assert radio_labels(radio) == [
                "Publish to keys.openpgp.org",
                "Show me the key; I'll add it myself",
            ]


async def test_step_header_renders_title_and_muted_explainer():
    async with PrimitiveHarness(
        StepHeader("Link your key", "We'll show the full public key.", id="header"),
    ).run_test() as pilot:
        await pilot.pause()
        header = pilot.app.query_one("#header", StepHeader)
        title, explainer = list(header.query(Static))

        assert str(title.render()) == "Link your key"
        assert "muted" in explainer.classes
        assert str(explainer.render()) == "We'll show the full public key."
        assert header.styles.margin.bottom == 1


async def test_step_header_omits_optional_explainer():
    async with PrimitiveHarness(StepHeader("Link your key", None, id="header")).run_test() as pilot:
        await pilot.pause()
        header = pilot.app.query_one("#header", StepHeader)

        assert len(list(header.query(Static))) == 1


def test_step_actions_rejects_multiple_primary_buttons():
    with pytest.raises(ValueError, match="exactly one primary"):
        StepActions(
            Button("Back", variant="primary"),
            primary=Button("Next", variant="primary"),
        )


async def test_step_actions_renders_primary_rightmost():
    async with PrimitiveHarness(
        StepActions(
            Button("Back", id="back"),
            Button("Skip", id="skip"),
            primary=Button("Next", id="next", variant="primary"),
            id="actions",
        ),
    ).run_test() as pilot:
        await pilot.pause()
        actions = pilot.app.query_one("#actions", StepActions)
        buttons = list(actions.query(Button))

        assert actions.styles.align_horizontal == "right"
        assert buttons[-1].id == "next"
        assert buttons[-1].variant == "primary"


async def test_step_body_applies_shared_spacing_rules():
    table = DataTable(id="table")

    async with PrimitiveHarness(
        StepBody(
            Input(id="input"),
            RadioSet(RadioButton("SSH"), id="radio"),
            table,
            Static("", id="status", classes="status-line"),
            StepActions(Button("Back"), primary=Button("Next", variant="primary")),
            id="body",
        ),
    ).run_test() as pilot:
        await pilot.pause()
        body = pilot.app.query_one("#body", StepBody)

        assert body.styles.margin.bottom == 1
        assert pilot.app.query_one("#input", Input).styles.margin.bottom == 1
        assert pilot.app.query_one("#radio", RadioSet).styles.max_height.value == 12
        assert pilot.app.query_one("#table", DataTable).styles.max_height.value == 12
        assert pilot.app.query_one("#status", Static).styles.min_height.value == 1


async def test_key_preview_scrolls_without_losing_focus():
    async with KeyPreviewHarness().run_test(size=(80, 18)) as pilot:
        await pilot.pause()
        preview = pilot.app.query_one("#preview", KeyPreview)

        assert "-----END PGP PUBLIC KEY BLOCK-----" in str(preview.query_one(Static).render())

        preview.focus()
        await pilot.pause()
        before = preview.scroll_y

        await pilot.press("pagedown")
        await pilot.pause()

        assert pilot.app.focused is preview
        assert preview.scroll_y > before

        await pilot.press("tab")
        await pilot.pause()

        assert pilot.app.focused == pilot.app.query_one("#after", Button)


async def test_pending_status_updates_reactive_label_and_spinner():
    allowed = {"⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"}

    async with PrimitiveHarness(PendingStatus("Checking GitHub", id="pending")).run_test() as pilot:
        await pilot.pause()
        pending = pilot.app.query_one("#pending", PendingStatus)
        spinner, label = list(pending.children)
        first = str(spinner.render())

        pending.label = "Checking keys.openpgp.org"
        await pilot.pause(delay=0.2)
        second = str(spinner.render())

        assert first in allowed
        assert second in allowed
        assert "muted" in label.classes
        assert str(label.render()) == "Checking keys.openpgp.org"


async def test_palette_classes_apply_shared_theme_rules():
    async with PrimitiveHarness(
        Label("Muted", id="muted", classes="muted"),
        Label("Success", id="success", classes="success"),
        Label("Warning", id="warning", classes="warning"),
        Label("Error", id="error", classes="error"),
        Static("code", id="code", classes="code"),
    ).run_test() as pilot:
        await pilot.pause()
        muted = pilot.app.query_one("#muted", Label)
        success = pilot.app.query_one("#success", Label)
        warning = pilot.app.query_one("#warning", Label)
        error = pilot.app.query_one("#error", Label)
        code = pilot.app.query_one("#code", Static)

        assert muted.styles.color != success.styles.color
        assert success.styles.color != warning.styles.color
        assert warning.styles.color != error.styles.color
        assert code.styles.background is not None
        assert code.styles.border_top[0] == "round"
        assert code.styles.padding.left == 1
        assert code.styles.padding.right == 1


async def test_try_existing_gist_returns_config_when_found():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=Path("/home/.cc-sentiment/keys/id_ed25519")), \
         patch("cc_sentiment.tui.status.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.status.KeyDiscovery.find_cc_sentiment_gist_id", return_value="abcdef1234567890abcd"):
        result = await setup.try_existing_gist("octocat")

    assert isinstance(result, GistConfig)
    assert result.contributor_id == ContributorId("octocat")
    assert result.gist_id == "abcdef1234567890abcd"


async def test_try_existing_gist_returns_none_when_no_local_keypair():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=None):
        assert await setup.try_existing_gist("octocat") is None


async def test_try_existing_gist_returns_none_when_gh_not_authed():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=Path("/home/.cc-sentiment/keys/id_ed25519")), \
         patch("cc_sentiment.tui.status.KeyDiscovery.gh_authenticated", return_value=False):
        assert await setup.try_existing_gist("octocat") is None


async def test_try_existing_gist_returns_none_when_no_gist_found():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=Path("/home/.cc-sentiment/keys/id_ed25519")), \
         patch("cc_sentiment.tui.status.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.status.KeyDiscovery.find_cc_sentiment_gist_id", return_value=None):
        assert await setup.try_existing_gist("octocat") is None

def test_setup_malformed_gist_list_returns_none_malformed_gist_list() -> None:
    with (
        patch.object(KeyDiscovery, "has_tool", return_value=True),
        patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            ["gh", "gist", "list"],
            0,
            "\n\tcc-sentiment public key\t2 files\tpublic\tupdated\nnot-a-tsv-row\n",
            "",
        )
        assert KeyDiscovery.find_cc_sentiment_gist_id() is None


def test_setup_passphrase_ssh_skips_unusable_key_passphrase_ssh() -> None:
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with (
        patch.object(KeyDiscovery, "fetch_github_ssh_keys", return_value=("ssh-ed25519 AAAA key1",)),
        patch.object(KeyDiscovery, "find_ssh_keys", return_value=(ssh_key,)),
        patch.object(SSHBackend, "fingerprint", return_value="ssh-ed25519 AAAA"),
        patch("cc_sentiment.signing.discovery.subprocess.run", side_effect=subprocess.TimeoutExpired(["ssh-keygen"], 2)),
    ):
        assert KeyDiscovery.match_ssh_key("testuser") is None


def test_setup_multi_ssh_priority_prefers_first_match_multi_ssh_priority() -> None:
    ed25519 = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    rsa = SSHKeyInfo(path=Path("/home/.ssh/id_rsa"), algorithm="ssh-rsa", comment="user@host")

    with (
        patch.object(
            KeyDiscovery,
            "fetch_github_ssh_keys",
            return_value=("ssh-ed25519 AAAA key1", "ssh-rsa BBBB key2"),
        ),
        patch.object(KeyDiscovery, "find_ssh_keys", return_value=(ed25519, rsa)),
        patch.object(KeyDiscovery, "ssh_key_usable", return_value=True),
        patch.object(SSHBackend, "fingerprint", side_effect=("ssh-ed25519 AAAA", "ssh-rsa BBBB")),
    ):
        result = KeyDiscovery.match_ssh_key("testuser")

    assert result is not None
    assert result.private_key_path == ed25519.path


async def test_setup_ssh_over_gpg_prefers_github_ssh_ssh_over_gpg():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    setup = AutoSetup(state, StatusEmitter(widget=widget))
    ssh_config = SSHConfig(
        contributor_id=ContributorId("octocat"),
        key_path=Path("/home/.ssh/id_ed25519"),
    )
    gpg_key = GPGKeyInfo(
        fpr="ABCDEF1234567890ABCDEF1234567890ABCDEF12",
        email="octocat@example.com",
        algo="ed25519",
    )

    with (
        patch.object(AutoSetup, "detect_username", new=AsyncMock(return_value="octocat")),
        patch.object(AutoSetup, "try_github_ssh", new=AsyncMock(return_value=ssh_config)) as try_github_ssh,
        patch.object(AutoSetup, "probe_and_save", new=AsyncMock(return_value=True)) as probe_and_save,
        patch.object(AutoSetup, "try_github_gpg", new=AsyncMock()) as try_github_gpg,
        patch.object(AutoSetup, "try_existing_gist", new=AsyncMock()) as try_existing_gist,
        patch.object(AutoSetup, "find_local_gpg", new=AsyncMock(return_value=(gpg_key,))) as find_local_gpg,
        patch.object(AutoSetup, "try_openpgp", new=AsyncMock()) as try_openpgp,
    ):
        assert await setup.run() == (True, "octocat")

    try_github_ssh.assert_awaited_once_with("octocat")
    probe_and_save.assert_awaited_once_with(ssh_config)
    try_github_gpg.assert_not_awaited()
    try_existing_gist.assert_not_awaited()
    find_local_gpg.assert_not_awaited()
    try_openpgp.assert_not_awaited()


async def test_setup_no_keys_with_gh_auth_uses_gist_mode_generation_gist(no_auto_setup):
    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen.discovery.generation_mode == "gist"
            assert screen.query_one("#discovery-next", Button).disabled is False


async def test_setup_no_gh_auth_with_ssh_keygen_picks_ssh_mode_generation_ssh(no_auto_setup):
    def has_tool(name: str) -> bool:
        return name == "ssh-keygen"

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", side_effect=has_tool):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen.discovery.generation_mode == "ssh"
            assert screen.query_one("#discovery-next", Button).disabled is False


async def test_setup_existing_keys_hide_create_new_option_generation_gist(no_auto_setup):
    ssh_keys = (SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host"),)

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=ssh_keys), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen.discovery.generation_mode == "gist"
            labels = [str(rb.label) for rb in screen.query("#key-select RadioButton").results(RadioButton)]
            assert screen.discovery.generation_radio_index is None
            assert "Create a new cc-sentiment key" not in labels


async def test_setup_generation_ssh_routes_to_remote(tmp_path: Path, no_auto_setup):
    state = AppState()
    key_path = tmp_path / "id_ed25519"
    pub_path = tmp_path / "id_ed25519.pub"
    pub_path.write_text("ssh-ed25519 AAAA cc-sentiment\n")

    def has_tool(name: str) -> bool:
        return name == "ssh-keygen"

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", side_effect=has_tool), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.generate_gist_keypair", return_value=key_path), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_github_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupHarness(state).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            screen.on_discovery_next()
            await pilot.pause(delay=0.5)

            assert isinstance(screen.selected_key, SSHKeyInfo)
            assert screen.selected_key.path == key_path
            assert screen.query_one(ContentSwitcher).current == "step-remote"


async def test_setup_generation_gist_routes_directly_to_done_verified(tmp_path: Path, no_auto_setup, auth_ok):
    state = AppState()
    state_file = tmp_path / "state.json"
    key_path = tmp_path / "id_ed25519"

    with patch.object(AppState, "state_path", return_value=state_file), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.generate_gist_keypair", return_value=key_path), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.create_gist", return_value="abcdef1234567890abcd"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            screen.on_discovery_next()
            await pilot.pause(delay=0.5)

            assert isinstance(state.config, GistConfig)
            assert state.config.contributor_id == ContributorId("testuser")
            assert state.config.gist_id == "abcdef1234567890abcd"
            assert state.config.key_path == key_path
            assert screen.query_one(ContentSwitcher).current == "step-done"
            assert SetupStage.REMOTE not in screen.transition_history
            assert SetupStage.UPLOAD not in screen.transition_history
            assert screen.query_one("#done-btn", Button).label == "Contribute my stats"


async def test_setup_generation_gpg_routes_to_remote(tmp_path: Path, no_auto_setup):
    state = AppState()
    generated_key = GPGKeyInfo(
        fpr="ABCDEF1234567890ABCDEF1234567890ABCDEF12",
        email="alice@users.noreply.github.com",
        algo="ed25519",
    )
    batch_inputs: list[str] = []

    def has_tool(name: str) -> bool:
        return name == "gpg"

    def run_gpg(command: list[str], **kwargs):
        batch_inputs.append(Path(command[-1]).read_text())
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", side_effect=((), (generated_key,))), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", side_effect=has_tool), \
         patch("cc_sentiment.tui.screens.setup.subprocess.run", side_effect=run_gpg), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_openpgp_key", return_value=None):
        async with SetupHarness(state).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "alice"
            screen._switch_to_discovery()
            await wait_for_condition(
                pilot,
                lambda: screen.discovery.generation_mode == "gpg" and screen.query_one("#discovery-next", Button).disabled is False,
                lambda: (
                    "Timed out waiting for discovery generation mode to become ready "
                    f"(stage={screen.current_stage.value}, mode={screen.discovery.generation_mode!r}, "
                    f"disabled={screen.query_one('#discovery-next', Button).disabled})"
                ),
            )

            screen.on_discovery_next()
            await wait_for_condition(
                pilot,
                lambda: screen.current_stage is SetupStage.REMOTE,
                lambda: f"Timed out waiting for {SetupStage.REMOTE.value}; saw {screen.current_stage.value}",
            )

            assert batch_inputs
            assert "Key-Type: eddsa" in batch_inputs[0]
            assert "Name-Email: alice@users.noreply.github.com" in batch_inputs[0]
            assert isinstance(screen.selected_key, GPGKeyInfo)
            assert screen.selected_key.fpr == generated_key.fpr
            assert screen.query_one(ContentSwitcher).current == "step-remote"


async def test_setup_upload_options_gpg_shows_openpgp(no_auto_setup):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = gpg_key
            screen.discovery.discovered_keys = [gpg_key]
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            assert "github-gpg" in screen.upload_plan.actions
            assert "openpgp" in screen.upload_plan.actions
            assert radio_labels(screen.query_one("#upload-options", RadioSet)) == [
                "Link via GitHub (gh)",
                "Publish to keys.openpgp.org",
                "Show me the key; I'll add it myself",
            ]


async def test_setup_upload_action_github_ssh_failure_routes_to_failed(no_auto_setup, tmp_path: Path):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
    state = AppState()
    state_file = tmp_path / "state.json"

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch("cc_sentiment.tui.screens.setup.subprocess.run", return_value=subprocess.CompletedProcess(["gh"], 1, "", "boom")):
        async with SetupHarness(state).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause(delay=0.3)

            assert screen.query_one(ContentSwitcher).current == "step-done"
            assert screen.verification_state is VerificationState.FAILED
            assert "Something went wrong: boom" in str(screen.query_one("#done-instructions", Static).render())
            assert list(screen.query("#done-btn")) == []
            assert screen.query_one("#failed-retry", Button).label.plain == "Retry"
            assert AppState.model_validate_json(state_file.read_text()).config == SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=ssh_key.path,
            )


async def test_setup_failed_retry_for_upload_returns_to_upload(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch.object(SetupScreen, "verify_server_config") as verify:
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.done_display.verification_action = VerificationAction.GITHUB_SSH
            screen.done_display.upload_failure_text = "Something went wrong: boom"
            screen.done_display.failed_retry_target = RetryTarget.UPLOAD
            screen.done_display.summary_text = "Signed in as testuser using SSH key id_ed25519."
            screen.transition_to(SetupStage.DONE)
            screen._set_verification_branch(VerificationState.FAILED)
            await pilot.pause(delay=0.2)

            await pilot.click("#failed-retry")
            await pilot.pause(delay=0.3)

            assert screen.query_one(ContentSwitcher).current == "step-upload"
            assert radio_labels(screen.query_one("#upload-options", RadioSet)) == [
                "Link via GitHub (gh)",
                "Show me the key; I'll add it myself",
            ]
            verify.assert_not_called()


async def test_setup_upload_action_manual_uses_non_error_tone(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    def finish_upload_only(screen: SetupScreen) -> None:
        screen.actions.upload_running = False

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch.object(SetupScreen, "_save_and_finish", autospec=True, side_effect=finish_upload_only):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            await pilot.click("#upload-go")
            await pilot.pause(delay=0.3)

            result = screen.query_one("#upload-result", Static)

            assert "https://github.com/settings/ssh/new" in str(result.render())
            assert "error" not in result.classes


async def test_setup_upload_action_openpgp_warns_and_saves(no_auto_setup):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="alice@example.com", algo="rsa4096")

    def finish_upload_only(screen: SetupScreen) -> None:
        screen.actions.upload_running = False

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.upload_openpgp_key", return_value=("token", {"alice@example.com": "unpublished"})), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.request_openpgp_verify", return_value={"alice@example.com": "pending"}), \
         patch.object(SetupScreen, "_save_and_finish", autospec=True, side_effect=finish_upload_only):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = ""
            screen.selected_key = gpg_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            await pilot.click(list(screen.query("#upload-options RadioButton").results(RadioButton))[0])
            await pilot.pause()
            await pilot.click("#upload-go")
            await pilot.pause(delay=0.3)

            result = screen.query_one("#upload-result", Static)

            assert "Check your email (alice@example.com) for a verification link." in str(result.render())
            assert "warning" in result.classes


async def test_setup_upload_action_github_gpg_cleans_temp_file_on_exception(no_auto_setup, tmp_path: Path):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="alice@example.com", algo="rsa4096")
    temp_path = tmp_path / "upload.asc"

    class FixedTempFile:
        def __enter__(self):
            self.handle = temp_path.open("w")
            return self.handle

        def __exit__(self, exc_type, exc, tb):
            self.handle.close()
            return False

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"), \
         patch("cc_sentiment.tui.screens.setup.tempfile.NamedTemporaryFile", return_value=FixedTempFile()), \
         patch("cc_sentiment.tui.screens.setup.subprocess.run", side_effect=subprocess.TimeoutExpired(["gh", "gpg-key", "add"], 30)):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = gpg_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause(delay=0.3)

            assert screen.query_one(ContentSwitcher).current == "step-done"
            assert screen.verification_state is VerificationState.FAILED
            assert "Something went wrong" in str(screen.query_one("#done-instructions", Static).render())
            assert list(screen.query("#done-btn")) == []
            assert temp_path.exists() is False


async def test_setup_gh_version_drift_keeps_github_option_available(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=True), \
         patch(
             "cc_sentiment.signing.discovery.subprocess.run",
             return_value=subprocess.CompletedProcess(["gh", "auth", "status"], 0, "logged in", "warning: update available"),
         ), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            assert screen.upload_plan.actions == ["github-ssh", "manual"]
            assert "Link via GitHub (gh)" in radio_labels(screen.query_one("#upload-options", RadioSet))


async def test_setup_button_contract_uses_step_actions_and_single_primary_per_step(no_auto_setup):
    async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen

        for step_id in ("step-username", "step-discovery", "step-remote", "step-upload", "step-done"):
            step = screen.query_one(f"#{step_id}", Vertical)
            buttons = list(step.query_one(StepActions).query(Button))

            assert len([button for button in buttons if button.variant == "primary"]) == 1


async def test_setup_button_contract_primary_is_rightmost(no_auto_setup):
    async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen

        for step_id in ("step-username", "step-discovery", "step-remote", "step-upload"):
            screen.query_one(ContentSwitcher).current = step_id
            await pilot.pause()
            buttons = list(current_step_actions(screen).query(Button))
            primary = next(button for button in buttons if button.variant == "primary")
            secondary_x = [button.region.x for button in buttons if button is not primary]

            assert primary.region.x > max(secondary_x)


async def test_setup_enter_advances_username_with_valid_input(no_auto_setup):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.github.com/users/testuser"),
    )

    with patch("cc_sentiment.tui.screens.setup.httpx.get", return_value=response), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "testuser"

            await pilot.press("enter")
            await pilot.pause(delay=0.3)

            assert screen.query_one(ContentSwitcher).current == "step-discovery"


async def test_setup_username_mixed_case_and_hyphen_round_trip_verbatim(no_auto_setup):
    requests: list[str] = []

    def fake_get(url: str, timeout: float) -> httpx.Response:
        requests.append(url)
        return httpx.Response(404, request=httpx.Request("GET", url))

    with patch("cc_sentiment.tui.screens.setup.httpx.get", side_effect=fake_get):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "Alice-01"

            await pilot.click("#username-next")
            await pilot.pause(delay=0.3)

            assert requests == ["https://api.github.com/users/Alice-01"]
            assert screen.query_one(ContentSwitcher).current == "step-username"
            assert "GitHub user 'Alice-01' not found" in str(screen.query_one("#username-status", Static).render())


async def test_setup_username_special_chars_round_trip_verbatim(no_auto_setup):
    requests: list[str] = []

    def fake_get(url: str, timeout: float) -> httpx.Response:
        requests.append(url)
        return httpx.Response(404, request=httpx.Request("GET", url))

    with patch("cc_sentiment.tui.screens.setup.httpx.get", side_effect=fake_get):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "hello$world"

            await pilot.click("#username-next")
            await pilot.pause(delay=0.3)

            assert requests == ["https://api.github.com/users/hello$world"]
            assert screen.query_one(ContentSwitcher).current == "step-username"
            assert "GitHub user 'hello$world' not found" in str(screen.query_one("#username-status", Static).render())


async def test_setup_enter_advances_username_blocks_empty_input(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen
        screen.query_one("#username-input", Input).value = ""

        await pilot.press("enter")
        await pilot.pause()

        assert screen.query_one(ContentSwitcher).current == "step-username"
        assert "Username is required" in str(screen.query_one("#username-status", Static).render())


async def test_setup_enter_advances_discovery_when_enabled(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch.object(SetupScreen, "check_remotes") as mock_check_remotes:
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            await pilot.press("enter")
            await pilot.pause()

            assert screen.query_one(ContentSwitcher).current == "step-discovery"
            assert screen.actions.discovery_action_running is True
            mock_check_remotes.assert_called_once()


async def test_setup_enter_advances_remote_to_upload(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch.object(SetupScreen, "_populate_upload_options", new_callable=AsyncMock) as mock_populate:
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-remote"
            screen.query_one("#remote-next", Button).disabled = False
            screen.remote_check.key_on_remote = False

            await pilot.press("enter")
            await pilot.pause()

            assert screen.query_one(ContentSwitcher).current == "step-upload"
            mock_populate.assert_awaited_once()


async def test_setup_enter_advances_upload_and_done(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    harness = SetupHarness(AppState())

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch("cc_sentiment.tui.screens.setup.subprocess.run", return_value=subprocess.CompletedProcess(["gh"], 0, "", "")), \
         patch.object(SetupScreen, "_save_and_finish") as mock_save:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause(delay=0.1)

            assert mock_save.call_count == 1

            mock_save.reset_mock()
            screen.query_one(ContentSwitcher).current = "step-done"
            await pilot.pause()

            await pilot.press("enter")
            await pilot.pause()

            assert harness.dismissed is True


@pytest.mark.parametrize("step_id", ["step-loading", "step-username", "step-discovery", "step-remote", "step-upload", "step-done"])
@pytest.mark.parametrize("key", ["escape", "ctrl+c"])
async def test_setup_escape_cancels_from_every_step(no_auto_setup, step_id: str, key: str):
    harness = SetupHarness(AppState())

    async with harness.run_test() as pilot:
        await pilot.pause(delay=0.3)
        pilot.app.screen.query_one(ContentSwitcher).current = step_id
        await pilot.pause()

        await pilot.press(key)
        await pilot.pause()

        assert harness.dismissed is False


async def test_setup_focus_primary_on_interactive_step_entrance(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch.object(SetupScreen, "check_remotes"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen

            assert pilot.app.focused == screen.query_one("#username-input", Input)

            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)
            assert pilot.app.focused == screen.query_one("#discovery-next", Button)

            screen.query_one(ContentSwitcher).current = "step-remote"
            screen._enable_remote_next()
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#remote-next", Button)

            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#upload-go", Button)

            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#done-btn", Button)


async def test_setup_tab_order_body_secondary_primary(no_auto_setup):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen

            assert pilot.app.focused == screen.query_one("#username-input", Input)
            await pilot.press("tab")
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#username-skip", Button)
            await pilot.press("tab")
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#username-next", Button)

            screen.username = "testuser"
            screen.selected_key = gpg_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            radio = screen.query_one("#upload-options", RadioSet)
            radio.focus()
            await pilot.pause()

            await pilot.press("tab")
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#upload-key-text", KeyPreview)
            await pilot.press("tab")
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#upload-back", Button)
            await pilot.press("tab")
            await pilot.pause()
            assert pilot.app.focused == screen.query_one("#upload-go", Button)


async def test_setup_double_enter_username_starts_one_validation(no_auto_setup):
    calls: list[str] = []

    def fake_get(url: str, timeout: float) -> httpx.Response:
        calls.append(url)
        sleep(0.1)
        return httpx.Response(200, request=httpx.Request("GET", url))

    with patch("cc_sentiment.tui.screens.setup.httpx.get", side_effect=fake_get), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "testuser"

            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.pause(delay=0.4)

            assert calls == ["https://api.github.com/users/testuser"]


async def test_setup_double_enter_discovery_starts_one_remote_check(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch.object(SetupScreen, "check_remotes", new=Mock()) as mock_check_remotes:
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.pause()

            assert mock_check_remotes.call_count == 1


async def test_setup_double_enter_remote_starts_one_upload_population(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch.object(SetupScreen, "_populate_upload_options", new_callable=AsyncMock) as mock_populate:
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-remote"
            screen.query_one("#remote-next", Button).disabled = False
            screen.remote_check.key_on_remote = False

            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.pause()

            assert mock_populate.await_count == 1


async def test_setup_double_enter_upload_starts_one_upload_worker(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    calls: list[list[str]] = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        sleep(0.1)
        return subprocess.CompletedProcess(args[0], 0, "", "")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch("cc_sentiment.tui.screens.setup.subprocess.run", side_effect=fake_run), \
         patch.object(SetupScreen, "_save_and_finish"):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            await pilot.press("enter")
            await pilot.press("enter")
            await pilot.pause(delay=0.4)

            assert len(calls) == 1


async def test_setup_state_machine_transition_to_tracks_stage_history(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen

        assert screen.current_stage.__class__.__name__ == "SetupStage"
        assert screen.current_stage.value == "step-username"
        assert [stage.value for stage in screen.transition_history] == [
            "step-loading",
            "step-username",
        ]

        screen.transition_to(screen.current_stage.__class__.DISCOVERY)
        await pilot.pause()

        assert screen.current_stage.value == "step-discovery"
        assert screen.query_one(ContentSwitcher).current == "step-discovery"
        assert visible_step_ids(screen) == ["step-discovery"]


async def test_setup_back_nav_discovery_preserves_username_input_and_status(no_auto_setup):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.github.com/users/testuser"),
    )
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.httpx.get", return_value=response), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "testuser"
            screen.query_one("#username-status", Static).update("Auto-detected: testuser")

            await pilot.click("#username-next")
            await pilot.pause(delay=0.3)
            await pilot.click("#discovery-back")
            await pilot.pause()

            assert screen.current_stage.value == "step-username"
            assert screen.query_one("#username-input", Input).value == "testuser"
            assert str(screen.query_one("#username-status", Static).render()) == "Auto-detected: testuser"
            assert len(list(screen.query("#username-input"))) == 1
            assert len(list(screen.query("#username-next"))) == 1


async def test_setup_discovery_reset_on_remote_back_no_duplicate(no_auto_setup):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.github.com/users/testuser"),
    )
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")
    find_ssh_keys = Mock(side_effect=((ssh_key,), (ssh_key,)))
    find_gpg_keys = Mock(side_effect=((gpg_key,), (gpg_key,)))

    with patch("cc_sentiment.tui.screens.setup.httpx.get", return_value=response), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", new=find_ssh_keys), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", new=find_gpg_keys), \
         patch.object(SetupScreen, "check_remotes", new=Mock()) as mock_check_remotes:
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "testuser"

            await pilot.click("#username-next")
            await pilot.pause(delay=0.3)

            radio = screen.query_one("#key-select", RadioSet)
            expected_keys = screen.discovery.discovered_keys
            expected_labels = radio_labels(radio)
            expected_generation_mode = screen.discovery.generation_mode
            expected_generation_index = screen.discovery.generation_radio_index

            await pilot.click(list(screen.query("#key-select RadioButton").results(RadioButton))[1])
            await pilot.pause()
            screen.on_discovery_next()
            await pilot.pause()
            screen.transition_to(screen.current_stage.__class__.REMOTE)
            await pilot.pause()
            await pilot.click("#remote-back")
            await pilot.pause(delay=0.3)

            assert screen.current_stage.value == "step-discovery"
            assert screen.discovery.discovered_keys is not expected_keys
            assert screen.discovery.discovered_keys == expected_keys
            assert screen.discovery.generation_mode == expected_generation_mode
            assert screen.discovery.generation_radio_index == expected_generation_index
            assert radio_labels(radio) == expected_labels
            assert list(screen.query("#step-discovery DataTable")) == []
            assert find_ssh_keys.call_count == 2
            assert find_gpg_keys.call_count == 2

            screen.on_discovery_next()
            await pilot.pause()

            assert screen.current_stage.value == "step-discovery"
            assert mock_check_remotes.call_count == 2


async def test_setup_back_nav_upload_preserves_remote_results_without_rerun(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch.object(SetupScreen, "_populate_upload_options", new_callable=AsyncMock), \
         patch.object(SetupScreen, "check_remotes", new=Mock()) as mock_check_remotes:
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.selected_key = ssh_key
            screen.transition_to(screen.current_stage.__class__.REMOTE)
            screen.query_one("#remote-next", Button).disabled = False
            screen.query_one("#remote-status", Static).update("Link this key next so the dashboard can verify your uploads.")
            table = screen.query_one("#remote-checks", DataTable)
            table.clear(columns=False)
            table.add_row("✓", "GitHub", "Found on GitHub")

            await pilot.click("#remote-next")
            await pilot.pause()
            await pilot.click("#upload-back")
            await pilot.pause()

            assert screen.current_stage.value == "step-remote"
            assert str(screen.query_one("#remote-status", Static).render()) == "Link this key next so the dashboard can verify your uploads."
            assert table_rows(screen.query_one("#remote-checks", DataTable)) == [("✓", "GitHub", "Found on GitHub")]
            assert screen.query_one("#remote-next", Button).disabled is False
            assert mock_check_remotes.call_count == 0


async def test_setup_back_nav_key_change_resets_downstream_upload_state(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)), \
         patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"), \
         patch.object(SetupScreen, "check_remotes", new=Mock()):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            screen.on_discovery_next()
            await pilot.pause()
            screen.transition_to(screen.current_stage.__class__.REMOTE)
            await pilot.pause()
            screen.query_one("#remote-next", Button).disabled = False
            await pilot.click("#remote-next")
            await pilot.pause(delay=0.3)
            screen.query_one("#upload-result", Static).update("Paste your public key at:\nhttps://github.com/settings/ssh/new")

            await pilot.click("#upload-back")
            await pilot.pause()
            await pilot.click("#remote-back")
            await pilot.pause()

            radio = screen.query_one("#key-select", RadioSet)
            list(screen.query("#key-select RadioButton").results(RadioButton))[1].toggle()
            await pilot.pause()
            assert radio.pressed_index == 1

            screen.on_discovery_next()
            await pilot.pause()
            screen.transition_to(screen.current_stage.__class__.REMOTE)
            await pilot.pause()
            screen.query_one("#remote-next", Button).disabled = False
            await pilot.click("#remote-next")
            await pilot.pause(delay=0.3)

            assert screen.current_stage.value == "step-upload"
            assert screen.upload_plan.actions == ["github-gpg", "openpgp", "manual"]
            assert radio_labels(screen.query_one("#upload-options", RadioSet)) == [
                "Link via GitHub (gh)",
                "Publish to keys.openpgp.org",
                "Show me the key; I'll add it myself",
            ]
            assert "github-ssh" not in screen.upload_plan.actions
            assert "PGP PUBLIC KEY BLOCK" in screen.query_one("#upload-key-text", KeyPreview).text
            assert str(screen.query_one("#upload-result", Static).render()) == ""


async def test_setup_check_remotes_cancel_on_remote_back(no_auto_setup):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.github.com/users/testuser"),
    )
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    remote_worker = Mock()

    with patch("cc_sentiment.tui.screens.setup.httpx.get", return_value=response), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=True), \
         patch(
             "cc_sentiment.signing.discovery.subprocess.run",
             return_value=subprocess.CompletedProcess(["gh", "auth", "status"], 0, "logged in", "warning: update available"),
         ) as mock_run, \
         patch.object(SetupScreen, "check_remotes", new=Mock(return_value=remote_worker)):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "testuser"

            await pilot.click("#username-next")
            await pilot.pause(delay=0.3)
            screen.on_discovery_next()
            await pilot.pause()
            screen.transition_to(screen.current_stage.__class__.REMOTE)
            await pilot.pause()
            await pilot.click("#remote-back")
            await pilot.pause()

            remote_worker.cancel.assert_called_once()
            assert mock_run.call_args_list
            assert {tuple(call.args[0]) for call in mock_run.call_args_list} == {("gh", "auth", "status")}


async def test_setup_check_remotes_cancel_on_key_selection_change(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")
    remote_worker = Mock()

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)):
        async with SetupHarness(AppState()).run_test(size=(80, 40)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            screen.remote_check.worker = remote_worker
            await pilot.click(list(screen.query("#key-select RadioButton").results(RadioButton))[1])
            await pilot.pause()

            remote_worker.cancel.assert_called_once()


async def test_setup_rapid_toggle_username_discovery_preserves_widget_state(no_auto_setup):
    response = httpx.Response(
        200,
        request=httpx.Request("GET", "https://api.github.com/users/testuser"),
    )
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.httpx.get", return_value=response), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "testuser"

            await pilot.click("#username-next")
            await pilot.pause(delay=0.3)

            radio = screen.query_one("#key-select", RadioSet)
            expected_labels = radio_labels(radio)
            expected_actions_y = current_step_actions(screen).region.y

            for _ in range(5):
                screen.on_discovery_back()
                await pilot.pause(delay=0.1)
                screen.on_username_next()
                await pilot.pause(delay=0.5)

            radio = screen.query_one("#key-select", RadioSet)
            for _ in range(10):
                if radio_labels(radio):
                    break
                await pilot.pause(delay=0.2)

            assert screen.current_stage.value == "step-discovery"
            assert screen.query_one("#username-input", Input).value == "testuser"
            assert radio_labels(radio) == expected_labels
            assert list(screen.query("#step-discovery DataTable")) == []
            assert current_step_actions(screen).region.y == expected_actions_y


async def test_setup_short_circuit_auto_success_uses_loading_and_done_only():
    state = AppState()

    async def fake_run(self) -> tuple[bool, str | None]:
        self.state.config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        return True, "testuser"

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new=fake_run), \
         patch.object(AppState, "save"):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen

            assert screen.current_stage.value == "step-done"
            assert [stage.value for stage in screen.transition_history] == [
                "step-loading",
                "step-done",
            ]
            assert visible_step_ids(screen) == ["step-done"]


async def test_setup_content_switcher_single_visible_pane_per_stage(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen

        for stage in (
            screen.current_stage.__class__.USERNAME,
            screen.current_stage.__class__.DISCOVERY,
            screen.current_stage.__class__.REMOTE,
            screen.current_stage.__class__.UPLOAD,
            screen.current_stage.__class__.DONE,
        ):
            screen.transition_to(stage)
            await pilot.pause()

            assert screen.current_stage is stage
            assert visible_step_ids(screen) == [stage.value]


async def test_setup_status_line_reserved_height_across_steps(no_auto_setup):
    async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen

        username_y = current_step_actions(screen).region.y
        screen.query_one("#username-status", Static).update("Username is required")
        await pilot.pause()
        assert current_step_actions(screen).region.y == username_y

        screen.transition_to(screen.current_stage.__class__.REMOTE)
        await pilot.pause()
        remote_y = current_step_actions(screen).region.y
        screen.query_one("#remote-status", Static).update("Not linked yet. We can set this up next.")
        await pilot.pause()
        assert current_step_actions(screen).region.y == remote_y

        screen.transition_to(screen.current_stage.__class__.UPLOAD)
        await pilot.pause()
        upload_y = current_step_actions(screen).region.y
        screen.query_one("#upload-result", Static).update("Key linked to GitHub. You're all set.")
        await pilot.pause()
        assert current_step_actions(screen).region.y == upload_y


async def test_setup_step_header_everywhere_copy_hierarchy(no_auto_setup):
    async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen

        for step_id in ("step-loading", "step-username", "step-discovery", "step-remote", "step-upload", "step-done"):
            step = screen.query_one(f"#{step_id}", Vertical)

            assert len(list(step.query(StepHeader))) == 1
            assert len(list(step.query(StepBody))) == 1
            assert list(step.query("Label.step-title")) == []


async def test_setup_fingerprint_format_and_email_angle_copy_hierarchy(no_auto_setup):
    gpg_key = GPGKeyInfo(
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        email="John Doe <john.doe@example.org>",
        algo="rsa4096",
    )

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = ""
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)

            assert radio_labels(screen.query_one("#key-select", RadioSet)) == [
                "GPG · F329 9DE3 ... A700 D73A · John Doe <john.doe@example.org>",
            ]


async def test_setup_text_wrap_long_username_copy_hierarchy(no_auto_setup):
    long_username = "a" * 39

    async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen
        actions_y = current_step_actions(screen).region.y
        screen.query_one("#username-status", Static).update(f"Auto-detected: {long_username}")
        await pilot.pause()

        assert "…" not in screenshot_text(pilot.app)
        assert long_username in screenshot_text(pilot.app)
        assert current_step_actions(screen).region.y == actions_y


async def test_setup_text_wrap_one_char_username_keeps_layout(no_auto_setup):
    async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
        await pilot.pause(delay=0.3)
        screen = pilot.app.screen
        actions_y = current_step_actions(screen).region.y
        screen.query_one("#username-input", Input).value = "a"
        await pilot.pause()

        assert current_step_actions(screen).region.y == actions_y
        assert "…" not in screenshot_text(pilot.app)


async def test_setup_resize_preserves_current_step_state(no_auto_setup):
    gpg_key = GPGKeyInfo(
        fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        email="John Doe <john.doe@example.org>",
        algo="rsa4096",
    )

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=(gpg_key,)), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.query_one("#username-input", Input).value = "testuser"

            await pilot.resize_terminal(120, 40)
            await pilot.pause()

            assert screen.query_one("#username-input", Input).value == "testuser"

            screen.username = ""
            screen._switch_to_discovery()
            await pilot.pause(delay=0.3)
            radio = screen.query_one("#key-select", RadioSet)
            button = next(iter(radio.query(RadioButton)))
            await pilot.click(button)
            await pilot.pause()

            await pilot.resize_terminal(80, 24)
            await pilot.pause()

            assert screen.current_stage.value == "step-discovery"
            assert radio.pressed_index == 0


async def test_setup_text_wrap_small_terminal_does_not_crash(no_auto_setup):
    async with SetupHarness(AppState()).run_test(size=(40, 20)) as pilot:
        await pilot.pause(delay=0.3)

        assert pilot.app.screen.current_stage.value == "step-username"
        assert "Traceback" not in screenshot_text(pilot.app)


async def test_setup_text_wrap_wide_dialog_stays_centered(no_auto_setup):
    async with SetupHarness(AppState()).run_test(size=(180, 68)) as pilot:
        await pilot.pause(delay=0.3)
        dialog = pilot.app.screen.query_one("#dialog-box", Vertical)

        assert dialog.region.width <= 90
        assert dialog.region.x > 40
        assert dialog.region.x + dialog.region.width < 140


def test_setup_ansi_escape_sample_payload_is_plain_text():
    assert "\x1b[" not in SetupScreen.render_sample_payload()


class CostHarness(App[None]):
    def __init__(self, bucket_count: int, model: str) -> None:
        super().__init__()
        self.bucket_count = bucket_count
        self.model = model
        self.dismissed: bool | None = None

    def on_mount(self) -> None:
        self.push_screen(CostReviewScreen(self.bucket_count, self.model), self._capture)

    def _capture(self, result: bool | None) -> None:
        self.dismissed = result


async def test_cost_review_renders_bucket_count_and_cost():
    harness = CostHarness(500, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        text = " ".join(
            str(w.render()) for w in pilot.app.screen.query("Label, Static")
        )
        assert "500" in text
        assert "claude-haiku-4-5" in text


async def test_cost_review_continue_dismisses_true():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#cost-yes")
        await pilot.pause()
        assert harness.dismissed is True


async def test_cost_review_cancel_dismisses_false():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#cost-no")
        await pilot.pause()
        assert harness.dismissed is False


async def test_cost_review_escape_dismisses_false():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert harness.dismissed is False


class ErrorHarness(App[None]):
    def __init__(self, status: ClaudeStatus) -> None:
        super().__init__()
        self.status = status
        self.dismissed: object = "not-yet"

    def on_mount(self) -> None:
        self.push_screen(PlatformErrorScreen(self.status), self._capture)

    def _capture(self, result: object) -> None:
        self.dismissed = result


async def test_platform_error_not_installed_shows_brew_install():
    harness = ErrorHarness(ClaudeNotInstalled(brew_available=True))
    async with harness.run_test() as pilot:
        await pilot.pause()
        boxes = pilot.app.screen.query(CommandBox)
        commands = [b.command for b in boxes]
        assert "brew install --cask claude-code" in commands
        assert "claude auth login" in commands


async def test_platform_error_not_installed_without_brew_shows_curl():
    harness = ErrorHarness(ClaudeNotInstalled(brew_available=False))
    async with harness.run_test() as pilot:
        await pilot.pause()
        commands = [b.command for b in pilot.app.screen.query(CommandBox)]
        assert any("install.sh" in c for c in commands)
        assert "claude auth login" in commands


async def test_platform_error_not_authenticated_shows_auth_only():
    harness = ErrorHarness(ClaudeNotAuthenticated())
    async with harness.run_test() as pilot:
        await pilot.pause()
        commands = [b.command for b in pilot.app.screen.query(CommandBox)]
        assert commands == ["claude auth login"]


async def test_platform_error_quit_dismisses():
    harness = ErrorHarness(ClaudeNotAuthenticated())
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#quit-btn")
        await pilot.pause()
        assert harness.dismissed is None


async def test_ccsentiment_app_engine_failure_shows_error_and_exits(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"
    with patch(
        "cc_sentiment.tui.app.EngineFactory.resolve",
        side_effect=ClaudeUnavailable(ClaudeNotInstalled(brew_available=True)),
    ), patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert isinstance(pilot.app.screen, PlatformErrorScreen)


async def test_ccsentiment_app_debug_mode_composes(tmp_path: Path):
    from cc_sentiment.tui.widgets.debug_section import DebugSection

    state = AppState()
    db_path = tmp_path / "records.db"
    with patch(
        "cc_sentiment.tui.app.EngineFactory.resolve",
        side_effect=ClaudeUnavailable(ClaudeNotInstalled(brew_available=True)),
    ), patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path, debug=True)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.1)
            assert pilot.app.query_one(DebugSection) is not None


async def test_ccsentiment_app_pushes_setup_when_no_config(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 1))), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.find_git_username", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, SetupScreen)


async def test_ccsentiment_app_setup_only_pushes_setup_without_worker(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.find_git_username", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path, setup_only=True)
        app.exit = Mock(wraps=app.exit)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, SetupScreen)
            await pilot.press("escape")
            await pilot.pause(delay=0.1)

    app.exit.assert_called_once_with()


async def test_ccsentiment_app_claude_engine_shows_cost_review(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 50))):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            assert pilot.app.screen.bucket_count == 50


async def test_ccsentiment_app_cost_cancel_exits(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    mock_pipeline_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 50))), \
         patch("cc_sentiment.pipeline.Pipeline.run", mock_pipeline_run):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            await pilot.click("#cost-no")
            await pilot.pause()
            mock_pipeline_run.assert_not_called()


async def test_ccsentiment_app_idle_when_no_work(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, (IdleEmpty, IdleCaughtUp))
            assert "all" in app.status_text.lower() or "set" in app.status_text.lower()


async def test_ccsentiment_app_rescan_clears_state(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, IdleAfterUpload)

            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.stage, RescanConfirm)

            await pilot.press("r")
            await pilot.pause()

    verify = Repository.open(db_path)
    try:
        assert verify.stats() == (0, 0, 0)
    finally:
        verify.close()


async def test_ccsentiment_app_runs_pipeline_and_uploads(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    mock_upload = AsyncMock()

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 2))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", mock_upload):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)

            mock_upload.assert_awaited_once()


async def test_authenticate_returns_true_when_creds_valid(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is True


async def test_authenticate_returns_false_on_unreachable(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnreachable(detail="connect refused"),
         ):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is False


async def test_authenticate_returns_false_on_server_error(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthServerError(status=500),
         ):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is False


async def test_authenticate_unauthorized_clears_config_and_pushes_setup(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    async def user_cancels_setup(screen) -> bool:
        return False

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnauthorized(status=401),
         ), \
         patch.object(CCSentimentApp, "push_screen_wait", side_effect=user_cancels_setup) as mock_push:
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            result = await app._authenticate()
            assert result is False
            assert app.state.config is None
            mock_push.assert_awaited()


async def test_auto_open_dashboard_opens_url_after_delay(tmp_path: Path, auth_ok, monkeypatch):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    monkeypatch.setattr(CCSentimentApp, "AUTO_OPEN_DASHBOARD_DELAY_SECONDS", 0.0)

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.tui.app.webbrowser.open") as mock_open:
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app._auto_open_dashboard()
            mock_open.assert_called_once_with(DASHBOARD_URL)


async def test_run_flow_aborts_when_authenticate_returns_false(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    mock_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 1))), \
         patch("cc_sentiment.pipeline.Pipeline.run", mock_run), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnreachable(detail="no net"),
         ):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            mock_run.assert_not_called()


def test_format_duration_under_30_seconds():
    assert TimeFormat.format_duration(0) == "a few seconds"
    assert TimeFormat.format_duration(29) == "a few seconds"


def test_format_duration_minutes():
    assert TimeFormat.format_duration(60) == "~1 min"
    assert TimeFormat.format_duration(900) == "~15 min"


def test_format_duration_hours():
    assert TimeFormat.format_duration(3600) == "~1 hour"
    assert TimeFormat.format_duration(7200) == "~2 hours"


def test_sample_payload_fields_match_real_record_schema():
    from cc_sentiment.models import SentimentRecord

    payload = SetupScreen.render_sample_payload()
    real_fields = set(SentimentRecord.model_fields)
    sample_fields = [
        line.split('"')[1]
        for line in payload.split("\n")
        if line.strip().startswith("[cyan]")
    ]

    for k in sample_fields:
        assert k in real_fields, f"{k!r} is not a real SentimentRecord field"
    for forbidden in ("message", "content", "transcript", "prompt_text", "prompt_body"):
        assert not any(forbidden in f for f in real_fields), (
            f"SentimentRecord has a field matching {forbidden!r} — sample payload may be misleading"
        )


async def test_set_total_renders_eta_when_hardware_estimates(tmp_path: Path, auth_ok):
    from textual.widgets import Label
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
    from textual.widgets import Label
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


async def test_action_open_dashboard_opens_browser(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.tui.app.webbrowser.open") as mock_open:
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.press("o")
            await pilot.pause()
            mock_open.assert_called_once_with(DASHBOARD_URL)
            assert DASHBOARD_URL in app.status_text


async def test_enter_idle_after_upload_mentions_dashboard(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)

            await app._enter_idle(uploaded=True)
            assert isinstance(app.stage, IdleAfterUpload)
            assert "Uploaded" in app.status_text
            assert "sentiments.cc" in app.status_text

            await app._enter_idle(uploaded=False)
            assert isinstance(app.stage, IdleCaughtUp)
            assert "Uploaded" not in app.status_text
            assert "O to open dashboard" in app.status_text


async def test_enter_idle_empty_state_mentions_dashboard(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app._enter_idle(uploaded=False)
            assert isinstance(app.stage, IdleEmpty)
            assert "No conversations yet" in app.status_text
            assert "O to browse" in app.status_text


async def test_successful_upload_lands_in_idle_after_upload(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 2))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)
            assert isinstance(app.stage, IdleAfterUpload)
            assert "sentiments.cc" in app.status_text


async def test_stage_transitions_across_successful_run(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3)]
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    seen: list[type[Stage]] = []

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 1))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        original_watch = app.watch_stage

        def recording_watch(stage: Stage) -> None:
            seen.append(type(stage))
            original_watch(stage)

        app.watch_stage = recording_watch  # type: ignore[method-assign]

        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)

    assert Discovering in seen
    assert Scoring in seen
    assert Uploading in seen
    assert IdleAfterUpload in seen
    assert seen.index(Discovering) < seen.index(Scoring) < seen.index(Uploading) < seen.index(IdleAfterUpload)


async def test_rescan_confirm_restores_previous_stage_on_cancel(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, IdleAfterUpload)
            prev = app.stage

            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.stage, RescanConfirm)
            assert app.stage.prev == prev

            await app._cancel_rescan()
            assert app.stage == prev


def _make_pool(state: AppState, db_path: Path) -> UploadPool:
    return UploadPool(
        uploader=Uploader(),
        state=state,
        repo=Repository.open(db_path),
        progress=UploadProgress(),
        on_progress_change=lambda _: None,
    )


async def test_upload_worker_retries_transient_network_errors(tmp_path: Path):
    import anyio

    state = AppState(config=SSHConfig(contributor_id=ContributorId("u"), key_path=Path("/k")))
    pool = _make_pool(state, tmp_path / "records.db")

    send, recv = anyio.create_memory_object_stream[list](float("inf"))
    send.send_nowait([make_record()])
    send.close()

    calls = 0

    async def fake_upload(self, batch, state, repo, on_progress=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("boom")

    with patch("cc_sentiment.upload.Uploader.upload", fake_upload), \
         patch("cc_sentiment.upload.anyio.sleep", new_callable=AsyncMock):
        await pool._worker_loop(recv, worker_id=0)

    assert calls == 2
    assert pool.progress.uploaded_records == 1
    assert pool.progress.failed_batches == 0
    assert pool.progress.fatal is None


async def test_upload_worker_records_partial_failure_after_retries_exhaust(tmp_path: Path):
    import anyio

    state = AppState(config=SSHConfig(contributor_id=ContributorId("u"), key_path=Path("/k")))
    pool = _make_pool(state, tmp_path / "records.db")

    send, recv = anyio.create_memory_object_stream[list](float("inf"))
    send.send_nowait([make_record(session_id="s1")])
    send.send_nowait([make_record(session_id="s2")])
    send.close()

    async def always_fail(self, batch, state, repo, on_progress=None):
        raise httpx.ConnectError("down")

    with patch("cc_sentiment.upload.Uploader.upload", always_fail), \
         patch("cc_sentiment.upload.anyio.sleep", new_callable=AsyncMock):
        await pool._worker_loop(recv, worker_id=0)

    assert pool.progress.failed_batches == 2
    assert pool.progress.uploaded_records == 0
    assert pool.progress.fatal is None


async def test_upload_worker_fatal_on_401_drops_subsequent_batches(tmp_path: Path):
    import anyio

    state = AppState(config=SSHConfig(contributor_id=ContributorId("u"), key_path=Path("/k")))
    pool = _make_pool(state, tmp_path / "records.db")

    send, recv = anyio.create_memory_object_stream[list](float("inf"))
    send.send_nowait([make_record(session_id="s1")])
    send.send_nowait([make_record(session_id="s2")])
    send.close()

    calls = 0

    async def reject_first(self, batch, state, repo, on_progress=None):
        nonlocal calls
        calls += 1
        raise httpx.HTTPStatusError(
            "nope",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(401),
        )

    with patch("cc_sentiment.upload.Uploader.upload", reject_first):
        await pool._worker_loop(recv, worker_id=0)

    assert calls == 1
    assert isinstance(pool.progress.fatal, httpx.HTTPStatusError)
    assert pool.progress.fatal.response.status_code == 401
    assert pool.progress.uploaded_records == 0
    assert pool.progress.failed_batches == 0


class ChartHarness(App[None]):
    def compose(self):
        yield HourlyChart(id="chart")


async def test_hourly_chart_renders_dot_and_line_grid():
    from datetime import datetime, timezone

    records = [
        make_record(score=5, time=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)),
        make_record(score=1, time=datetime(2026, 4, 10, 14, 0, tzinfo=timezone.utc)),
        make_record(score=3, time=datetime(2026, 4, 10, 20, 0, tzinfo=timezone.utc)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        text = str(chart.content)
        lines = text.split("\n")
        assert len(lines) == 7
        for tick in HourlyChart.Y_TICKS.values():
            assert any(line.startswith(tick) for line in lines[:5])
        assert "─" * 24 in lines[5]
        assert "12a" in lines[6]
        assert "6a" in lines[6]
        assert "12p" in lines[6]
        assert "[red]●[/]" in lines[4]
        assert "[cyan]●[/]" in lines[0]


async def test_hourly_chart_scales_frustration_relative_to_max():
    from datetime import datetime, timezone

    records = [
        make_record(score=1, time=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)),
        make_record(score=2, time=datetime(2026, 4, 10, 8, 1, tzinfo=timezone.utc)),
        make_record(score=1, time=datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)),
        make_record(score=4, time=datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        lines = str(chart.content).split("\n")
        assert "[red]●[/]" in lines[4]
        assert "[yellow]●[/]" in lines[2]
        assert "[cyan]●[/]" in lines[0]


async def test_hourly_chart_empty_records():
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart([])
        await pilot.pause()
        assert "no data yet" in str(chart.content)


class MomentsHarness(App[None]):
    def compose(self):
        with Vertical(id="section"):
            yield Static("", id="log")


async def test_moments_view_snippet_survives_bracket_heavy_content():
    async with MomentsHarness().run_test() as pilot:
        moments = MomentsView(
            app=pilot.app,
            section=pilot.app.query_one("#section"),
            log=pilot.app.query_one("#log", Static),
        )
        moments.show()
        await moments.add_snippet(
            "2026-04-03T11:14:13.287367+0000 +13m26s [🐞][DSPyCompilationServer.compile] 'ignore'",
            1,
        )
        moments.last_snippet_at = 0.0
        await moments.add_snippet("prefix text [dim", 1)
        moments.last_snippet_at = 0.0
        await moments.add_snippet("<task-notification> <task-id>abc</task-id> body", 5)
        await pilot.pause()
        assert len(moments.lines) >= 1


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
    from cc_sentiment.models import ShareMintResponse
    return AsyncMock(return_value=ShareMintResponse(
        id=share_id,
        url=f"https://sentiments.cc/share/{share_id}",
    ))


async def test_stat_share_renders_stat_text():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share()):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            text = " ".join(
                str(w.render()) for w in pilot.app.screen.query("Label, Static")
            )
            assert "nicer to Claude than 72% of developers" in text


async def test_stat_share_tweet_button_opens_share_url():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share("sh-xyz789")), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.click("#stat-tweet")
            await pilot.pause()

    mock_open.assert_called_once()
    url = mock_open.call_args[0][0]
    assert "twitter.com/intent/tweet" in url
    assert "share%2Fsh-xyz789" in url or "share/sh-xyz789" in url
    assert "nicer+to+Claude" in url or "nicer%20to%20Claude" in url


async def test_stat_share_tweet_button_disabled_until_mint_resolves():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    mint_event = __import__("anyio").Event()

    async def slow_mint(self, config):
        await mint_event.wait()
        from cc_sentiment.models import ShareMintResponse
        return ShareMintResponse(id="sh-late", url="https://sentiments.cc/share/sh-late")

    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=slow_mint), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.1)
            tweet = pilot.app.screen.query_one("#stat-tweet", Button)
            assert tweet.disabled is True
            await pilot.click("#stat-tweet")
            await pilot.pause()
            assert not mock_open.called

            mint_event.set()
            await pilot.pause(delay=0.3)
            assert tweet.disabled is False


async def test_stat_share_skip_dismisses_without_opening_browser():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share()), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.click("#stat-skip")
            await pilot.pause()

    mock_open.assert_not_called()


async def test_stat_share_escape_dismisses():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share()), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.press("escape")
            await pilot.pause()

    mock_open.assert_not_called()


async def test_cta_shows_schedule_when_daemon_not_installed(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app.view.cta.schedule_available is True
            assert app.view.cta.showing == "schedule"
            section = pilot.app.query_one("#cta-section")
            assert "inactive" not in section.classes
            button = pilot.app.query_one("#cta-action", Button)
            assert str(button.label) == "Schedule it"


async def test_cta_hides_when_daemon_installed_and_no_tweet(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=True), \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app.view.cta.schedule_available is False
            assert app.view.cta.has_tweet() is False
            section = pilot.app.query_one("#cta-section")
            assert "inactive" in section.classes


async def test_cta_rotates_between_tweet_and_schedule(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            app.view.set_tweet(GITHUB_CONFIG, STAT)
            await pilot.pause()
            assert app.view.cta.showing == "schedule"
            button = pilot.app.query_one("#cta-action", Button)
            assert str(button.label) == "Schedule it"

            app.view.rotate_cta()
            await pilot.pause()
            assert app.view.cta.showing == "tweet"
            assert str(button.label) == "Tweet it"
            title = pilot.app.query_one("#cta-title", Static)
            assert "nicer to Claude" in str(title.render())

            app.view.rotate_cta()
            await pilot.pause()
            assert app.view.cta.showing == "schedule"


async def test_cta_pins_to_tweet_after_install_succeeds(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.app.LaunchAgent.install") as mock_install, \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            app.view.set_tweet(GITHUB_CONFIG, STAT)
            await pilot.pause()
            assert app.view.cta.showing == "schedule"

            await pilot.click("#cta-action")
            await pilot.pause(delay=0.2)

            mock_install.assert_called_once()
            assert app.view.cta.schedule_available is False
            assert app.view.cta.showing == "tweet"


async def test_card_poller_invokes_on_ready_when_stat_arrives():
    from cc_sentiment.tui.screens.stat_share import CardPoller

    calls: list[MyStat] = []
    states: list[tuple[int, str, float, str | None]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        return_value=STAT,
    ):
        poller = CardPoller(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda a, s, e, stop: states.append((a, s, e, stop)),
        )
        await poller.run()

    assert calls == [STAT]
    assert any(state[3] == "ready" for state in states)


async def test_card_poller_gives_up_when_max_duration_exceeded():
    from cc_sentiment.tui.screens.stat_share import CardPoller

    calls: list[MyStat] = []
    states: list[tuple[int, str, float, str | None]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("no net"),
    ), patch.object(CardPoller, "MAX_POLL_SECONDS", 0.0):
        poller = CardPoller(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda a, s, e, stop: states.append((a, s, e, stop)),
        )
        await poller.run()

    assert calls == []
    assert any(state[3] == "timeout" for state in states)

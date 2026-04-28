from __future__ import annotations

from typing import TypeVar

from textual.app import App, ComposeResult
from textual.widgets import Button, Input, Static

from cc_sentiment.onboarding.state import ExistingKey, KeySource
from cc_sentiment.tui.onboarding.widgets import (
    FallbackPanel,
    GpgKeyCard,
    InlineUsernameRow,
    KeyCard,
    KeyPreview,
    ManagedKeyCard,
    PublishActions,
    SshKeyCard,
    WatcherRow,
)


W = TypeVar("W")


class Harness(App[None]):
    def __init__(self, factory) -> None:
        super().__init__()
        self.factory = factory
        self.captured: list[object] = []

    def compose(self) -> ComposeResult:
        yield self.factory()

    def on_key_card_selected(self, event: KeyCard.Selected) -> None:
        self.captured.append(event)

    def on_publish_actions_opened(self, event: PublishActions.Opened) -> None:
        self.captured.append(event)

    def on_publish_actions_copy_again(self, event: PublishActions.CopyAgain) -> None:
        self.captured.append(event)

    def on_publish_actions_no_github(self, event: PublishActions.NoGithub) -> None:
        self.captured.append(event)

    def on_fallback_panel_confirmed(self, event: FallbackPanel.Confirmed) -> None:
        self.captured.append(event)

    def on_inline_username_row_submitted(self, event: InlineUsernameRow.Submitted) -> None:
        self.captured.append(event)


SSH_KEY = ExistingKey(fingerprint="SHA256:abcd1234", label="id_ed25519")
GPG_KEY = ExistingKey(fingerprint="DEADBEEFCAFE0001", label="alice@example.com")


# ─── KeyCard family ────────────────────────────────────────────────────────


def text_of(widget) -> str:
    return str(widget.render())


class TestSshKeyCard:
    async def test_renders_with_id_and_label(self):
        async with Harness(lambda: SshKeyCard(SSH_KEY, index=0)).run_test() as pilot:
            card = pilot.app.query_one("#ssh-card-0", SshKeyCard)
            labels = [text_of(s) for s in card.query(".key-card-label")]
            assert any("id_ed25519" in label for label in labels)

    async def test_no_preview_when_unfocused(self):
        # Render two cards, focus the first; second must have no preview.
        class TwoCards(App[None]):
            def compose(self):
                yield SshKeyCard(SSH_KEY, index=0, focused=True)
                yield SshKeyCard(SSH_KEY, index=1, focused=False)

        async with TwoCards().run_test() as pilot:
            await pilot.pause()
            second = pilot.app.query_one("#ssh-card-1", SshKeyCard)
            assert not second.has_focus
            assert not second.query(".key-preview")

    async def test_preview_shows_when_initially_focused(self):
        async with Harness(lambda: SshKeyCard(SSH_KEY, index=0, focused=True)).run_test() as pilot:
            await pilot.pause()
            card = pilot.app.query_one("#ssh-card-0", SshKeyCard)
            assert card.has_focus
            assert card.query(".key-preview")

    async def test_click_posts_selected(self):
        async with Harness(lambda: SshKeyCard(SSH_KEY, index=0)).run_test() as pilot:
            await pilot.click("#ssh-card-0")
            await pilot.pause()
            assert pilot.app.captured
            event = pilot.app.captured[0]
            assert isinstance(event, KeyCard.Selected)
            assert event.source is KeySource.EXISTING_SSH
            assert event.key == SSH_KEY


class TestGpgKeyCard:
    async def test_renders_with_id_and_email_subline(self):
        async with Harness(lambda: GpgKeyCard(GPG_KEY, index=0)).run_test() as pilot:
            card = pilot.app.query_one("#gpg-card-0", GpgKeyCard)
            subs = [text_of(s) for s in card.query(".key-card-subline")]
            assert any("alice@example.com" in s for s in subs)

    async def test_label_uses_short_fingerprint(self):
        async with Harness(lambda: GpgKeyCard(GPG_KEY, index=0)).run_test() as pilot:
            card = pilot.app.query_one("#gpg-card-0", GpgKeyCard)
            label = text_of(card.query_one(".key-card-label"))
            assert "CAFE0001" in label

    async def test_click_posts_selected_with_gpg_source(self):
        async with Harness(lambda: GpgKeyCard(GPG_KEY, index=0)).run_test() as pilot:
            await pilot.click("#gpg-card-0")
            await pilot.pause()
            event = pilot.app.captured[0]
            assert isinstance(event, KeyCard.Selected)
            assert event.source is KeySource.EXISTING_GPG


class TestManagedKeyCard:
    async def test_renders_with_managed_id(self):
        async with Harness(lambda: ManagedKeyCard()).run_test() as pilot:
            assert pilot.app.query_one("#managed-card", ManagedKeyCard)

    async def test_default_label_and_subline(self):
        async with Harness(lambda: ManagedKeyCard()).run_test() as pilot:
            card = pilot.app.query_one("#managed-card", ManagedKeyCard)
            text = " ".join(text_of(s) for s in card.query(Static))
            assert "Create a new signature" in text
            assert "~/.cc-sentiment/keys" in text

    async def test_recommended_pill_present_when_recommended(self):
        async with Harness(lambda: ManagedKeyCard(recommended=True)).run_test() as pilot:
            pill = pilot.app.query_one("#recommended-pill", Static)
            assert "recommended" in text_of(pill)

    async def test_recommended_pill_absent_when_not_recommended(self):
        async with Harness(lambda: ManagedKeyCard(recommended=False)).run_test() as pilot:
            assert not pilot.app.query("#recommended-pill")

    async def test_click_posts_selected_with_managed_source_and_no_key(self):
        async with Harness(lambda: ManagedKeyCard()).run_test() as pilot:
            await pilot.click("#managed-card")
            await pilot.pause()
            event = pilot.app.captured[0]
            assert isinstance(event, KeyCard.Selected)
            assert event.source is KeySource.MANAGED
            assert event.key is None


# ─── KeyPreview ────────────────────────────────────────────────────────────


class TestKeyPreview:
    async def test_renders_with_default_id(self):
        async with Harness(lambda: KeyPreview("ssh-ed25519 AAAA…")).run_test() as pilot:
            assert pilot.app.query_one("#key-preview", KeyPreview)

    async def test_includes_key_text(self):
        async with Harness(lambda: KeyPreview("ssh-ed25519 AAAA…")).run_test() as pilot:
            text = pilot.app.query_one("#key-preview-text", Static)
            assert "ssh-ed25519" in text_of(text)

    async def test_default_title(self):
        async with Harness(lambda: KeyPreview("ssh-ed25519 AAAA…")).run_test() as pilot:
            preview = pilot.app.query_one("#key-preview", KeyPreview)
            assert preview.border_title == "Your signature"


# ─── PublishActions ────────────────────────────────────────────────────────


class TestPublishActions:
    async def test_renders_open_and_copy(self):
        async with Harness(lambda: PublishActions(open_url="https://x.test/")).run_test() as pilot:
            assert pilot.app.query_one("#open-btn", Button).label.plain == "Open GitHub"
            assert pilot.app.query_one("#copy-again-link")

    async def test_open_button_focused_on_mount(self):
        async with Harness(lambda: PublishActions(open_url="https://x.test/")).run_test() as pilot:
            await pilot.pause()
            assert pilot.app.query_one("#open-btn", Button).has_focus

    async def test_no_github_hidden_by_default(self):
        async with Harness(lambda: PublishActions(open_url="https://x.test/")).run_test() as pilot:
            assert not pilot.app.query("#no-github-link")

    async def test_no_github_shown_when_enabled(self):
        async with Harness(
            lambda: PublishActions(open_url="https://x.test/", show_no_github=True)
        ).run_test() as pilot:
            assert pilot.app.query_one("#no-github-link")

    async def test_click_open_posts_opened_message(self):
        url = "https://gist.github.com/new"
        async with Harness(lambda: PublishActions(open_url=url)).run_test() as pilot:
            await pilot.click("#open-btn")
            await pilot.pause()
            opened = [e for e in pilot.app.captured if isinstance(e, PublishActions.Opened)]
            assert opened and opened[0].url == url

    async def test_click_copy_again_posts_message(self):
        async with Harness(lambda: PublishActions(open_url="https://x.test/")).run_test() as pilot:
            await pilot.click("#copy-again-link")
            await pilot.pause()
            assert any(isinstance(e, PublishActions.CopyAgain) for e in pilot.app.captured)

    async def test_click_no_github_posts_message(self):
        async with Harness(
            lambda: PublishActions(open_url="https://x.test/", show_no_github=True)
        ).run_test() as pilot:
            await pilot.click("#no-github-link")
            await pilot.pause()
            assert any(isinstance(e, PublishActions.NoGithub) for e in pilot.app.captured)


# ─── WatcherRow ────────────────────────────────────────────────────────────


class TestWatcherRow:
    async def test_renders_with_default_id(self):
        async with Harness(lambda: WatcherRow("Watching for your gist…")).run_test() as pilot:
            assert pilot.app.query_one("#watcher-row", WatcherRow)

    async def test_rate_limit_note_hidden_by_default(self):
        async with Harness(lambda: WatcherRow("Watching…")).run_test() as pilot:
            note = pilot.app.query_one("#rate-limit-note")
            assert not note.display

    async def test_rate_limit_note_shows_when_toggled(self):
        async with Harness(lambda: WatcherRow("Watching…")).run_test() as pilot:
            row = pilot.app.query_one("#watcher-row", WatcherRow)
            row.rate_limited = True
            await pilot.pause()
            assert pilot.app.query_one("#rate-limit-note").display

    async def test_text_reactive_updates_label(self):
        async with Harness(lambda: WatcherRow("Watching…")).run_test() as pilot:
            row = pilot.app.query_one("#watcher-row", WatcherRow)
            row.text = "Still waiting…"
            await pilot.pause()
            assert "Still waiting" in str(row._spinner.label)

    async def test_custom_id_for_inbox_polling(self):
        async with Harness(
            lambda: WatcherRow("Waiting for verification…", id="polling-status")
        ).run_test() as pilot:
            assert pilot.app.query_one("#polling-status", WatcherRow)


# ─── FallbackPanel ─────────────────────────────────────────────────────────


class TestFallbackPanel:
    def make(self) -> FallbackPanel:
        return FallbackPanel(
            key_text="ssh-ed25519 AAAA…",
            target_url="https://gist.github.com/new",
        )

    async def test_hidden_by_default(self):
        async with Harness(self.make).run_test() as pilot:
            panel = pilot.app.query_one("#fallback-panel", FallbackPanel)
            assert not panel.display

    async def test_shows_when_visible_set(self):
        async with Harness(self.make).run_test() as pilot:
            panel = pilot.app.query_one("#fallback-panel", FallbackPanel)
            panel.visible = True
            await pilot.pause()
            assert panel.display

    async def test_renders_key_text_and_url(self):
        async with Harness(self.make).run_test() as pilot:
            panel = pilot.app.query_one("#fallback-panel", FallbackPanel)
            panel.visible = True
            await pilot.pause()
            key = pilot.app.query_one("#fallback-key-text", Static)
            url = pilot.app.query_one("#fallback-url", Static)
            assert "ssh-ed25519" in text_of(key)
            assert "gist.github.com/new" in text_of(url)

    async def test_confirm_button_posts_message(self):
        async with Harness(self.make).run_test() as pilot:
            panel = pilot.app.query_one("#fallback-panel", FallbackPanel)
            panel.visible = True
            await pilot.pause()
            await pilot.click("#fallback-confirm-btn")
            await pilot.pause()
            assert any(isinstance(e, FallbackPanel.Confirmed) for e in pilot.app.captured)


# ─── InlineUsernameRow ─────────────────────────────────────────────────────


class TestInlineUsernameRow:
    async def test_renders_with_default_id_and_input(self):
        async with Harness(lambda: InlineUsernameRow()).run_test() as pilot:
            assert pilot.app.query_one("#username-row", InlineUsernameRow)
            assert pilot.app.query_one("#username-input", Input)

    async def test_input_has_default_placeholder(self):
        async with Harness(lambda: InlineUsernameRow()).run_test() as pilot:
            assert pilot.app.query_one("#username-input", Input).placeholder == "yasyf"

    async def test_input_prefilled_from_current(self):
        async with Harness(lambda: InlineUsernameRow(current="alice")).run_test() as pilot:
            assert pilot.app.query_one("#username-input", Input).value == "alice"

    async def test_visible_false_hides_row(self):
        async with Harness(lambda: InlineUsernameRow()).run_test() as pilot:
            row = pilot.app.query_one("#username-row", InlineUsernameRow)
            row.visible = False
            await pilot.pause()
            assert not row.display

    async def test_no_submit_button_by_default(self):
        async with Harness(lambda: InlineUsernameRow()).run_test() as pilot:
            assert not pilot.app.query("#username-submit")

    async def test_submit_button_renders_when_label_set(self):
        async with Harness(
            lambda: InlineUsernameRow(submit_label="Try this username")
        ).run_test() as pilot:
            btn = pilot.app.query_one("#username-submit", Button)
            assert btn.label.plain == "Try this username"

    async def test_submit_button_click_posts_message_with_value(self):
        async with Harness(
            lambda: InlineUsernameRow(current="alice", submit_label="Try this username")
        ).run_test() as pilot:
            await pilot.click("#username-submit")
            await pilot.pause()
            event = next(
                (e for e in pilot.app.captured if isinstance(e, InlineUsernameRow.Submitted)),
                None,
            )
            assert event is not None
            assert event.value == "alice"

    async def test_input_enter_posts_submitted(self):
        async with Harness(lambda: InlineUsernameRow()).run_test() as pilot:
            inp = pilot.app.query_one("#username-input", Input)
            inp.value = "bob"
            inp.focus()
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            event = next(
                (e for e in pilot.app.captured if isinstance(e, InlineUsernameRow.Submitted)),
                None,
            )
            assert event is not None
            assert event.value == "bob"

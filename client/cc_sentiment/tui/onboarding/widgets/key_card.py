from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from cc_sentiment.onboarding.state import ExistingKey, KeySource
from cc_sentiment.tui.widgets.section import Section


class KeyCard(Section):
    DEFAULT_CSS: ClassVar[str] = """
    KeyCard {
        height: auto;
        border: round $panel-lighten-2;
        padding: 1 2;
        margin: 0 0 1 0;
        background: $panel;
    }
    KeyCard:focus {
        border: round $accent;
        background: $boost;
    }
    KeyCard > .key-card-label {
        width: 100%;
        text-style: bold;
        color: $text;
    }
    KeyCard > .key-card-subline {
        width: 100%;
        color: $text-muted;
    }
    KeyCard > .key-preview {
        width: 100%;
        color: $text-muted;
        text-style: italic;
        margin: 1 0 0 0;
    }
    KeyCard > .recommended-pill {
        width: auto;
        color: $accent;
        text-style: italic;
    }
    """

    can_focus: ClassVar[bool] = True

    is_active: reactive[bool] = reactive(False, recompose=True)

    @dataclass
    class Selected(Message):
        source: KeySource
        key: ExistingKey | None = None

    def __init__(self, *, focused: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._initial_focus = focused
        self.set_reactive(KeyCard.is_active, focused)

    def on_mount(self) -> None:
        if self._initial_focus:
            self.focus()

    def on_focus(self) -> None:
        self.is_active = True

    def on_blur(self) -> None:
        self.is_active = False

    def on_key(self, event: events.Key) -> None:
        if event.key in ("enter", "space"):
            event.stop()
            self.post_message(self.make_selected())

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.focus()
        self.post_message(self.make_selected())

    def make_selected(self) -> Selected:
        raise NotImplementedError

    @staticmethod
    def glyph(active: bool) -> str:
        return "●" if active else "○"


class SshKeyCard(KeyCard):
    def __init__(self, key: ExistingKey, *, index: int, focused: bool = False) -> None:
        super().__init__(focused=focused, id=f"ssh-card-{index}", classes="key-card")
        self.key = key

    def compose(self) -> ComposeResult:
        yield Static(f"{self.glyph(self.is_active)}  {self.key.label}", classes="key-card-label")
        yield Static(self.key.fingerprint, classes="key-card-subline")
        if self.is_active:
            yield Static(self.key.fingerprint, classes="key-preview")

    def make_selected(self) -> KeyCard.Selected:
        return KeyCard.Selected(source=KeySource.EXISTING_SSH, key=self.key)


class GpgKeyCard(KeyCard):
    def __init__(self, key: ExistingKey, *, index: int, focused: bool = False) -> None:
        super().__init__(focused=focused, id=f"gpg-card-{index}", classes="key-card")
        self.key = key

    def compose(self) -> ComposeResult:
        yield Static(
            f"{self.glyph(self.is_active)}  GPG key {self.key.fingerprint[-8:]}",
            classes="key-card-label",
        )
        yield Static(self.key.label, classes="key-card-subline")
        if self.is_active:
            yield Static(self.key.fingerprint, classes="key-preview")

    def make_selected(self) -> KeyCard.Selected:
        return KeyCard.Selected(source=KeySource.EXISTING_GPG, key=self.key)


class ManagedKeyCard(KeyCard):
    def __init__(
        self,
        *,
        recommended: bool = False,
        focused: bool = False,
        label: str = "Create a new signature for cc-sentiment",
        subline: str = "Dedicated to cc-sentiment, stored under ~/.cc-sentiment/keys.",
        recommended_label: str = "recommended",
    ) -> None:
        super().__init__(focused=focused, id="managed-card", classes="key-card")
        self.recommended = recommended
        self.label_text = label
        self.subline_text = subline
        self.recommended_label = recommended_label

    def compose(self) -> ComposeResult:
        yield Static(
            f"{self.glyph(self.is_active)}  {self.label_text}",
            classes="key-card-label",
        )
        yield Static(self.subline_text, classes="key-card-subline")
        if self.recommended:
            yield Static(self.recommended_label, id="recommended-pill", classes="recommended-pill")
        if self.is_active:
            yield Static("cc-sentiment managed", classes="key-preview")

    def make_selected(self) -> KeyCard.Selected:
        return KeyCard.Selected(source=KeySource.MANAGED, key=None)

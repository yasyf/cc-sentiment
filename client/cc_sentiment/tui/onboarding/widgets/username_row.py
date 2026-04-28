from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Input, Static

from cc_sentiment.tui.widgets.section import Section


class InlineUsernameRow(Section):
    DEFAULT_CSS: ClassVar[str] = """
    InlineUsernameRow { height: auto; margin: 1 0; }
    InlineUsernameRow > Static.username-label {
        width: 100%;
        color: $text-muted;
        margin: 0 0 0 0;
    }
    InlineUsernameRow > Input#username-input {
        width: 100%;
        margin: 0 0 1 0;
    }
    InlineUsernameRow > Button#username-submit { width: auto; }
    """

    visible: reactive[bool] = reactive(True)

    @dataclass
    class Submitted(Message):
        value: str

    def __init__(
        self,
        *,
        current: str = "",
        label: str = "GitHub username",
        placeholder: str = "yasyf",
        submit_label: str | None = None,
        id: str = "username-row",
    ) -> None:
        super().__init__(id=id)
        self.current = current
        self.label_text = label
        self.placeholder = placeholder
        self.submit_label = submit_label

    def compose(self) -> ComposeResult:
        yield Static(self.label_text, classes="username-label")
        yield Input(value=self.current, placeholder=self.placeholder, id="username-input")
        if self.submit_label is not None:
            yield Button(self.submit_label, id="username-submit", variant="primary")

    def watch_visible(self, value: bool) -> None:
        self.display = value

    @on(Input.Submitted, "#username-input")
    def handle_input_submit(self, event: Input.Submitted) -> None:
        self.post_message(self.Submitted(value=event.value.strip()))

    @on(Button.Pressed, "#username-submit")
    def handle_button_submit(self) -> None:
        value = self.query_one("#username-input", Input).value.strip()
        self.post_message(self.Submitted(value=value))

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.message import Message
from textual.widgets import Button

from cc_sentiment.tui.widgets.link_row import LinkRow
from cc_sentiment.tui.widgets.section import Section


class PublishActions(Section):
    DEFAULT_CSS: ClassVar[str] = """
    PublishActions { height: auto; }
    PublishActions > Button#open-btn {
        width: auto;
        margin: 1 0 0 0;
    }
    PublishActions > LinkRow { margin: 1 0 0 0; }
    """

    @dataclass
    class Opened(Message):
        url: str

    @dataclass
    class CopyAgain(Message):
        pass

    @dataclass
    class NoGithub(Message):
        pass

    def __init__(
        self,
        *,
        open_url: str,
        show_no_github: bool = False,
        open_label: str = "Open GitHub",
        copy_label: str = "Copy again",
        no_github_label: str = "I don't use GitHub →",
    ) -> None:
        super().__init__()
        self.open_url = open_url
        self.show_no_github = show_no_github
        self.open_label = open_label
        self.copy_label = copy_label
        self.no_github_label = no_github_label

    def compose(self) -> ComposeResult:
        btn = Button(self.open_label, id="open-btn", variant="primary")
        btn.url = self.open_url
        yield btn
        yield LinkRow(self.copy_label, id="copy-again-link")
        if self.show_no_github:
            yield LinkRow(self.no_github_label, id="no-github-link")

    def on_mount(self) -> None:
        self.query_one("#open-btn", Button).focus()

    @on(Button.Pressed, "#open-btn")
    def handle_open(self) -> None:
        self.post_message(self.Opened(url=self.open_url))

    @on(LinkRow.Pressed, "#copy-again-link")
    def handle_copy_again(self) -> None:
        self.post_message(self.CopyAgain())

    @on(LinkRow.Pressed, "#no-github-link")
    def handle_no_github(self) -> None:
        self.post_message(self.NoGithub())

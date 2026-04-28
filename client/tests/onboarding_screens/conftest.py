from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import Mock

from textual.app import App
from textual.pilot import Pilot

from cc_sentiment.onboarding import Capabilities, State as GlobalState
from cc_sentiment.onboarding.ui import Screen


CAPABILITY_DEFAULTS: dict[str, bool] = {
    "has_ssh_keygen": False,
    "has_gpg": False,
    "has_gh": False,
    "gh_authenticated": False,
    "has_brew": False,
}


def fake_caps(**overrides: bool) -> Capabilities:
    mock = Mock(spec=Capabilities)
    mock.configure_mock(**(CAPABILITY_DEFAULTS | overrides))
    return mock


class Harness(App[None]):
    def __init__(
        self,
        screen_cls: type[Screen],
        gs: GlobalState,
        caps: Capabilities,
    ) -> None:
        super().__init__()
        self._screen_cls = screen_cls
        self._gs = gs
        self._caps = caps

    def on_mount(self) -> None:
        rendered = self._screen_cls().render(self._gs, self._caps)
        if rendered is None:
            raise NotImplementedError(
                f"{self._screen_cls.__name__}.render() returned None — implementation pending"
            )
        self.push_screen(rendered)


@asynccontextmanager
async def mounted(
    screen_cls: type[Screen],
    gs: GlobalState | None = None,
    caps: Capabilities | None = None,
) -> AsyncIterator[Pilot[None]]:
    app = Harness(screen_cls, gs or GlobalState(), caps or fake_caps())
    async with app.run_test() as pilot:
        yield pilot


def texts_in(pilot: Pilot[None]) -> list[str]:
    """Flat list of every Static-like widget's rendered text. Used for forbidden-text assertions."""
    out: list[str] = []
    for widget in pilot.app.screen.walk_children():
        renderable = getattr(widget, "renderable", None)
        if renderable is not None:
            out.append(str(renderable))
        label = getattr(widget, "label", None)
        if label is not None:
            out.append(str(label))
    return out


def has_text(pilot: Pilot[None], needle: str) -> bool:
    return any(needle in text for text in texts_in(pilot))

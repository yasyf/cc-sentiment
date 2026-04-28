from __future__ import annotations

from dataclasses import dataclass

import pytest
from textual import screen as t

from cc_sentiment.onboarding import Stage
from cc_sentiment.onboarding.ui import Screen


def test_screen_is_abstract():
    with pytest.raises(TypeError):
        Screen()  # type: ignore[abstract]


def test_concrete_subclass_satisfies_contract():
    @dataclass
    class WelcomeState:
        busy: bool = False

    class WelcomeScreen(Screen[WelcomeState]):
        State = WelcomeState

        @classmethod
        def matcher(cls) -> Stage:
            return Stage.WELCOME

        def render(self) -> t.Screen:
            return t.Screen()

    assert WelcomeScreen.matcher() is Stage.WELCOME
    assert WelcomeScreen.State is WelcomeState
    assert isinstance(WelcomeScreen().render(), t.Screen)


def test_matcher_classmethod_dispatches_a_stage():
    class WelcomeScreen(Screen[object]):
        State = object

        @classmethod
        def matcher(cls) -> Stage:
            return Stage.WELCOME

        def render(self) -> t.Screen:
            return t.Screen()

    class TroubleScreen(Screen[object]):
        State = object

        @classmethod
        def matcher(cls) -> Stage:
            return Stage.TROUBLE

        def render(self) -> t.Screen:
            return t.Screen()

    registry: tuple[type[Screen], ...] = (WelcomeScreen, TroubleScreen)

    def dispatch(stage: Stage) -> type[Screen] | None:
        return next((s for s in registry if s.matcher() == stage), None)

    assert dispatch(Stage.WELCOME) is WelcomeScreen
    assert dispatch(Stage.TROUBLE) is TroubleScreen
    assert dispatch(Stage.DONE) is None

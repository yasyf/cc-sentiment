from __future__ import annotations

from dataclasses import dataclass

import pytest
from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class WelcomeState(BaseState):
    busy: bool = False


class WelcomeScreen(Screen[WelcomeState]):
    State = WelcomeState

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.WELCOME)

    def render(self) -> t.Screen:
        return t.Screen()


def test_screen_is_abstract():
    with pytest.raises(TypeError):
        Screen()  # type: ignore[abstract]


def test_default_empty_uses_constructor():
    @dataclass(frozen=True)
    class DefaultState(BaseState):
        flag: bool = True

    assert DefaultState.empty() == DefaultState(flag=True)


def test_subclass_can_override_empty():
    @dataclass(frozen=True)
    class SeededState(BaseState):
        counter: int = 0

        @classmethod
        def empty(cls) -> SeededState:
            return cls(counter=42)

    assert SeededState.empty().counter == 42


def test_screen_seeds_state_via_empty():
    instance = WelcomeScreen()
    assert isinstance(instance.state, WelcomeState)
    assert instance.state.busy is False


def test_screen_uses_subclass_override_of_empty():
    @dataclass(frozen=True)
    class SeededState(BaseState):
        counter: int = 0

        @classmethod
        def empty(cls) -> SeededState:
            return cls(counter=42)

    class SeededScreen(Screen[SeededState]):
        State = SeededState

        @classmethod
        def matcher(cls) -> GlobalState:
            return GlobalState(stage=Stage.WELCOME)

        def render(self) -> t.Screen:
            return t.Screen()

    assert SeededScreen().state.counter == 42


def test_matcher_returns_global_state_shape():
    pattern = WelcomeScreen.matcher()
    assert isinstance(pattern, GlobalState)
    assert pattern.stage is Stage.WELCOME


def test_matcher_can_describe_a_substate():
    from cc_sentiment.onboarding import TroubleReason

    class GistTroubleScreen(Screen[WelcomeState]):
        State = WelcomeState

        @classmethod
        def matcher(cls) -> GlobalState:
            return GlobalState(
                stage=Stage.TROUBLE,
                trouble_reason=TroubleReason.GIST_TIMEOUT,
            )

        def render(self) -> t.Screen:
            return t.Screen()

    pattern = GistTroubleScreen.matcher()
    assert pattern.stage is Stage.TROUBLE
    assert pattern.trouble_reason is TroubleReason.GIST_TIMEOUT


def test_render_returns_textual_screen():
    assert isinstance(WelcomeScreen().render(), t.Screen)

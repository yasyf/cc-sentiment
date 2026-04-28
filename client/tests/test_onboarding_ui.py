from __future__ import annotations

from dataclasses import dataclass

import pytest
from textual import screen as t

from cc_sentiment.onboarding import Stage, State as FsmState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class WelcomeState(BaseState):
    busy: bool = False

    @classmethod
    def empty(cls) -> WelcomeState:
        return cls()


class WelcomeScreen(Screen[WelcomeState]):
    State = WelcomeState

    @classmethod
    def matcher(cls) -> FsmState:
        return FsmState(stage=Stage.WELCOME)

    def render(self) -> t.Screen:
        return t.Screen()


def test_screen_is_abstract():
    with pytest.raises(TypeError):
        Screen()  # type: ignore[abstract]


def test_base_state_is_abstract():
    with pytest.raises(TypeError):
        BaseState()  # type: ignore[abstract]


def test_concrete_subclass_initializes_state_via_empty():
    instance = WelcomeScreen()
    assert isinstance(instance.state, WelcomeState)
    assert instance.state.busy is False


def test_init_uses_subclass_empty_not_default_constructor():
    @dataclass(frozen=True)
    class SeededState(BaseState):
        counter: int = 0

        @classmethod
        def empty(cls) -> SeededState:
            return cls(counter=42)

    class SeededScreen(Screen[SeededState]):
        State = SeededState

        @classmethod
        def matcher(cls) -> FsmState:
            return FsmState(stage=Stage.WELCOME)

        def render(self) -> t.Screen:
            return t.Screen()

    assert SeededScreen().state.counter == 42


def test_matcher_returns_fsm_state_shape():
    pattern = WelcomeScreen.matcher()
    assert isinstance(pattern, FsmState)
    assert pattern.stage is Stage.WELCOME


def test_matcher_can_describe_a_substate():
    from cc_sentiment.onboarding import TroubleReason

    @dataclass(frozen=True)
    class GistTroubleState(BaseState):
        @classmethod
        def empty(cls) -> GistTroubleState:
            return cls()

    class GistTroubleScreen(Screen[GistTroubleState]):
        State = GistTroubleState

        @classmethod
        def matcher(cls) -> FsmState:
            return FsmState(
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

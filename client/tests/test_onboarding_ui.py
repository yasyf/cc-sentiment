from __future__ import annotations

from dataclasses import dataclass

import pytest
from textual.widget import Widget

from cc_sentiment.onboarding import Stage
from cc_sentiment.onboarding.ui import Screen


def test_screen_is_abstract():
    with pytest.raises(TypeError):
        Screen()  # type: ignore[abstract]


def test_concrete_subclass_satisfies_contract():
    @dataclass
    class WelcomeState:
        busy: bool = False

    class WelcomeScreen(Screen):
        STAGE = Stage.WELCOME
        State = WelcomeState

        def screen(self) -> Widget:
            return Widget()

    instance = WelcomeScreen()
    assert instance.STAGE is Stage.WELCOME
    assert instance.State is WelcomeState
    assert isinstance(instance.screen(), Widget)


def test_stage_class_attr_works_as_match_pattern():
    class WelcomeScreen(Screen):
        STAGE = Stage.WELCOME
        State = type("State", (), {})

        def screen(self) -> Widget:
            return Widget()

    class TroubleScreen(Screen):
        STAGE = Stage.TROUBLE
        State = type("State", (), {})

        def screen(self) -> Widget:
            return Widget()

    def dispatch(stage: Stage) -> str:
        match stage:
            case WelcomeScreen.STAGE:
                return "welcome"
            case TroubleScreen.STAGE:
                return "trouble"
            case _:
                return "other"

    assert dispatch(Stage.WELCOME) == "welcome"
    assert dispatch(Stage.TROUBLE) == "trouble"
    assert dispatch(Stage.DONE) == "other"

from __future__ import annotations

from dataclasses import dataclass

import pytest
from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState, TroubleReason
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.onboarding.ui.screens import (
    BlockedScreen,
    DoneScreen,
    EmailScreen,
    GhAddScreen,
    GistTroubleScreen,
    InboxScreen,
    InitialScreen,
    KeyPickScreen,
    PublishScreen,
    SavedRetryScreen,
    SshMethodScreen,
    UserFormScreen,
    VerifyTroubleScreen,
    WelcomeScreen,
    WorkingScreen,
)


ALL_SCREENS: tuple[type[Screen], ...] = (
    InitialScreen, SavedRetryScreen, WelcomeScreen, UserFormScreen,
    KeyPickScreen, SshMethodScreen, WorkingScreen, PublishScreen,
    GhAddScreen, EmailScreen, InboxScreen, GistTroubleScreen,
    VerifyTroubleScreen, BlockedScreen, DoneScreen,
)


@dataclass(frozen=True)
class WelcomeState(BaseState):
    busy: bool = False


class _MinimalScreen(Screen[WelcomeState]):
    State = WelcomeState

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.WELCOME)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {"title": "x"}

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
    instance = _MinimalScreen()
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

        @classmethod
        def strings(cls) -> dict[str, str]:
            return {}

        def render(self) -> t.Screen:
            return t.Screen()

    assert SeededScreen().state.counter == 42


def test_matcher_returns_global_state_shape():
    pattern = WelcomeScreen.matcher()
    assert isinstance(pattern, GlobalState)
    assert pattern.stage is Stage.WELCOME


def test_matcher_can_describe_a_substate():
    pattern = GistTroubleScreen.matcher()
    assert pattern.stage is Stage.TROUBLE
    assert pattern.trouble_reason is TroubleReason.GIST_TIMEOUT


def test_render_returns_textual_screen():
    assert isinstance(_MinimalScreen().render(), t.Screen)


def test_strings_is_required_on_subclasses():
    @dataclass(frozen=True)
    class S(BaseState):
        pass

    class MissingStrings(Screen[S]):  # type: ignore[abstract]
        State = S

        @classmethod
        def matcher(cls) -> GlobalState:
            return GlobalState(stage=Stage.WELCOME)

        def render(self) -> t.Screen:
            return t.Screen()

    with pytest.raises(TypeError):
        MissingStrings()  # type: ignore[abstract]


# ===========================================================================
# Coverage: every screen has unique reachable point + non-empty strings dict
# ===========================================================================


class TestScreenRegistry:
    def test_every_reachable_point_has_a_screen(self):
        reachable: set[tuple[Stage, TroubleReason | None]] = (
            {(stage, None) for stage in Stage if stage is not Stage.TROUBLE}
            | {
                (Stage.TROUBLE, TroubleReason.GIST_TIMEOUT),
                (Stage.TROUBLE, TroubleReason.VERIFY_TIMEOUT),
            }
        )
        owned = {(s.matcher().stage, s.matcher().trouble_reason) for s in ALL_SCREENS}
        assert owned == reachable

    def test_every_matcher_is_unique(self):
        seen: dict[tuple[Stage, TroubleReason | None], type[Screen]] = {}
        for s in ALL_SCREENS:
            key = (s.matcher().stage, s.matcher().trouble_reason)
            assert key not in seen, f"{s} duplicates {seen[key]}"
            seen[key] = s

    def test_every_screen_returns_non_empty_strings(self):
        for s in ALL_SCREENS:
            strings = s.strings()
            assert isinstance(strings, dict), f"{s.__name__}.strings() not a dict"
            assert strings, f"{s.__name__}.strings() returned an empty dict"
            for key, value in strings.items():
                assert isinstance(key, str), f"{s.__name__} key {key!r} not str"
                assert isinstance(value, str), f"{s.__name__}[{key!r}] not str"
                assert value, f"{s.__name__}[{key!r}] is empty"

    def test_every_screen_can_be_instantiated(self):
        for s in ALL_SCREENS:
            s()  # all abstract methods overridden — construction must not raise

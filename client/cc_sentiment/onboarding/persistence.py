from __future__ import annotations

from time import time
from typing import Literal

from cc_sentiment.models import PendingSelectedKey, PendingSetupModel
from cc_sentiment.onboarding.state import (
    ExistingKey,
    Identity,
    KeySource,
    SelectedKey,
    Stage,
    State,
)


ResumeTarget = Literal["gist", "gh_add", "email"]


class Persistence:
    @staticmethod
    def from_state(state: State, *, target: ResumeTarget, started_at: float | None = None) -> PendingSetupModel:
        assert state.selected is not None, "cannot snapshot pending without a selected key"
        return PendingSetupModel(
            selected=Persistence._selected_to_model(state.selected),
            username=state.identity.github_username,
            email=state.identity.email,
            email_usable=state.identity.email_usable,
            target=target,
            started_at=started_at if started_at is not None else time(),
        )

    @staticmethod
    def to_resume_state(model: PendingSetupModel) -> State:
        identity = Identity(
            github_username=model.username,
            email=model.email,
            email_usable=model.email_usable,
        )
        selected = Persistence._selected_from_model(model.selected)
        match model.target:
            case "gist" | "gh_add":
                stage = Stage.PUBLISH if model.target == "gist" else Stage.GH_ADD
            case "email":
                stage = Stage.INBOX
        return State(
            stage=stage,
            identity=identity,
            selected=selected,
            resumed_from_pending=True,
        )

    @staticmethod
    def _selected_to_model(selected: SelectedKey) -> PendingSelectedKey:
        key = selected.key
        return PendingSelectedKey(
            source=selected.source.value,
            fingerprint=key.fingerprint if key else "",
            label=key.label if key else "",
            managed=key.managed if key else False,
            path=key.path if key else None,
            algorithm=key.algorithm if key else "",
        )

    @staticmethod
    def _selected_from_model(model: PendingSelectedKey) -> SelectedKey:
        if model.source == "managed" and not model.fingerprint:
            return SelectedKey(source=KeySource(model.source))
        return SelectedKey(
            source=KeySource(model.source),
            key=ExistingKey(
                fingerprint=model.fingerprint,
                label=model.label,
                managed=model.managed,
                path=model.path,
                algorithm=model.algorithm,
            ),
        )

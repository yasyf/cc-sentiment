from __future__ import annotations

from dataclasses import replace
from typing import ClassVar

from .capabilities import Capabilities
from .events import (
    DiscoveryComplete,
    EmailSent,
    Event,
    GhAddFailed,
    GhAddVerified,
    GistTimedOut,
    GistVerified,
    KeyPicked,
    MethodPicked,
    NoGitHubChosen,
    NoSavedConfig,
    QuitOnboarding,
    RecheckRequested,
    ResumePendingEmail,
    ResumePendingGist,
    SavedConfigChecked,
    SavedRetryRestart,
    StartProcessing,
    TroubleChoseEmail,
    TroubleEditUsername,
    TroubleRestart,
    UsernameSubmitted,
    VerificationOk,
    VerificationTimedOut,
    WorkingFailed,
    WorkingSucceeded,
)
from .state import (
    GistTimeout,
    KeySource,
    SelectedKey,
    SshMethod,
    Stage,
    State,
    Trouble,
    VerifyTimeout,
)


class InvalidTransition(Exception):
    def __init__(self, state: State, event: Event) -> None:
        super().__init__(f"no transition from {state.stage} for {type(event).__name__}")


class Router:
    @staticmethod
    def main_path(state: State, caps: Capabilities) -> Stage:
        ssh_path = caps.has_ssh_keygen and state.github_lookup_allowed
        return (
            Stage.WORKING if ssh_path and caps.gh_authenticated
            else Stage.PUBLISH if ssh_path and state.identity.has_username
            else Stage.EMAIL if caps.has_gpg
            else Stage.USER_FORM if ssh_path
            else Stage.BLOCKED
        )

    @staticmethod
    def to_trouble(state: State, trouble: Trouble) -> State:
        return replace(state, stage=Stage.TROUBLE, trouble=trouble)


class StartMachine:
    OWNS: ClassVar[frozenset[Stage]] = frozenset({Stage.INITIAL, Stage.SAVED_RETRY})

    @classmethod
    def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        match (state.stage, event):
            case (Stage.INITIAL, ResumePendingGist()):
                return replace(state, stage=Stage.PUBLISH, resumed_from_pending=True)
            case (Stage.INITIAL, ResumePendingEmail()):
                return replace(state, stage=Stage.INBOX, resumed_from_pending=True)
            case (Stage.INITIAL, NoSavedConfig()):
                return replace(state, stage=Stage.WELCOME)
            case (Stage.INITIAL | Stage.SAVED_RETRY, SavedConfigChecked(result="ok")):
                return replace(state, stage=Stage.DONE)
            case (Stage.INITIAL | Stage.SAVED_RETRY, SavedConfigChecked(result="invalid")):
                return replace(state, stage=Stage.WELCOME, has_saved_config=True)
            case (Stage.INITIAL, SavedConfigChecked(result="unreachable")):
                return replace(state, stage=Stage.SAVED_RETRY, has_saved_config=True)
            case (Stage.SAVED_RETRY, SavedConfigChecked(result="unreachable")):
                return state
            case (Stage.SAVED_RETRY, SavedRetryRestart()):
                return replace(state, stage=Stage.WELCOME)
        raise InvalidTransition(state, event)


class DiscoveryMachine:
    OWNS: ClassVar[frozenset[Stage]] = frozenset({Stage.WELCOME, Stage.USER_FORM})

    @classmethod
    def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        match (state.stage, event):
            case (Stage.WELCOME, DiscoveryComplete(auto_verified=True) as e):
                return replace(
                    state, stage=Stage.DONE,
                    identity=e.identity, existing_keys=e.existing_keys,
                )
            case (Stage.WELCOME, DiscoveryComplete() as e):
                hydrated = replace(
                    state, identity=e.identity, existing_keys=e.existing_keys,
                )
                if hydrated.existing_keys.any_usable:
                    return replace(hydrated, stage=Stage.KEY_PICK)
                return replace(hydrated, stage=Router.main_path(hydrated, caps))
            case (Stage.USER_FORM, UsernameSubmitted(username=u)):
                hydrated = replace(state, identity=replace(state.identity, github_username=u))
                return replace(hydrated, stage=Router.main_path(hydrated, caps))
            case (Stage.USER_FORM, NoGitHubChosen()):
                hydrated = replace(state, github_lookup_allowed=False)
                return replace(hydrated, stage=Router.main_path(hydrated, caps))
        raise InvalidTransition(state, event)


class KeyMachine:
    OWNS: ClassVar[frozenset[Stage]] = frozenset({Stage.KEY_PICK, Stage.SSH_METHOD})

    @classmethod
    def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        match (state.stage, event):
            case (Stage.KEY_PICK, KeyPicked(source=KeySource.EXISTING_SSH, key=k)):
                return replace(
                    state, stage=Stage.SSH_METHOD,
                    selected=SelectedKey(source=KeySource.EXISTING_SSH, key=k),
                )
            case (Stage.KEY_PICK, KeyPicked(source=KeySource.EXISTING_GPG, key=k)):
                return replace(
                    state, stage=Stage.EMAIL,
                    selected=SelectedKey(source=KeySource.EXISTING_GPG, key=k),
                )
            case (Stage.KEY_PICK, KeyPicked(source=KeySource.MANAGED)):
                hydrated = replace(state, selected=SelectedKey(source=KeySource.MANAGED))
                return replace(hydrated, stage=Router.main_path(hydrated, caps))
            case (Stage.SSH_METHOD, MethodPicked(method=SshMethod.GIST)):
                return replace(state, stage=Stage.PUBLISH)
            case (Stage.SSH_METHOD, MethodPicked(method=SshMethod.GH_ADD)):
                return replace(state, stage=Stage.GH_ADD)
        raise InvalidTransition(state, event)


class WorkflowMachine:
    OWNS: ClassVar[frozenset[Stage]] = frozenset({
        Stage.WORKING, Stage.PUBLISH, Stage.GH_ADD, Stage.EMAIL, Stage.INBOX,
    })

    @classmethod
    def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        match (state.stage, event):
            case (Stage.WORKING, WorkingSucceeded()):
                return replace(state, stage=Stage.DONE)
            case (Stage.WORKING, WorkingFailed()):
                return Router.to_trouble(state, GistTimeout())
            case (Stage.PUBLISH, GistVerified()):
                return replace(state, stage=Stage.DONE)
            case (Stage.PUBLISH, GistTimedOut()):
                return Router.to_trouble(state, GistTimeout())
            case (Stage.PUBLISH, TroubleChoseEmail()):
                return replace(state, stage=Stage.EMAIL)
            case (Stage.GH_ADD, GhAddVerified()):
                return replace(state, stage=Stage.DONE)
            case (Stage.GH_ADD, GhAddFailed()):
                return Router.to_trouble(state, GistTimeout())
            case (Stage.EMAIL, EmailSent()):
                return replace(state, stage=Stage.INBOX)
            case (Stage.INBOX, VerificationOk()):
                return replace(state, stage=Stage.DONE)
            case (Stage.INBOX, VerificationTimedOut(error_code=ec)):
                return Router.to_trouble(state, VerifyTimeout(error_code=ec))
            case (Stage.INBOX, TroubleChoseEmail()):
                return replace(state, stage=Stage.EMAIL, resumed_from_pending=False)
            case (Stage.INBOX, RecheckRequested()):
                return state
        raise InvalidTransition(state, event)


class TroubleMachine:
    OWNS: ClassVar[frozenset[Stage]] = frozenset({Stage.TROUBLE})

    @classmethod
    def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        match (state.stage, event):
            case (Stage.TROUBLE, TroubleEditUsername(new_username=u)):
                return replace(
                    state, stage=Stage.PUBLISH, trouble=None,
                    identity=replace(state.identity, github_username=u),
                )
            case (Stage.TROUBLE, TroubleChoseEmail()):
                return replace(state, stage=Stage.EMAIL, trouble=None)
            case (Stage.TROUBLE, TroubleRestart()):
                return replace(state, stage=Stage.WELCOME, trouble=None)
        raise InvalidTransition(state, event)


class TerminalMachine:
    OWNS: ClassVar[frozenset[Stage]] = frozenset({Stage.DONE, Stage.BLOCKED})

    @classmethod
    def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        match (state.stage, event):
            case (Stage.DONE, StartProcessing()):
                return state
            case (Stage.BLOCKED, QuitOnboarding()):
                return state
        raise InvalidTransition(state, event)


SubMachine = type[
    StartMachine | DiscoveryMachine | KeyMachine | WorkflowMachine | TroubleMachine | TerminalMachine
]


class SetupMachine:
    SUB_MACHINES: ClassVar[tuple[SubMachine, ...]] = (
        StartMachine, DiscoveryMachine, KeyMachine, WorkflowMachine, TroubleMachine, TerminalMachine,
    )
    DISPATCH: ClassVar[dict[Stage, SubMachine]] = {
        stage: sub for sub in SUB_MACHINES for stage in sub.OWNS
    }

    @classmethod
    def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        try:
            sub = cls.DISPATCH[state.stage]
        except KeyError as exc:
            raise InvalidTransition(state, event) from exc
        return sub.transition(state, event, caps)

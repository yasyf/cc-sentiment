from __future__ import annotations

from dataclasses import replace

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
    ResumePendingEmail,
    ResumePendingGist,
    SavedConfigChecked,
    SavedRetryRestart,
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
    KeySource,
    SelectedKey,
    SshMethod,
    Stage,
    State,
    TroubleReason,
)


class InvalidTransition(Exception):
    pass


class SetupMachine:
    @classmethod
    async def transition(cls, state: State, event: Event, caps: Capabilities) -> State:
        match (state.stage, event):
            case (Stage.INITIAL, ResumePendingGist()):
                return replace(state, stage=Stage.PUBLISH)
            case (Stage.INITIAL, ResumePendingEmail()):
                return replace(state, stage=Stage.INBOX)
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

            case (Stage.WELCOME, DiscoveryComplete(auto_verified=True) as e):
                return replace(
                    state, stage=Stage.DONE,
                    identity=e.identity, existing_keys=e.existing_keys,
                )
            case (Stage.WELCOME, DiscoveryComplete() as e):
                return await cls._dispatch_after_discovery(
                    replace(state, identity=e.identity, existing_keys=e.existing_keys),
                    caps,
                )

            case (Stage.USER_FORM, UsernameSubmitted(username=u)):
                return await cls._route_main_path(
                    replace(state, identity=replace(state.identity, github_username=u)),
                    caps,
                )
            case (Stage.USER_FORM, NoGitHubChosen()):
                return await cls._route_main_path(
                    replace(state, github_lookup_allowed=False), caps,
                )

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
                return await cls._route_main_path(
                    replace(state, selected=SelectedKey(source=KeySource.MANAGED)),
                    caps,
                )

            case (Stage.SSH_METHOD, MethodPicked(method=SshMethod.GIST)):
                return replace(state, stage=Stage.PUBLISH)
            case (Stage.SSH_METHOD, MethodPicked(method=SshMethod.GH_ADD)):
                return replace(state, stage=Stage.GH_ADD)

            case (Stage.WORKING, WorkingSucceeded()):
                return replace(state, stage=Stage.DONE)
            case (Stage.WORKING, WorkingFailed()):
                return cls._enter_trouble(state, TroubleReason.GIST_TIMEOUT)

            case (Stage.PUBLISH, GistVerified()):
                return replace(state, stage=Stage.DONE)
            case (Stage.PUBLISH, GistTimedOut()):
                return cls._enter_trouble(state, TroubleReason.GIST_TIMEOUT)

            case (Stage.GH_ADD, GhAddVerified()):
                return replace(state, stage=Stage.DONE)
            case (Stage.GH_ADD, GhAddFailed()):
                return cls._enter_trouble(state, TroubleReason.GIST_TIMEOUT)

            case (Stage.EMAIL, EmailSent()):
                return replace(state, stage=Stage.INBOX)

            case (Stage.INBOX, VerificationOk()):
                return replace(state, stage=Stage.DONE)
            case (Stage.INBOX, VerificationTimedOut()):
                return cls._enter_trouble(state, TroubleReason.VERIFY_TIMEOUT)

            case (Stage.TROUBLE, TroubleEditUsername(new_username=u)):
                return replace(
                    state, stage=Stage.PUBLISH, trouble_reason=None,
                    identity=replace(state.identity, github_username=u),
                )
            case (Stage.TROUBLE, TroubleChoseEmail()):
                return replace(state, stage=Stage.EMAIL, trouble_reason=None)
            case (Stage.TROUBLE, TroubleRestart()):
                return replace(state, stage=Stage.WELCOME, trouble_reason=None)

        raise InvalidTransition(
            f"no transition from {state.stage} for {type(event).__name__}"
        )

    @classmethod
    def _enter_trouble(cls, state: State, reason: TroubleReason) -> State:
        return replace(state, stage=Stage.TROUBLE, trouble_reason=reason)

    @classmethod
    async def _dispatch_after_discovery(cls, state: State, caps: Capabilities) -> State:
        if state.existing_keys.any_usable:
            return replace(state, stage=Stage.KEY_PICK)
        return await cls._route_main_path(state, caps)

    @classmethod
    async def _route_main_path(cls, state: State, caps: Capabilities) -> State:
        ssh_path = await caps.has_ssh_keygen and state.github_lookup_allowed
        next_stage = (
            Stage.WORKING if ssh_path and await caps.gh_authenticated
            else Stage.PUBLISH if ssh_path and state.identity.has_username
            else Stage.EMAIL if await caps.has_gpg
            else Stage.USER_FORM if ssh_path
            else Stage.BLOCKED
        )
        return replace(state, stage=next_stage)

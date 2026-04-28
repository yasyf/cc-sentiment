from __future__ import annotations

from unittest.mock import Mock

import pytest

from cc_sentiment.onboarding import (
    Capabilities,
    DiscoveryComplete,
    EmailSent,
    ExistingKey,
    ExistingKeys,
    GhAddFailed,
    GhAddVerified,
    GistTimedOut,
    GistVerified,
    Identity,
    InvalidTransition,
    KeyPicked,
    KeySource,
    MethodPicked,
    NoGitHubChosen,
    NoSavedConfig,
    ResumePendingEmail,
    ResumePendingGist,
    SavedConfigChecked,
    SavedRetryRestart,
    SelectedKey,
    SetupMachine,
    SshMethod,
    Stage,
    State,
    TroubleChoseEmail,
    TroubleEditUsername,
    TroubleReason,
    TroubleRestart,
    UsernameSubmitted,
    VerificationOk,
    VerificationTimedOut,
    WorkingFailed,
    WorkingSucceeded,
)


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


def state_at(stage: Stage, **overrides) -> State:
    return State(stage=stage, **overrides)


def existing_ssh(label: str = "id_ed25519") -> ExistingKey:
    return ExistingKey(fingerprint=f"SHA256:{label}", label=label)


def existing_gpg(label: str = "alice@example.com") -> ExistingKey:
    return ExistingKey(fingerprint="DEADBEEF", label=label)


def step(state: State, event, caps: Capabilities | None = None) -> State:
    return SetupMachine.transition(state, event, caps or fake_caps())


# ===========================================================================
# Start subtree: INITIAL → DONE | SAVED_RETRY | WELCOME | PUBLISH | INBOX
# ===========================================================================


class TestInitialTransitions:
    def test_resume_pending_gist_to_publish(self):
        result = step(state_at(Stage.INITIAL), ResumePendingGist())
        assert result.stage is Stage.PUBLISH

    def test_resume_pending_email_to_inbox(self):
        result = step(state_at(Stage.INITIAL), ResumePendingEmail())
        assert result.stage is Stage.INBOX

    def test_no_saved_config_to_welcome(self):
        result = step(state_at(Stage.INITIAL), NoSavedConfig())
        assert result.stage is Stage.WELCOME
        assert result.has_saved_config is False

    def test_saved_ok_to_done(self):
        result = step(state_at(Stage.INITIAL), SavedConfigChecked(result="ok"))
        assert result.stage is Stage.DONE

    def test_saved_invalid_to_welcome_with_flag(self):
        result = step(state_at(Stage.INITIAL), SavedConfigChecked(result="invalid"))
        assert result.stage is Stage.WELCOME
        assert result.has_saved_config is True

    def test_saved_unreachable_to_saved_retry(self):
        result = step(state_at(Stage.INITIAL), SavedConfigChecked(result="unreachable"))
        assert result.stage is Stage.SAVED_RETRY
        assert result.has_saved_config is True


# ===========================================================================
# Start subtree: SAVED_RETRY → DONE | WELCOME | (stay)
# ===========================================================================


class TestSavedRetryTransitions:
    def test_retry_ok_to_done(self):
        result = step(
            state_at(Stage.SAVED_RETRY, has_saved_config=True),
            SavedConfigChecked(result="ok"),
        )
        assert result.stage is Stage.DONE

    def test_retry_invalid_to_welcome(self):
        result = step(
            state_at(Stage.SAVED_RETRY, has_saved_config=True),
            SavedConfigChecked(result="invalid"),
        )
        assert result.stage is Stage.WELCOME

    def test_retry_unreachable_stays(self):
        prev = state_at(Stage.SAVED_RETRY, has_saved_config=True)
        assert step(prev, SavedConfigChecked(result="unreachable")) == prev

    def test_restart_to_welcome(self):
        result = step(state_at(Stage.SAVED_RETRY), SavedRetryRestart())
        assert result.stage is Stage.WELCOME


# ===========================================================================
# Discovery subtree: WELCOME → DONE | KEY_PICK | WORKING | PUBLISH | EMAIL | USER_FORM | BLOCKED
# ===========================================================================


class TestWelcomeTransitions:
    def test_auto_verified_to_done(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"),
            existing_keys=ExistingKeys(ssh=(existing_ssh(),)),
            auto_verified=True,
        )
        result = step(
            state_at(Stage.WELCOME), event,
            fake_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.DONE
        assert result.identity.github_username == "alice"

    def test_existing_keys_to_key_pick(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"),
            existing_keys=ExistingKeys(ssh=(existing_ssh(),)),
        )
        result = step(
            state_at(Stage.WELCOME), event,
            fake_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.KEY_PICK

    def test_gh_authed_to_working(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = step(
            state_at(Stage.WELCOME), event,
            fake_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING

    def test_username_no_gh_to_publish(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = step(
            state_at(Stage.WELCOME), event, fake_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH

    def test_no_username_with_gpg_to_email(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = step(
            state_at(Stage.WELCOME), event,
            fake_caps(has_ssh_keygen=True, has_gpg=True),
        )
        assert result.stage is Stage.EMAIL

    def test_gpg_only_to_email(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = step(state_at(Stage.WELCOME), event, fake_caps(has_gpg=True))
        assert result.stage is Stage.EMAIL

    def test_ssh_only_no_username_to_user_form(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = step(state_at(Stage.WELCOME), event, fake_caps(has_ssh_keygen=True))
        assert result.stage is Stage.USER_FORM

    def test_no_path_to_blocked(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = step(state_at(Stage.WELCOME), event, fake_caps())
        assert result.stage is Stage.BLOCKED

    def test_github_disallowed_with_no_gpg_to_blocked(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = step(
            state_at(Stage.WELCOME, github_lookup_allowed=False), event,
            fake_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.BLOCKED

    def test_github_disallowed_with_gpg_to_email(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = step(
            state_at(Stage.WELCOME, github_lookup_allowed=False), event,
            fake_caps(has_ssh_keygen=True, has_gpg=True),
        )
        assert result.stage is Stage.EMAIL


# ===========================================================================
# Discovery subtree: USER_FORM → PUBLISH | WORKING | EMAIL | BLOCKED
# ===========================================================================


class TestUserFormTransitions:
    def test_username_no_gh_to_publish(self):
        result = step(
            state_at(Stage.USER_FORM), UsernameSubmitted(username="alice"),
            fake_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH
        assert result.identity.github_username == "alice"

    def test_username_with_gh_to_working(self):
        result = step(
            state_at(Stage.USER_FORM), UsernameSubmitted(username="alice"),
            fake_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING

    def test_no_github_with_gpg_to_email(self):
        result = step(
            state_at(Stage.USER_FORM), NoGitHubChosen(),
            fake_caps(has_ssh_keygen=True, has_gpg=True),
        )
        assert result.stage is Stage.EMAIL
        assert result.github_lookup_allowed is False

    def test_no_github_without_gpg_to_blocked(self):
        result = step(
            state_at(Stage.USER_FORM), NoGitHubChosen(),
            fake_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.BLOCKED


# ===========================================================================
# Key subtree: KEY_PICK → SSH_METHOD | EMAIL | WORKING | PUBLISH | EMAIL | BLOCKED
# ===========================================================================


class TestKeyPickTransitions:
    def test_existing_ssh_to_ssh_method(self):
        key = existing_ssh()
        result = step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.EXISTING_SSH, key=key),
            fake_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.SSH_METHOD
        assert result.selected == SelectedKey(source=KeySource.EXISTING_SSH, key=key)

    def test_existing_gpg_to_email(self):
        key = existing_gpg()
        result = step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.EXISTING_GPG, key=key),
            fake_caps(has_gpg=True),
        )
        assert result.stage is Stage.EMAIL
        assert result.selected == SelectedKey(source=KeySource.EXISTING_GPG, key=key)

    def test_managed_with_gh_to_working(self):
        result = step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.MANAGED),
            fake_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING
        assert result.selected == SelectedKey(source=KeySource.MANAGED)

    def test_managed_with_username_no_gh_to_publish(self):
        result = step(
            state_at(Stage.KEY_PICK, identity=Identity(github_username="alice")),
            KeyPicked(source=KeySource.MANAGED),
            fake_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH

    def test_managed_with_gpg_to_email(self):
        result = step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.MANAGED),
            fake_caps(has_gpg=True),
        )
        assert result.stage is Stage.EMAIL

    def test_managed_with_no_path_to_blocked(self):
        result = step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.MANAGED), fake_caps(),
        )
        assert result.stage is Stage.BLOCKED


# ===========================================================================
# Key subtree: SSH_METHOD → PUBLISH | GH_ADD
# ===========================================================================


class TestSshMethodTransitions:
    @pytest.fixture
    def prior_ssh_pick(self) -> State:
        return state_at(
            Stage.SSH_METHOD,
            selected=SelectedKey(source=KeySource.EXISTING_SSH, key=existing_ssh()),
        )

    def test_gist_to_publish(self, prior_ssh_pick: State):
        assert step(prior_ssh_pick, MethodPicked(method=SshMethod.GIST)).stage is Stage.PUBLISH

    def test_gh_add_to_gh_add_stage(self, prior_ssh_pick: State):
        assert step(prior_ssh_pick, MethodPicked(method=SshMethod.GH_ADD)).stage is Stage.GH_ADD


# ===========================================================================
# Workflow subtree: WORKING / PUBLISH / GH_ADD / INBOX → DONE | TROUBLE
# ===========================================================================


class TestWorkflowTransitions:
    def test_working_succeeded_to_done(self):
        assert step(state_at(Stage.WORKING), WorkingSucceeded()).stage is Stage.DONE

    def test_working_failed_to_trouble_gist(self):
        result = step(state_at(Stage.WORKING), WorkingFailed())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.GIST_TIMEOUT

    def test_publish_verified_to_done(self):
        assert step(state_at(Stage.PUBLISH), GistVerified()).stage is Stage.DONE

    def test_publish_timeout_to_trouble_gist(self):
        result = step(state_at(Stage.PUBLISH), GistTimedOut())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.GIST_TIMEOUT

    def test_gh_add_verified_to_done(self):
        assert step(state_at(Stage.GH_ADD), GhAddVerified()).stage is Stage.DONE

    def test_gh_add_failed_to_trouble_gist(self):
        result = step(state_at(Stage.GH_ADD), GhAddFailed())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.GIST_TIMEOUT

    def test_email_sent_to_inbox(self):
        assert step(state_at(Stage.EMAIL), EmailSent()).stage is Stage.INBOX

    def test_inbox_verified_to_done(self):
        assert step(state_at(Stage.INBOX), VerificationOk()).stage is Stage.DONE

    def test_inbox_timeout_to_trouble_verify(self):
        result = step(state_at(Stage.INBOX), VerificationTimedOut())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.VERIFY_TIMEOUT


# ===========================================================================
# Trouble subtree: TROUBLE → PUBLISH | EMAIL | WELCOME
# ===========================================================================


class TestTroubleTransitions:
    def test_edit_username_to_publish_with_new_name(self):
        prev = state_at(
            Stage.TROUBLE,
            identity=Identity(github_username="old"),
            trouble_reason=TroubleReason.GIST_TIMEOUT,
        )
        result = step(prev, TroubleEditUsername(new_username="new"))
        assert result.stage is Stage.PUBLISH
        assert result.identity.github_username == "new"
        assert result.trouble_reason is None

    def test_chose_email_to_email(self):
        prev = state_at(Stage.TROUBLE, trouble_reason=TroubleReason.GIST_TIMEOUT)
        result = step(prev, TroubleChoseEmail())
        assert result.stage is Stage.EMAIL
        assert result.trouble_reason is None

    def test_restart_to_welcome(self):
        prev = state_at(Stage.TROUBLE, trouble_reason=TroubleReason.VERIFY_TIMEOUT)
        result = step(prev, TroubleRestart())
        assert result.stage is Stage.WELCOME
        assert result.trouble_reason is None


# ===========================================================================
# Dispatcher: SetupMachine routes by stage to the right sub-machine
# ===========================================================================


class TestDispatcher:
    def test_every_stage_has_a_handler_except_terminal(self):
        terminal = {Stage.DONE, Stage.BLOCKED}
        owned = set(SetupMachine.DISPATCH)
        assert owned | terminal == set(Stage)

    def test_stages_are_disjoint_across_sub_machines(self):
        seen: dict[Stage, type] = {}
        for sub in SetupMachine.SUB_MACHINES:
            for stage in sub.OWNS:
                assert stage not in seen, f"{stage} owned by {seen[stage]} and {sub}"
                seen[stage] = sub

    def test_unknown_stage_raises(self):
        with pytest.raises(InvalidTransition):
            step(state_at(Stage.DONE), GistVerified())


# ===========================================================================
# Invariants
# ===========================================================================


class TestInvariants:
    def test_unmodeled_event_for_owned_stage_raises(self):
        with pytest.raises(InvalidTransition):
            step(state_at(Stage.PUBLISH), TroubleRestart())

    def test_state_is_immutable(self):
        prev = state_at(Stage.INITIAL)
        step(prev, NoSavedConfig())
        assert prev.stage is Stage.INITIAL

    def test_default_state_starts_in_initial(self):
        assert State().stage is Stage.INITIAL
        assert State().github_lookup_allowed is True
        assert State().selected is None
        assert State().trouble_reason is None

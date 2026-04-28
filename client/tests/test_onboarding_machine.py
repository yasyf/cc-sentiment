from __future__ import annotations

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


@pytest.fixture(autouse=True)
def reset_caps():
    Capabilities.reset()
    yield
    Capabilities.reset()


def with_caps(**overrides: bool) -> Capabilities:
    seed: dict[str, bool] = {
        "has_ssh_keygen": False,
        "has_gpg": False,
        "has_gh": False,
        "gh_authenticated": False,
        "has_brew": False,
        "can_clipboard": True,
        "can_open_browser": True,
    }
    seed.update(overrides)
    Capabilities.reset()
    Capabilities.seed(**seed)
    return Capabilities()


def state_at(stage: Stage, **overrides) -> State:
    return State(stage=stage, **overrides)


def existing_ssh(label: str = "id_ed25519") -> ExistingKey:
    return ExistingKey(fingerprint=f"SHA256:{label}", label=label)


def existing_gpg(label: str = "alice@example.com") -> ExistingKey:
    return ExistingKey(fingerprint="DEADBEEF", label=label)


async def step(state: State, event, caps: Capabilities | None = None) -> State:
    return await SetupMachine.transition(state, event, caps or with_caps())


# ===========================================================================
# INITIAL → DONE | SAVED_RETRY | WELCOME | PUBLISH | INBOX
# ===========================================================================


class TestInitialTransitions:
    async def test_resume_pending_gist_to_publish(self):
        result = await step(state_at(Stage.INITIAL), ResumePendingGist())
        assert result.stage is Stage.PUBLISH

    async def test_resume_pending_email_to_inbox(self):
        result = await step(state_at(Stage.INITIAL), ResumePendingEmail())
        assert result.stage is Stage.INBOX

    async def test_no_saved_config_to_welcome(self):
        result = await step(state_at(Stage.INITIAL), NoSavedConfig())
        assert result.stage is Stage.WELCOME
        assert result.has_saved_config is False

    async def test_saved_ok_to_done(self):
        result = await step(state_at(Stage.INITIAL), SavedConfigChecked(result="ok"))
        assert result.stage is Stage.DONE

    async def test_saved_invalid_to_welcome_with_flag(self):
        result = await step(state_at(Stage.INITIAL), SavedConfigChecked(result="invalid"))
        assert result.stage is Stage.WELCOME
        assert result.has_saved_config is True

    async def test_saved_unreachable_to_saved_retry(self):
        result = await step(state_at(Stage.INITIAL), SavedConfigChecked(result="unreachable"))
        assert result.stage is Stage.SAVED_RETRY
        assert result.has_saved_config is True


# ===========================================================================
# SAVED_RETRY → DONE | WELCOME | (stay)
# ===========================================================================


class TestSavedRetryTransitions:
    async def test_retry_ok_to_done(self):
        result = await step(
            state_at(Stage.SAVED_RETRY, has_saved_config=True),
            SavedConfigChecked(result="ok"),
        )
        assert result.stage is Stage.DONE

    async def test_retry_invalid_to_welcome(self):
        result = await step(
            state_at(Stage.SAVED_RETRY, has_saved_config=True),
            SavedConfigChecked(result="invalid"),
        )
        assert result.stage is Stage.WELCOME

    async def test_retry_unreachable_stays(self):
        prev = state_at(Stage.SAVED_RETRY, has_saved_config=True)
        result = await step(prev, SavedConfigChecked(result="unreachable"))
        assert result == prev

    async def test_restart_to_welcome(self):
        result = await step(state_at(Stage.SAVED_RETRY), SavedRetryRestart())
        assert result.stage is Stage.WELCOME


# ===========================================================================
# WELCOME → DONE | KEY_PICK | WORKING | PUBLISH | EMAIL | USER_FORM | BLOCKED
# ===========================================================================


class TestWelcomeTransitions:
    async def test_auto_verified_to_done(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"),
            existing_keys=ExistingKeys(ssh=(existing_ssh(),)),
            auto_verified=True,
        )
        result = await step(
            state_at(Stage.WELCOME), event,
            with_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.DONE
        assert result.identity.github_username == "alice"

    async def test_existing_keys_to_key_pick(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"),
            existing_keys=ExistingKeys(ssh=(existing_ssh(),)),
        )
        result = await step(
            state_at(Stage.WELCOME), event,
            with_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.KEY_PICK

    async def test_gh_authed_to_working(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = await step(
            state_at(Stage.WELCOME), event,
            with_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING

    async def test_username_no_gh_to_publish(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = await step(
            state_at(Stage.WELCOME), event, with_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH

    async def test_no_username_with_gpg_to_email(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = await step(
            state_at(Stage.WELCOME), event,
            with_caps(has_ssh_keygen=True, has_gpg=True),
        )
        assert result.stage is Stage.EMAIL

    async def test_gpg_only_to_email(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = await step(state_at(Stage.WELCOME), event, with_caps(has_gpg=True))
        assert result.stage is Stage.EMAIL

    async def test_ssh_only_no_username_to_user_form(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = await step(state_at(Stage.WELCOME), event, with_caps(has_ssh_keygen=True))
        assert result.stage is Stage.USER_FORM

    async def test_no_path_to_blocked(self):
        event = DiscoveryComplete(identity=Identity(), existing_keys=ExistingKeys())
        result = await step(state_at(Stage.WELCOME), event, with_caps())
        assert result.stage is Stage.BLOCKED

    async def test_github_disallowed_with_no_gpg_to_blocked(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = await step(
            state_at(Stage.WELCOME, github_lookup_allowed=False), event,
            with_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.BLOCKED

    async def test_github_disallowed_with_gpg_to_email(self):
        event = DiscoveryComplete(
            identity=Identity(github_username="alice"), existing_keys=ExistingKeys(),
        )
        result = await step(
            state_at(Stage.WELCOME, github_lookup_allowed=False), event,
            with_caps(has_ssh_keygen=True, has_gpg=True),
        )
        assert result.stage is Stage.EMAIL


# ===========================================================================
# USER_FORM → PUBLISH | WORKING | EMAIL | BLOCKED
# ===========================================================================


class TestUserFormTransitions:
    async def test_username_no_gh_to_publish(self):
        result = await step(
            state_at(Stage.USER_FORM), UsernameSubmitted(username="alice"),
            with_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH
        assert result.identity.github_username == "alice"

    async def test_username_with_gh_to_working(self):
        result = await step(
            state_at(Stage.USER_FORM), UsernameSubmitted(username="alice"),
            with_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING

    async def test_no_github_with_gpg_to_email(self):
        result = await step(
            state_at(Stage.USER_FORM), NoGitHubChosen(),
            with_caps(has_ssh_keygen=True, has_gpg=True),
        )
        assert result.stage is Stage.EMAIL
        assert result.github_lookup_allowed is False

    async def test_no_github_without_gpg_to_blocked(self):
        result = await step(
            state_at(Stage.USER_FORM), NoGitHubChosen(),
            with_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.BLOCKED


# ===========================================================================
# KEY_PICK → SSH_METHOD | EMAIL | WORKING | PUBLISH | EMAIL | BLOCKED
# ===========================================================================


class TestKeyPickTransitions:
    async def test_existing_ssh_to_ssh_method(self):
        key = existing_ssh()
        result = await step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.EXISTING_SSH, key=key),
            with_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.SSH_METHOD
        assert result.selected == SelectedKey(source=KeySource.EXISTING_SSH, key=key)

    async def test_existing_gpg_to_email(self):
        key = existing_gpg()
        result = await step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.EXISTING_GPG, key=key),
            with_caps(has_gpg=True),
        )
        assert result.stage is Stage.EMAIL
        assert result.selected == SelectedKey(source=KeySource.EXISTING_GPG, key=key)

    async def test_managed_with_gh_to_working(self):
        result = await step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.MANAGED),
            with_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING
        assert result.selected == SelectedKey(source=KeySource.MANAGED)

    async def test_managed_with_username_no_gh_to_publish(self):
        result = await step(
            state_at(Stage.KEY_PICK, identity=Identity(github_username="alice")),
            KeyPicked(source=KeySource.MANAGED),
            with_caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH

    async def test_managed_with_gpg_to_email(self):
        result = await step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.MANAGED),
            with_caps(has_gpg=True),
        )
        assert result.stage is Stage.EMAIL

    async def test_managed_with_no_path_to_blocked(self):
        result = await step(
            state_at(Stage.KEY_PICK), KeyPicked(source=KeySource.MANAGED), with_caps(),
        )
        assert result.stage is Stage.BLOCKED


# ===========================================================================
# SSH_METHOD → PUBLISH | GH_ADD
# ===========================================================================


class TestSshMethodTransitions:
    @pytest.fixture
    def prior_ssh_pick(self) -> State:
        return state_at(
            Stage.SSH_METHOD,
            selected=SelectedKey(source=KeySource.EXISTING_SSH, key=existing_ssh()),
        )

    async def test_gist_to_publish(self, prior_ssh_pick: State):
        result = await step(prior_ssh_pick, MethodPicked(method=SshMethod.GIST))
        assert result.stage is Stage.PUBLISH

    async def test_gh_add_to_gh_add_stage(self, prior_ssh_pick: State):
        result = await step(prior_ssh_pick, MethodPicked(method=SshMethod.GH_ADD))
        assert result.stage is Stage.GH_ADD


# ===========================================================================
# WORKING / PUBLISH / GH_ADD → DONE | TROUBLE
# ===========================================================================


class TestWorkingTransitions:
    async def test_succeeded_to_done(self):
        assert (await step(state_at(Stage.WORKING), WorkingSucceeded())).stage is Stage.DONE

    async def test_failed_to_trouble_gist_timeout(self):
        result = await step(state_at(Stage.WORKING), WorkingFailed())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.GIST_TIMEOUT


class TestPublishTransitions:
    async def test_gist_verified_to_done(self):
        assert (await step(state_at(Stage.PUBLISH), GistVerified())).stage is Stage.DONE

    async def test_timed_out_to_trouble_gist(self):
        result = await step(state_at(Stage.PUBLISH), GistTimedOut())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.GIST_TIMEOUT


class TestGhAddTransitions:
    async def test_verified_to_done(self):
        assert (await step(state_at(Stage.GH_ADD), GhAddVerified())).stage is Stage.DONE

    async def test_failed_to_trouble_gist(self):
        result = await step(state_at(Stage.GH_ADD), GhAddFailed())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.GIST_TIMEOUT


# ===========================================================================
# EMAIL → INBOX; INBOX → DONE | TROUBLE
# ===========================================================================


class TestEmailInboxTransitions:
    async def test_email_sent_to_inbox(self):
        assert (await step(state_at(Stage.EMAIL), EmailSent())).stage is Stage.INBOX

    async def test_inbox_verified_to_done(self):
        assert (await step(state_at(Stage.INBOX), VerificationOk())).stage is Stage.DONE

    async def test_inbox_timeout_to_trouble_verify(self):
        result = await step(state_at(Stage.INBOX), VerificationTimedOut())
        assert result.stage is Stage.TROUBLE
        assert result.trouble_reason is TroubleReason.VERIFY_TIMEOUT


# ===========================================================================
# TROUBLE → PUBLISH | EMAIL | WELCOME
# ===========================================================================


class TestTroubleTransitions:
    async def test_edit_username_to_publish_with_new_name(self):
        prev = state_at(
            Stage.TROUBLE,
            identity=Identity(github_username="old"),
            trouble_reason=TroubleReason.GIST_TIMEOUT,
        )
        result = await step(prev, TroubleEditUsername(new_username="new"))
        assert result.stage is Stage.PUBLISH
        assert result.identity.github_username == "new"
        assert result.trouble_reason is None

    async def test_chose_email_to_email(self):
        prev = state_at(Stage.TROUBLE, trouble_reason=TroubleReason.GIST_TIMEOUT)
        result = await step(prev, TroubleChoseEmail())
        assert result.stage is Stage.EMAIL
        assert result.trouble_reason is None

    async def test_restart_to_welcome(self):
        prev = state_at(Stage.TROUBLE, trouble_reason=TroubleReason.VERIFY_TIMEOUT)
        result = await step(prev, TroubleRestart())
        assert result.stage is Stage.WELCOME
        assert result.trouble_reason is None


# ===========================================================================
# Invariants
# ===========================================================================


class TestInvariants:
    async def test_unmodeled_transition_raises(self):
        with pytest.raises(InvalidTransition):
            await step(state_at(Stage.DONE), GistVerified())

    async def test_state_is_immutable(self):
        prev = state_at(Stage.INITIAL)
        await step(prev, NoSavedConfig())
        assert prev.stage is Stage.INITIAL

    def test_default_state_starts_in_initial(self):
        assert State().stage is Stage.INITIAL
        assert State().github_lookup_allowed is True
        assert State().selected is None
        assert State().trouble_reason is None

from __future__ import annotations

from unittest.mock import Mock

import pytest

from cc_sentiment.onboarding import (
    Capabilities,
    GistTimeout,
    SetupMachine,
    Stage,
    State,
    VerifyTimeout,
)
from cc_sentiment.onboarding.events import (
    DiscoveryComplete,
    EmailSent,
    GhAddFailed,
    GhAddVerified,
    GistTimedOut,
    GistVerified,
    KeyPicked,
    MethodPicked,
    NoGitHubChosen,
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
from cc_sentiment.onboarding.state import (
    ExistingKey,
    ExistingKeys,
    Identity,
    KeySource,
    SshMethod,
    Trouble,
)


# Direct mapping of the mermaid diagram in the integrated UX plan, plus the
# clarifications we've made:
#
#   Saved → Done | Retry | Welcome | Publish | Inbox
#   Welcome → Done | KeyPick | Working | Publish | Email | UserForm | Blocked
#   UserForm → Publish | Email
#   KeyPick → SshMethod | Email | Working | Publish | Email
#   SshMethod → Publish | GhAdd
#   GhAdd → Done | Trouble
#   Working → Done | Trouble
#   Publish → Done | Trouble
#   Email → Inbox
#   Inbox → Done | Trouble
#   Retry → Done | Welcome
#   Trouble → Publish | Email | Welcome
#
# Each edge below is named exactly after the diagram label.


def caps(**overrides: bool) -> Capabilities:
    defaults: dict[str, bool] = {
        "has_ssh_keygen": False, "has_gpg": False, "has_gh": False,
        "gh_authenticated": False, "has_brew": False,
    }
    Capabilities.reset()
    Capabilities.seed(**(defaults | overrides))
    return Capabilities()


def step(state: State, event, c: Capabilities | None = None) -> State:
    import anyio
    return anyio.run(SetupMachine.transition, state, event, c or caps())


def existing_ssh() -> ExistingKey:
    return ExistingKey(fingerprint="SHA256:test", label="id_ed25519")


def existing_gpg() -> ExistingKey:
    return ExistingKey(fingerprint="DEADBEEF", label="alice@example.com")


# ---------------------------------------------------------------------------
# Saved → ...
# ---------------------------------------------------------------------------


class TestSavedEdges:
    def test_saved_to_done_when_valid(self):
        result = step(State(stage=Stage.INITIAL), SavedConfigChecked(result="ok"))
        assert result.stage is Stage.DONE

    def test_saved_to_retry_when_network(self):
        result = step(State(stage=Stage.INITIAL), SavedConfigChecked(result="unreachable"))
        assert result.stage is Stage.SAVED_RETRY

    def test_saved_to_welcome_when_invalid(self):
        result = step(State(stage=Stage.INITIAL), SavedConfigChecked(result="invalid"))
        assert result.stage is Stage.WELCOME
        assert result.has_saved_config is True

    def test_saved_to_publish_when_pending_gist(self):
        result = step(State(stage=Stage.INITIAL), ResumePendingGist())
        assert result.stage is Stage.PUBLISH
        assert result.resumed_from_pending is True

    def test_saved_to_inbox_when_pending_email(self):
        result = step(State(stage=Stage.INITIAL), ResumePendingEmail())
        assert result.stage is Stage.INBOX
        assert result.resumed_from_pending is True


# ---------------------------------------------------------------------------
# Welcome → ...
# ---------------------------------------------------------------------------


class TestWelcomeEdges:
    def _discovery(
        self,
        identity: Identity = Identity(),
        existing_keys: ExistingKeys = ExistingKeys(),
        auto_verified: bool = False,
    ) -> DiscoveryComplete:
        return DiscoveryComplete(
            identity=identity,
            existing_keys=existing_keys,
            auto_verified=auto_verified,
        )

    def test_welcome_to_done_when_existing_verifies(self):
        result = step(
            State(stage=Stage.WELCOME),
            self._discovery(auto_verified=True),
        )
        assert result.stage is Stage.DONE

    def test_welcome_to_keypick_when_usable_keys_remain(self):
        result = step(
            State(stage=Stage.WELCOME),
            self._discovery(existing_keys=ExistingKeys(ssh=(existing_ssh(),))),
            caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.KEY_PICK

    def test_welcome_to_working_when_gh_and_ssh(self):
        result = step(
            State(stage=Stage.WELCOME),
            self._discovery(identity=Identity(github_username="alice")),
            caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING

    def test_welcome_to_publish_when_user_and_ssh(self):
        result = step(
            State(stage=Stage.WELCOME),
            self._discovery(identity=Identity(github_username="alice")),
            caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH

    def test_welcome_to_email_when_gpg(self):
        result = step(
            State(stage=Stage.WELCOME),
            self._discovery(),
            caps(has_gpg=True),
        )
        assert result.stage is Stage.EMAIL

    def test_welcome_to_userform_when_ssh_only(self):
        result = step(
            State(stage=Stage.WELCOME),
            self._discovery(),
            caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.USER_FORM

    def test_welcome_to_blocked_when_no_path(self):
        result = step(
            State(stage=Stage.WELCOME),
            self._discovery(),
            caps(),
        )
        assert result.stage is Stage.BLOCKED


# ---------------------------------------------------------------------------
# UserForm → ...
# ---------------------------------------------------------------------------


class TestUserFormEdges:
    def test_userform_to_publish_when_username_ok(self):
        result = step(
            State(stage=Stage.USER_FORM),
            UsernameSubmitted(username="alice"),
            caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH

    def test_userform_to_email_when_no_github_and_gpg(self):
        result = step(
            State(stage=Stage.USER_FORM),
            NoGitHubChosen(),
            caps(has_ssh_keygen=True, has_gpg=True),
        )
        assert result.stage is Stage.EMAIL


# ---------------------------------------------------------------------------
# KeyPick → ...
# ---------------------------------------------------------------------------


class TestKeyPickEdges:
    def test_keypick_to_sshmethod_when_ssh_key(self):
        result = step(
            State(stage=Stage.KEY_PICK),
            KeyPicked(source=KeySource.EXISTING_SSH, key=existing_ssh()),
        )
        assert result.stage is Stage.SSH_METHOD

    def test_keypick_to_email_when_gpg_key(self):
        result = step(
            State(stage=Stage.KEY_PICK),
            KeyPicked(source=KeySource.EXISTING_GPG, key=existing_gpg()),
        )
        assert result.stage is Stage.EMAIL

    def test_keypick_to_working_when_managed_and_gh(self):
        result = step(
            State(stage=Stage.KEY_PICK),
            KeyPicked(source=KeySource.MANAGED),
            caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True),
        )
        assert result.stage is Stage.WORKING

    def test_keypick_to_publish_when_managed_manual(self):
        result = step(
            State(stage=Stage.KEY_PICK, identity=Identity(github_username="alice")),
            KeyPicked(source=KeySource.MANAGED),
            caps(has_ssh_keygen=True),
        )
        assert result.stage is Stage.PUBLISH

    def test_keypick_to_email_when_managed_gpg(self):
        result = step(
            State(stage=Stage.KEY_PICK),
            KeyPicked(source=KeySource.MANAGED),
            caps(has_gpg=True),
        )
        assert result.stage is Stage.EMAIL


# ---------------------------------------------------------------------------
# SshMethod → ...
# ---------------------------------------------------------------------------


class TestSshMethodEdges:
    def test_sshmethod_to_publish_when_gist_default(self):
        result = step(State(stage=Stage.SSH_METHOD), MethodPicked(method=SshMethod.GIST))
        assert result.stage is Stage.PUBLISH

    def test_sshmethod_to_ghadd_when_add_to_github(self):
        result = step(State(stage=Stage.SSH_METHOD), MethodPicked(method=SshMethod.GH_ADD))
        assert result.stage is Stage.GH_ADD


# ---------------------------------------------------------------------------
# GhAdd → ...
# ---------------------------------------------------------------------------


class TestGhAddEdges:
    def test_ghadd_to_done_when_verified(self):
        assert step(State(stage=Stage.GH_ADD), GhAddVerified()).stage is Stage.DONE

    def test_ghadd_to_trouble_when_failed(self):
        result = step(State(stage=Stage.GH_ADD), GhAddFailed())
        assert result.stage is Stage.TROUBLE
        assert isinstance(result.trouble, GistTimeout)


# ---------------------------------------------------------------------------
# Working → ...
# ---------------------------------------------------------------------------


class TestWorkingEdges:
    def test_working_to_done_when_verified(self):
        assert step(State(stage=Stage.WORKING), WorkingSucceeded()).stage is Stage.DONE

    def test_working_to_trouble_when_failed(self):
        result = step(State(stage=Stage.WORKING), WorkingFailed())
        assert result.stage is Stage.TROUBLE
        assert isinstance(result.trouble, GistTimeout)


# ---------------------------------------------------------------------------
# Publish → ...
# ---------------------------------------------------------------------------


class TestPublishEdges:
    def test_publish_to_done_when_gist_verified(self):
        assert step(State(stage=Stage.PUBLISH), GistVerified()).stage is Stage.DONE

    def test_publish_to_trouble_when_timeout(self):
        result = step(State(stage=Stage.PUBLISH), GistTimedOut())
        assert result.stage is Stage.TROUBLE
        assert isinstance(result.trouble, GistTimeout)


# ---------------------------------------------------------------------------
# Email → Inbox
# ---------------------------------------------------------------------------


class TestEmailEdges:
    def test_email_to_inbox_when_sent(self):
        assert step(State(stage=Stage.EMAIL), EmailSent()).stage is Stage.INBOX


# ---------------------------------------------------------------------------
# Inbox → ...
# ---------------------------------------------------------------------------


class TestInboxEdges:
    def test_inbox_to_done_when_verified(self):
        assert step(State(stage=Stage.INBOX), VerificationOk()).stage is Stage.DONE

    def test_inbox_to_trouble_when_timeout(self):
        result = step(State(stage=Stage.INBOX), VerificationTimedOut(error_code="key-not-found"))
        assert result.stage is Stage.TROUBLE
        assert isinstance(result.trouble, VerifyTimeout)
        assert result.trouble.error_code == "key-not-found"


# ---------------------------------------------------------------------------
# Retry → ...
# ---------------------------------------------------------------------------


class TestRetryEdges:
    def test_retry_to_done_when_retry_ok(self):
        result = step(State(stage=Stage.SAVED_RETRY), SavedConfigChecked(result="ok"))
        assert result.stage is Stage.DONE

    def test_retry_to_welcome_when_setup_again(self):
        assert step(State(stage=Stage.SAVED_RETRY), SavedRetryRestart()).stage is Stage.WELCOME


# ---------------------------------------------------------------------------
# Trouble → ...
# ---------------------------------------------------------------------------


TROUBLE_VARIANTS: tuple[Trouble, ...] = (GistTimeout(), VerifyTimeout(error_code="signature-failed"))


class TestTroubleEdges:
    @pytest.mark.parametrize("trouble", TROUBLE_VARIANTS)
    def test_trouble_to_publish_when_edit_username(self, trouble: Trouble):
        prev = State(stage=Stage.TROUBLE, trouble=trouble,
                     identity=Identity(github_username="old"))
        result = step(prev, TroubleEditUsername(new_username="new"))
        assert result.stage is Stage.PUBLISH
        assert result.identity.github_username == "new"
        assert result.trouble is None

    @pytest.mark.parametrize("trouble", TROUBLE_VARIANTS)
    def test_trouble_to_email_when_email_option(self, trouble: Trouble):
        prev = State(stage=Stage.TROUBLE, trouble=trouble)
        result = step(prev, TroubleChoseEmail())
        assert result.stage is Stage.EMAIL
        assert result.trouble is None

    @pytest.mark.parametrize("trouble", TROUBLE_VARIANTS)
    def test_trouble_to_welcome_when_restart(self, trouble: Trouble):
        prev = State(stage=Stage.TROUBLE, trouble=trouble)
        result = step(prev, TroubleRestart())
        assert result.stage is Stage.WELCOME
        assert result.trouble is None


# ---------------------------------------------------------------------------
# Diagram completeness — every documented edge above is enumerated below.
# Catches drift when somebody adds a Stage / Trouble variant without a test.
# ---------------------------------------------------------------------------


DIAGRAM_EDGES: tuple[tuple[str, str], ...] = (
    ("Saved", "Done"), ("Saved", "Retry"), ("Saved", "Welcome"),
    ("Saved", "Publish"), ("Saved", "Inbox"),

    ("Welcome", "Done"), ("Welcome", "KeyPick"), ("Welcome", "Working"),
    ("Welcome", "Publish"), ("Welcome", "Email"), ("Welcome", "UserForm"),
    ("Welcome", "Blocked"),

    ("UserForm", "Publish"), ("UserForm", "Email"),

    ("KeyPick", "SshMethod"), ("KeyPick", "Email"), ("KeyPick", "Working"),
    ("KeyPick", "Publish"),

    ("SshMethod", "Publish"), ("SshMethod", "GhAdd"),

    ("GhAdd", "Done"), ("GhAdd", "Trouble"),
    ("Working", "Done"), ("Working", "Trouble"),
    ("Publish", "Done"), ("Publish", "Trouble"),

    ("Email", "Inbox"),
    ("Inbox", "Done"), ("Inbox", "Trouble"),

    ("Retry", "Done"), ("Retry", "Welcome"),

    ("Trouble", "Publish"), ("Trouble", "Email"), ("Trouble", "Welcome"),
)


class TestDiagramCompleteness:
    def test_diagram_has_no_orphan_edges(self):
        sources = {src for src, _ in DIAGRAM_EDGES}
        dests = {dst for _, dst in DIAGRAM_EDGES}
        terminals = {"Done", "Blocked"}
        # Every non-terminal destination is also a source.
        non_terminals = dests - terminals
        assert non_terminals.issubset(sources), (
            f"non-terminal nodes with no outgoing edge: {non_terminals - sources}"
        )

    def test_every_stage_in_enum_appears_in_diagram(self):
        diagram_nodes = {src for src, _ in DIAGRAM_EDGES} | {dst for _, dst in DIAGRAM_EDGES}
        diagram_to_stage = {
            "Saved": Stage.INITIAL, "Retry": Stage.SAVED_RETRY,
            "Welcome": Stage.WELCOME, "UserForm": Stage.USER_FORM,
            "KeyPick": Stage.KEY_PICK, "SshMethod": Stage.SSH_METHOD,
            "Working": Stage.WORKING, "Publish": Stage.PUBLISH,
            "GhAdd": Stage.GH_ADD, "Email": Stage.EMAIL,
            "Inbox": Stage.INBOX, "Trouble": Stage.TROUBLE,
            "Blocked": Stage.BLOCKED, "Done": Stage.DONE,
        }
        assert diagram_nodes == set(diagram_to_stage), (
            f"diagram <-> Stage mismatch: {diagram_nodes ^ set(diagram_to_stage)}"
        )
        assert set(diagram_to_stage.values()) == set(Stage), (
            f"Stage enum drift: {set(Stage) ^ set(diagram_to_stage.values())}"
        )

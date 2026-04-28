"""
Cross-cutting plan-validation grep, parametrized over every screen.

Per the integrated UX plan's Validation section, no user-facing copy on any
onboarding screen may contain these terms — they're symptoms of UX leaks
(GitHub CLI exposure, manual retry buttons, debug rows, stale flow names,
or copy from earlier iterations that the plan has rejected).

The screens themselves haven't been implemented yet — these tests will fail
with NotImplementedError until each screen's render() is written. Once
rendered, the assertions enforce that the forbidden terms never appear.
"""
from __future__ import annotations

import pytest

from cc_sentiment.onboarding import (
    Capabilities,
    GistTimeout,
    Stage,
    State as GlobalState,
    VerifyTimeout,
)
from cc_sentiment.onboarding.state import (
    ExistingKey,
    ExistingKeys,
    Identity,
    KeySource,
    SelectedKey,
)
from cc_sentiment.onboarding.ui import Screen
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

from .conftest import fake_caps, has_text, mounted


FORBIDDEN_TERMS: tuple[str, ...] = (
    "GitHub CLI",
    "gh CLI",
    "Use GPG",
    "Check now",
    "Elapsed",
    "Checked:",
    "Verification: waiting",
    "Reopen verification",
)


def _ssh_key() -> ExistingKey:
    return ExistingKey(fingerprint="SHA256:test", label="id_ed25519")


def _gpg_key() -> ExistingKey:
    return ExistingKey(fingerprint="DEADBEEFCAFE0001", label="alice@example.com")


# (screen_cls, gs, caps) — one representative state per screen. Forbidden
# terms must not appear regardless of the path-dependent variant.
SCREEN_CASES: tuple[tuple[type[Screen], GlobalState, Capabilities], ...] = (
    (InitialScreen, GlobalState(stage=Stage.INITIAL), fake_caps()),
    (SavedRetryScreen, GlobalState(stage=Stage.SAVED_RETRY, has_saved_config=True), fake_caps()),
    (WelcomeScreen, GlobalState(stage=Stage.WELCOME), fake_caps()),
    (WelcomeScreen, GlobalState(stage=Stage.WELCOME, has_saved_config=True), fake_caps()),
    (UserFormScreen, GlobalState(stage=Stage.USER_FORM), fake_caps()),
    (
        KeyPickScreen,
        GlobalState(stage=Stage.KEY_PICK, existing_keys=ExistingKeys(ssh=(_ssh_key(),))),
        fake_caps(has_ssh_keygen=True),
    ),
    (
        SshMethodScreen,
        GlobalState(
            stage=Stage.SSH_METHOD,
            identity=Identity(github_username="alice"),
            selected=SelectedKey(source=KeySource.EXISTING_SSH, key=_ssh_key()),
        ),
        fake_caps(gh_authenticated=True),
    ),
    (
        SshMethodScreen,
        GlobalState(
            stage=Stage.SSH_METHOD,
            identity=Identity(github_username="alice"),
            selected=SelectedKey(source=KeySource.EXISTING_SSH, key=_ssh_key()),
        ),
        fake_caps(gh_authenticated=False),
    ),
    (WorkingScreen, GlobalState(stage=Stage.WORKING), fake_caps()),
    (
        PublishScreen,
        GlobalState(
            stage=Stage.PUBLISH,
            identity=Identity(github_username="alice"),
            selected=SelectedKey(source=KeySource.MANAGED, key=_ssh_key()),
        ),
        fake_caps(has_gpg=True),
    ),
    (
        GhAddScreen,
        GlobalState(
            stage=Stage.GH_ADD,
            identity=Identity(github_username="alice"),
            selected=SelectedKey(source=KeySource.EXISTING_SSH, key=_ssh_key()),
        ),
        fake_caps(gh_authenticated=True),
    ),
    (
        GhAddScreen,
        GlobalState(
            stage=Stage.GH_ADD,
            identity=Identity(github_username="alice"),
            selected=SelectedKey(source=KeySource.EXISTING_SSH, key=_ssh_key()),
        ),
        fake_caps(gh_authenticated=False),
    ),
    (
        EmailScreen,
        GlobalState(stage=Stage.EMAIL, identity=Identity(email="alice@example.com", email_usable=True)),
        fake_caps(has_gpg=True),
    ),
    (InboxScreen, GlobalState(stage=Stage.INBOX, identity=Identity(email="alice@example.com")), fake_caps()),
    (
        GistTroubleScreen,
        GlobalState(
            stage=Stage.TROUBLE,
            trouble=GistTimeout(),
            identity=Identity(github_username="alice"),
        ),
        fake_caps(has_gpg=True),
    ),
    (
        VerifyTroubleScreen,
        GlobalState(stage=Stage.TROUBLE, trouble=VerifyTimeout(error_code="key-not-found")),
        fake_caps(),
    ),
    (BlockedScreen, GlobalState(stage=Stage.BLOCKED), fake_caps(has_brew=True)),
    (
        DoneScreen,
        GlobalState(
            stage=Stage.DONE,
            identity=Identity(github_username="alice"),
            selected=SelectedKey(source=KeySource.MANAGED, key=_ssh_key()),
        ),
        fake_caps(),
    ),
    (
        DoneScreen,
        GlobalState(
            stage=Stage.DONE,
            identity=Identity(email="alice@example.com", email_usable=True),
            selected=SelectedKey(source=KeySource.EXISTING_GPG, key=_gpg_key()),
        ),
        fake_caps(),
    ),
)


@pytest.mark.parametrize(("screen_cls", "gs", "caps"), SCREEN_CASES)
@pytest.mark.parametrize("term", FORBIDDEN_TERMS)
async def test_forbidden_term_absent(
    screen_cls: type[Screen],
    gs: GlobalState,
    caps: Capabilities,
    term: str,
) -> None:
    async with mounted(screen_cls, gs, caps) as pilot:
        assert not has_text(pilot, term), (
            f"{screen_cls.__name__} contains forbidden plan term {term!r}"
        )

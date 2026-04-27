from __future__ import annotations

import re
import shutil
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import httpx

from cc_sentiment.signing import KeyDiscovery
from cc_sentiment.signing.discovery import GIST_DESCRIPTION
from cc_sentiment.tui.setup_state import (
    DiscoverRow,
    DiscoverRowState,
    DiscoveryResult,
    EmailSource,
    ExistingGPGKey,
    ExistingSSHKey,
    GenerateGPGKey,
    GenerateSSHKey,
    IdentityDiscovery,
    KeyKind,
    PublishMethod,
    RouteId,
    SetupRoute,
    ToolCapabilities,
    UsernameSource,
)

GIST_NEW_URL = "https://gist.github.com/"
GITHUB_SSH_NEW_URL = "https://github.com/settings/ssh/new"
GITHUB_GPG_NEW_URL = "https://github.com/settings/gpg/new"
OPENPGP_UPLOAD_URL = "https://keys.openpgp.org/upload"
ISSUES_URL = "https://github.com/yasyf/cc-sentiment/issues"
GITHUB_API_USERS_URL = "https://api.github.com/users"

ACCOUNT_SSH_WARNING = (
    "This changes your GitHub account keys. Use gist unless you specifically "
    "want this key listed in GitHub settings."
)
ACCOUNT_GPG_WARNING = (
    "This changes your GitHub account keys. Use keys.openpgp.org unless you specifically "
    "want this key listed in GitHub settings."
)


class Clipboard:
    @classmethod
    def command(cls) -> tuple[list[str], dict[str, str]] | None:
        match sys.platform:
            case "darwin":
                if shutil.which("pbcopy"):
                    return ["pbcopy"], {}
            case "win32":
                if shutil.which("clip"):
                    return ["clip"], {}
            case _:
                if shutil.which("wl-copy"):
                    return ["wl-copy"], {}
                if shutil.which("xclip"):
                    return ["xclip", "-selection", "clipboard"], {}
                if shutil.which("xsel"):
                    return ["xsel", "--clipboard", "--input"], {}
        return None

    @classmethod
    def copy(cls, text: str) -> bool:
        if (cmd := cls.command()) is None:
            return False
        argv, env = cmd
        try:
            subprocess.run(argv, input=text, text=True, check=True, timeout=5, env=env or None)
        except (subprocess.SubprocessError, OSError):
            return False
        return True

    @classmethod
    def available(cls) -> bool:
        return cls.command() is not None


class Browser:
    @staticmethod
    def open(url: str) -> bool:
        try:
            return bool(webbrowser.open(url))
        except (webbrowser.Error, OSError):
            return False


class Sanitizer:
    HOME_PATTERN = re.compile(re.escape(str(Path.home())))
    TOKEN_PATTERN = re.compile(r"(gho_|ghp_|ghu_|ghs_)[A-Za-z0-9]{20,}")

    @classmethod
    def error(cls, text: str, max_len: int = 240) -> str:
        cleaned = cls.TOKEN_PATTERN.sub("[redacted]", text or "")
        cleaned = cls.HOME_PATTERN.sub("~", cleaned)
        cleaned = cleaned.strip()
        return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


class IssueUrl:
    @staticmethod
    def build(route: str, location: str, error: str) -> str:
        body = (
            "## What I tried\n"
            f"- Route: `{route}`\n"
            f"- Public location: `{location}`\n\n"
            "## What happened\n"
            f"```\n{Sanitizer.error(error)}\n```\n\n"
            "## Context\n"
            "- cc-sentiment client\n"
        )
        return f"{ISSUES_URL}/new?{urlencode({'title': 'cc-sentiment setup verification failure', 'body': body})}"


class CapabilityProbe:
    @staticmethod
    def detect() -> ToolCapabilities:
        has_gh = KeyDiscovery.has_tool("gh")
        return ToolCapabilities(
            has_gh=has_gh,
            gh_authed=has_gh and KeyDiscovery.gh_authenticated(),
            has_gpg=KeyDiscovery.has_tool("gpg"),
            has_ssh_keygen=KeyDiscovery.has_tool("ssh-keygen"),
            has_brew=KeyDiscovery.has_brew(),
            can_clipboard=Clipboard.available(),
            can_open_browser=True,
        )


class IdentityProbe:
    @staticmethod
    def detect(saved_username: str = "") -> IdentityDiscovery:
        if username := KeyDiscovery.gh_login():
            email = KeyDiscovery.gh_primary_email() or ""
            return IdentityDiscovery(
                github_username=username,
                username_source=UsernameSource.GH,
                github_email=email,
                email_source=EmailSource.GH if email else EmailSource.NONE,
                email_usable=bool(email) and not KeyDiscovery.is_noreply_email(email),
            )
        if saved_username:
            return IdentityDiscovery(
                github_username=saved_username,
                username_source=UsernameSource.SAVED,
            )
        return IdentityDiscovery()

    @staticmethod
    def mine_email(username: str) -> tuple[str, EmailSource, bool]:
        try:
            repo = KeyDiscovery.fetch_latest_public_repo(username)
        except httpx.HTTPError:
            return "", EmailSource.NONE, False
        if not repo:
            return "", EmailSource.NONE, False
        try:
            email = KeyDiscovery.fetch_commit_email(username, repo)
        except httpx.HTTPError:
            return "", EmailSource.NONE, False
        if not email:
            return "", EmailSource.NONE, False
        usable = not KeyDiscovery.is_noreply_email(email)
        return email, EmailSource.COMMIT, usable

    @staticmethod
    def validate_username(username: str) -> str:
        try:
            response = httpx.get(f"{GITHUB_API_USERS_URL}/{username}", timeout=10.0)
        except httpx.HTTPError:
            return "unreachable"
        if response.status_code == 200:
            return "ok"
        if response.status_code == 404:
            return "not-found"
        return "unreachable"


class LocalKeysProbe:
    @staticmethod
    def detect_ssh() -> tuple[ExistingSSHKey, ...]:
        keys: list[ExistingSSHKey] = []
        for ssh in KeyDiscovery.find_ssh_keys():
            managed = ssh.path.parent.name == "keys" and ssh.path.parent.parent.name == ".cc-sentiment"
            keys.append(ExistingSSHKey(info=ssh, managed=managed))
        managed_path = KeyDiscovery.find_gist_keypair()
        if managed_path is not None and not any(k.info.path == managed_path for k in keys):
            parts = managed_path.with_suffix(managed_path.suffix + ".pub").read_text().strip().split()
            from cc_sentiment.signing import SSHKeyInfo

            keys.append(ExistingSSHKey(
                info=SSHKeyInfo(
                    path=managed_path,
                    algorithm=parts[0] if len(parts) >= 2 else "unknown",
                    comment=parts[2] if len(parts) >= 3 else "cc-sentiment",
                ),
                managed=True,
            ))
        return tuple(keys)

    @staticmethod
    def detect_gpg() -> tuple[ExistingGPGKey, ...]:
        return tuple(ExistingGPGKey(info=info) for info in KeyDiscovery.find_gpg_keys())


class SetupRoutePlanner:
    @classmethod
    def plan(
        cls,
        capabilities: ToolCapabilities,
        identity: IdentityDiscovery,
        ssh_keys: tuple[ExistingSSHKey, ...],
        gpg_keys: tuple[ExistingGPGKey, ...],
    ) -> tuple[SetupRoute | None, tuple[SetupRoute, ...]]:
        candidates: list[SetupRoute] = []
        username = identity.github_username

        if capabilities.gh_authed and capabilities.has_ssh_keygen:
            candidates.append(cls._managed_ssh_gist())
        elif capabilities.has_gpg and gpg_keys:
            candidates.append(cls._existing_gpg_openpgp(gpg_keys[0]))
        elif capabilities.has_gpg:
            candidates.append(cls._managed_gpg_openpgp())
        elif capabilities.has_ssh_keygen:
            candidates.append(cls._managed_ssh_manual_gist())
        elif ssh_keys:
            candidates.append(cls._existing_ssh_manual_gist(ssh_keys[0]))

        for ssh in ssh_keys:
            if capabilities.gh_authed and username:
                candidates.append(cls._existing_ssh_gist(ssh))
            elif username:
                candidates.append(cls._existing_ssh_manual_gist(ssh))
            if username:
                candidates.append(cls._existing_ssh_github(ssh))

        for gpg in gpg_keys:
            if capabilities.gh_authed and username:
                candidates.append(cls._existing_gpg_gist(gpg))
            candidates.append(cls._existing_gpg_openpgp(gpg))
            if capabilities.has_gpg and not gpg.managed:
                candidates.append(cls._managed_gpg_openpgp())
            if username:
                candidates.append(cls._existing_gpg_github(gpg))

        seen: set[tuple[str, str, str]] = set()
        unique: list[SetupRoute] = []
        for route in candidates:
            key = cls._route_key(route)
            if key in seen:
                continue
            seen.add(key)
            unique.append(route)

        if not unique:
            if capabilities.has_gh and not capabilities.gh_authed:
                unique.append(cls._sign_in_gh())
            else:
                unique.append(cls._install_tools(capabilities))
            return unique[0], ()

        recommended = unique[0]
        alternatives = tuple(unique[1:])
        if (
            recommended.route_id is RouteId.EXISTING_GPG_OPENPGP
            and not capabilities.gh_authed
            and not capabilities.has_gh
        ):
            return recommended, ()
        return recommended, alternatives

    @staticmethod
    def _route_key(route: SetupRoute) -> tuple[str, str, str]:
        match route.key_plan:
            case ExistingSSHKey(info=info):
                return route.route_id.value, route.publish_method.value if route.publish_method else "", str(info.path)
            case ExistingGPGKey(info=info):
                return route.route_id.value, route.publish_method.value if route.publish_method else "", info.fpr
            case GenerateSSHKey():
                return route.route_id.value, route.publish_method.value if route.publish_method else "", "managed-ssh"
            case GenerateGPGKey():
                return route.route_id.value, route.publish_method.value if route.publish_method else "", "managed-gpg"
            case _:
                return route.route_id.value, "", ""

    @staticmethod
    def _managed_ssh_gist() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_SSH_GIST,
            title="Create a cc-sentiment key and public gist",
            detail=(
                "Fastest option. We'll create a key used only by cc-sentiment, "
                "then create a public GitHub gist containing the public key and a short cc-sentiment note."
            ),
            primary_label="Create key and public gist",
            secondary_label="Use a different key",
            publish_method=PublishMethod.GIST_AUTO,
            key_kind=KeyKind.SSH,
            key_plan=GenerateSSHKey(),
            safety_note="This does not add an SSH login key to your GitHub account.",
            automated=True,
        )

    @staticmethod
    def _managed_gpg_openpgp() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_GPG_OPENPGP,
            title="Create a cc-sentiment GPG key and verify by email",
            detail=(
                "Best option without GitHub CLI. We'll create a GPG key for this app, "
                "send the public key to keys.openpgp.org, and ask them to email you a verification link."
            ),
            primary_label="Create key and send email",
            secondary_label="Use GitHub instead",
            publish_method=PublishMethod.OPENPGP,
            key_kind=KeyKind.GPG,
            key_plan=GenerateGPGKey(),
            needs_email=True,
            automated=True,
        )

    @staticmethod
    def _managed_ssh_manual_gist() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_SSH_MANUAL_GIST,
            title="Create a cc-sentiment key and public gist",
            detail=(
                "We'll create a key used only by cc-sentiment, open GitHub's gist page, "
                "copy the public key for you, and guide you through creating the gist."
            ),
            primary_label="Create key and open GitHub",
            secondary_label="Install GitHub CLI for automatic setup",
            publish_method=PublishMethod.GIST_MANUAL,
            key_kind=KeyKind.SSH,
            key_plan=GenerateSSHKey(),
            automated=False,
        )

    @staticmethod
    def _existing_ssh_gist(ssh: ExistingSSHKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_SSH_GIST,
            title="Publish this SSH public key in a gist",
            detail=(
                "Recommended because it does not add this key to your GitHub account. "
                "sentiments.cc only reads the public key from the gist."
            ),
            primary_label="Create gist with this key",
            secondary_label="Add SSH key to GitHub account",
            publish_method=PublishMethod.GIST_AUTO,
            key_kind=KeyKind.SSH,
            key_plan=ExistingSSHKey(info=ssh.info, managed=ssh.managed),
            safety_note="The private key never leaves this device.",
            automated=True,
        )

    @staticmethod
    def _existing_ssh_manual_gist(ssh: ExistingSSHKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_SSH_MANUAL_GIST,
            title="Publish this SSH public key in a gist",
            detail=(
                "Recommended because it does not add this key to your GitHub account. "
                "sentiments.cc only reads the public key from the gist."
            ),
            primary_label="Open GitHub gist guide",
            secondary_label="Add SSH key to GitHub account",
            publish_method=PublishMethod.GIST_MANUAL,
            key_kind=KeyKind.SSH,
            key_plan=ExistingSSHKey(info=ssh.info, managed=ssh.managed),
            safety_note="The private key never leaves this device.",
            automated=False,
        )

    @staticmethod
    def _existing_ssh_github(ssh: ExistingSSHKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_SSH_GITHUB,
            title="Add this SSH key to your GitHub account",
            detail=(
                "Adds the public SSH key to your GitHub account so sentiments.cc can find it. "
                "Use gist unless you want this key listed under GitHub settings."
            ),
            primary_label="Add SSH key to GitHub",
            secondary_label="Use a public gist instead",
            publish_method=PublishMethod.GITHUB_SSH,
            key_kind=KeyKind.SSH,
            key_plan=ExistingSSHKey(info=ssh.info, managed=ssh.managed),
            account_key_warning=ACCOUNT_SSH_WARNING,
            automated=True,
        )

    @staticmethod
    def _existing_gpg_gist(gpg: ExistingGPGKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_GPG_GIST,
            title="Publish this public GPG key in a gist",
            detail=(
                "We'll publish this public GPG key in a gist so sentiments.cc can verify signatures. "
                "This does not change your GitHub account keys."
            ),
            primary_label="Create gist with this key",
            secondary_label="Verify with keys.openpgp.org",
            publish_method=PublishMethod.GIST_AUTO,
            key_kind=KeyKind.GPG,
            key_plan=ExistingGPGKey(info=gpg.info, managed=gpg.managed),
            automated=True,
        )

    @staticmethod
    def _existing_gpg_openpgp(gpg: ExistingGPGKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_GPG_OPENPGP,
            title="Publish this GPG public key with keys.openpgp.org",
            detail="keys.openpgp.org will email you before making the public key searchable.",
            primary_label="Send verification email",
            secondary_label="Choose another key",
            publish_method=PublishMethod.OPENPGP,
            key_kind=KeyKind.GPG,
            key_plan=ExistingGPGKey(info=gpg.info, managed=gpg.managed),
            needs_email=True,
            automated=True,
        )

    @staticmethod
    def _existing_gpg_github(gpg: ExistingGPGKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_GPG_GITHUB,
            title="Add this GPG key to your GitHub account",
            detail="Uses the existing GPG key as your GitHub-published verification key.",
            primary_label="Add GPG key to GitHub",
            secondary_label="Use keys.openpgp.org instead",
            publish_method=PublishMethod.GITHUB_GPG,
            key_kind=KeyKind.GPG,
            key_plan=ExistingGPGKey(info=gpg.info, managed=gpg.managed),
            account_key_warning=ACCOUNT_GPG_WARNING,
            automated=True,
        )

    @staticmethod
    def _sign_in_gh() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.SIGN_IN_GH,
            title="Sign in to GitHub CLI",
            detail=(
                "We'll run gh auth login. After you finish, cc-sentiment can create the gist automatically."
            ),
            primary_label="Sign in to GitHub CLI",
            secondary_label="Continue without GitHub CLI",
            publish_method=None,
            key_kind=None,
            automated=False,
        )

    @staticmethod
    def _install_tools(capabilities: ToolCapabilities) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.INSTALL_TOOLS,
            title="Install GitHub CLI or GPG",
            detail="cc-sentiment can do most of setup for you if GitHub CLI or GPG is installed.",
            primary_label=(
                "Install GitHub CLI with Homebrew"
                if capabilities.has_brew
                else "I installed one"
            ),
            secondary_label=(
                "Install GPG with Homebrew"
                if capabilities.has_brew
                else "Manual setup"
            ),
            publish_method=None,
            key_kind=None,
            automated=False,
        )


class GistDiscovery:
    GIST_ID_PATTERN = re.compile(r"[0-9a-f]{20,}")

    @classmethod
    def parse_gist_id(cls, value: str) -> str | None:
        candidate = value.strip().rsplit("/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
        return candidate if cls.GIST_ID_PATTERN.fullmatch(candidate) else None

    @staticmethod
    def find_cc_sentiment_gist_id(username: str) -> str | None:
        try:
            response = httpx.get(
                f"https://api.github.com/users/{username}/gists",
                params={"per_page": "100"},
                timeout=10.0,
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return next(
            (
                str(gist["id"])
                for gist in response.json()
                if gist.get("description") == GIST_DESCRIPTION and gist.get("public")
            ),
            None,
        )

    @staticmethod
    def fetch_gist_description(gist_id: str) -> str | None:
        try:
            response = httpx.get(
                f"https://api.github.com/gists/{gist_id}",
                timeout=10.0,
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        return response.json().get("description") or ""


class DiscoveryRunner:
    @classmethod
    def run(cls, saved_username: str = "") -> DiscoveryResult:
        capabilities = CapabilityProbe.detect()
        identity = IdentityProbe.detect(saved_username=saved_username)
        if identity.github_username and not identity.email_usable:
            email, source, usable = IdentityProbe.mine_email(identity.github_username)
            if email:
                identity = IdentityDiscovery(
                    github_username=identity.github_username,
                    username_source=identity.username_source,
                    github_email=email,
                    email_source=source,
                    email_usable=usable,
                )
        ssh_keys = LocalKeysProbe.detect_ssh()
        gpg_keys = LocalKeysProbe.detect_gpg()
        recommended, alternatives = SetupRoutePlanner.plan(
            capabilities, identity, ssh_keys, gpg_keys,
        )
        rows = cls._build_rows(capabilities, identity, ssh_keys, gpg_keys)
        return DiscoveryResult(
            capabilities=capabilities,
            identity=identity,
            existing_ssh=ssh_keys,
            existing_gpg=gpg_keys,
            rows=rows,
            recommended=recommended,
            alternatives=alternatives,
        )

    @staticmethod
    def _build_rows(
        capabilities: ToolCapabilities,
        identity: IdentityDiscovery,
        ssh_keys: tuple[ExistingSSHKey, ...],
        gpg_keys: tuple[ExistingGPGKey, ...],
    ) -> tuple[DiscoverRow, ...]:
        gh_state = (
            DiscoverRowState.OK if capabilities.gh_authed
            else DiscoverRowState.WARNING if capabilities.has_gh
            else DiscoverRowState.SKIPPED
        )
        gh_detail = (
            f"GitHub CLI signed in as @{identity.github_username}."
            if capabilities.gh_authed and identity.github_username
            else "GitHub CLI installed but not signed in."
            if capabilities.has_gh
            else "GitHub CLI not installed."
        )
        username_state = (
            DiscoverRowState.OK if identity.github_username
            else DiscoverRowState.SKIPPED
        )
        email_state = (
            DiscoverRowState.OK if identity.email_usable
            else DiscoverRowState.SKIPPED
        )
        local_state = (
            DiscoverRowState.OK if ssh_keys or gpg_keys
            else DiscoverRowState.SKIPPED
        )
        local_detail = (
            f"{len(ssh_keys)} SSH, {len(gpg_keys)} GPG"
            if ssh_keys or gpg_keys else "None found yet."
        )
        return (
            DiscoverRow("Checking GitHub CLI…", gh_state, gh_detail),
            DiscoverRow(
                "Looking for a GitHub username…",
                username_state,
                identity.github_username or "Not found.",
            ),
            DiscoverRow(
                "Looking for an email only if keyserver verification is needed…",
                email_state,
                identity.github_email if identity.email_usable else "Not needed yet.",
            ),
            DiscoverRow("Finding local SSH and GPG keys…", local_state, local_detail),
            DiscoverRow(
                "Checking public key locations…",
                DiscoverRowState.WAITING,
                "",
            ),
            DiscoverRow(
                "Testing verification with sentiments.cc…",
                DiscoverRowState.WAITING,
                "",
            ),
        )

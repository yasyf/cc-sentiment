from __future__ import annotations

import re
import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlparse

import httpx

from cc_sentiment.signing import KeyDiscovery
from cc_sentiment.signing.discovery import GIST_DESCRIPTION, GIST_PUB_FILENAME
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
    SetupIntervention,
    SetupPlan,
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

ACCOUNT_SSH_WARNING = "Adds this key to your GitHub account settings."
ACCOUNT_GPG_WARNING = "Adds this key to your GitHub account settings."


class Clipboard:
    @classmethod
    def command(cls) -> list[str] | None:
        match sys.platform:
            case "darwin":
                if shutil.which("pbcopy"):
                    return ["pbcopy"]
            case "win32":
                if shutil.which("clip"):
                    return ["clip"]
            case _:
                if shutil.which("wl-copy"):
                    return ["wl-copy"]
                if shutil.which("xclip"):
                    return ["xclip", "-selection", "clipboard"]
                if shutil.which("xsel"):
                    return ["xsel", "--clipboard", "--input"]
        return None

    @classmethod
    def copy(cls, text: str) -> bool:
        if (cmd := cls.command()) is None:
            return False
        try:
            subprocess.run(cmd, input=text, text=True, check=True, timeout=5)
        except (subprocess.SubprocessError, OSError):
            return False
        return True

    @classmethod
    def available(cls) -> bool:
        return cls.command() is not None


class Browser:
    @staticmethod
    def available() -> bool:
        try:
            return webbrowser.get() is not None
        except webbrowser.Error:
            return False

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
            can_open_browser=Browser.available(),
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
        keys = tuple(
            ExistingSSHKey(
                info=ssh,
                managed=ssh.path.parent.name == "keys" and ssh.path.parent.parent.name == ".cc-sentiment",
            )
            for ssh in KeyDiscovery.find_ssh_keys()
        )
        return keys + (
            (LocalKeysProbe._managed_ssh_key(managed_path),)
            if (managed_path := KeyDiscovery.find_gist_keypair()) is not None
            and not any(key.info.path == managed_path for key in keys)
            else ()
        )

    @staticmethod
    def _managed_ssh_key(path: Path) -> ExistingSSHKey:
        info = KeyDiscovery.ssh_key_info(path, "cc-sentiment")
        assert info is not None
        return ExistingSSHKey(info=info, managed=True)

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
        github_lookup_allowed: bool = True,
    ) -> SetupPlan:
        username = identity.github_username
        gh_publish_allowed = github_lookup_allowed and capabilities.gh_authed
        if gh_publish_allowed and capabilities.has_ssh_keygen:
            recommended = cls._managed_ssh_gist()
        elif gh_publish_allowed and gpg_keys:
            recommended = cls._existing_gpg_gist(gpg_keys[0])
        elif capabilities.has_gpg:
            recommended = cls._managed_gpg_openpgp()
        elif github_lookup_allowed and capabilities.has_gh and not capabilities.gh_authed:
            return SetupPlan(intervention=SetupIntervention.SIGN_IN_GH)
        elif github_lookup_allowed and not username and capabilities.has_ssh_keygen:
            return SetupPlan(intervention=SetupIntervention.USERNAME)
        elif github_lookup_allowed and username and capabilities.has_ssh_keygen:
            recommended = cls._managed_ssh_manual_gist()
        else:
            return SetupPlan(intervention=SetupIntervention.INSTALL_TOOLS)

        unique = cls._unique_routes([
            recommended,
            *(
                cls._existing_ssh_gist(ssh)
                for ssh in ssh_keys
                if gh_publish_allowed and capabilities.has_ssh_keygen
            ),
            *(
                cls._existing_gpg_gist(gpg)
                for gpg in gpg_keys
                if gh_publish_allowed
            ),
            *(
                cls._existing_gpg_openpgp(gpg)
                for gpg in gpg_keys
                if capabilities.has_gpg
            ),
            *(
                cls._existing_ssh_manual_gist(ssh)
                for ssh in ssh_keys
                if github_lookup_allowed and username and capabilities.has_ssh_keygen
            ),
            *(
                cls._existing_ssh_github(ssh)
                for ssh in ssh_keys
                if gh_publish_allowed and capabilities.has_ssh_keygen
            ),
            *(
                cls._existing_gpg_github(gpg)
                for gpg in gpg_keys
                if gh_publish_allowed
            ),
        ])
        if not unique:
            return SetupPlan(intervention=SetupIntervention.INSTALL_TOOLS)
        return SetupPlan(unique[0], tuple(unique[1:]))

    @classmethod
    def _unique_routes(cls, candidates: list[SetupRoute]) -> list[SetupRoute]:
        return [
            route
            for index, route in enumerate(candidates)
            if cls._route_key(route) not in {
                cls._route_key(previous) for previous in candidates[:index]
            }
        ]

    @staticmethod
    def _route_key(route: SetupRoute) -> tuple[str, str, str]:
        publish_method = route.publish_method.value if route.publish_method else ""
        match route.key_plan:
            case ExistingSSHKey(info=info):
                return route.route_id.value, publish_method, str(info.path)
            case ExistingGPGKey(info=info):
                return route.route_id.value, publish_method, info.fpr
            case GenerateSSHKey():
                return route.route_id.value, publish_method, "managed-ssh"
            case GenerateGPGKey():
                return route.route_id.value, publish_method, "managed-gpg"
            case _:
                return route.route_id.value, "", ""

    @staticmethod
    def _managed_ssh_gist() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_SSH_GIST,
            title="Verify with a new cc-sentiment key",
            detail="Creates a dedicated key and publishes the public part in a gist.",
            primary_label="Create key",
            secondary_label="Choose another method",
            publish_method=PublishMethod.GIST_AUTO,
            key_kind=KeyKind.SSH,
            key_plan=GenerateSSHKey(),
            safety_note="Doesn't add a login key to your GitHub account.",
            automated=True,
        )

    @staticmethod
    def _managed_gpg_openpgp() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_GPG_OPENPGP,
            title="Verify by email",
            detail="Creates a dedicated GPG key and sends a one-time verification email.",
            primary_label="Create key",
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
            title="Verify with a new cc-sentiment key",
            detail="Creates a dedicated key, then guides you through publishing the public part.",
            primary_label="Create key",
            secondary_label="Install GitHub CLI instead",
            publish_method=PublishMethod.GIST_MANUAL,
            key_kind=KeyKind.SSH,
            key_plan=GenerateSSHKey(),
            automated=False,
        )

    @staticmethod
    def _existing_ssh_gist(ssh: ExistingSSHKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_SSH_GIST,
            title="Verify with this SSH key",
            detail="Publishes the public key in a gist without changing GitHub account keys.",
            primary_label="Create gist",
            secondary_label="Add to GitHub instead",
            publish_method=PublishMethod.GIST_AUTO,
            key_kind=KeyKind.SSH,
            key_plan=ExistingSSHKey(info=ssh.info, managed=ssh.managed),
            automated=True,
        )

    @staticmethod
    def _existing_ssh_manual_gist(ssh: ExistingSSHKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_SSH_MANUAL_GIST,
            title="Verify with this SSH key",
            detail="Publishes the public key in a gist without changing GitHub account keys.",
            primary_label="Create gist",
            secondary_label="Add to GitHub instead",
            publish_method=PublishMethod.GIST_MANUAL,
            key_kind=KeyKind.SSH,
            key_plan=ExistingSSHKey(info=ssh.info, managed=ssh.managed),
            automated=False,
        )

    @staticmethod
    def _existing_ssh_github(ssh: ExistingSSHKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_SSH_GITHUB,
            title="Verify with GitHub SSH",
            detail="Adds this public SSH key to your GitHub account.",
            primary_label="Add SSH key",
            secondary_label="Use gist instead",
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
            title="Verify with this GPG key",
            detail="Publishes the public key in a gist without changing GitHub account keys.",
            primary_label="Create gist",
            secondary_label="Verify by email",
            publish_method=PublishMethod.GIST_AUTO,
            key_kind=KeyKind.GPG,
            key_plan=ExistingGPGKey(info=gpg.info, managed=gpg.managed),
            automated=True,
        )

    @staticmethod
    def _existing_gpg_openpgp(gpg: ExistingGPGKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_GPG_OPENPGP,
            title="Verify this GPG key by email",
            detail="Sends a one-time email before publishing the public key.",
            primary_label="Send email",
            secondary_label="Choose another key",
            publish_method=PublishMethod.OPENPGP,
            key_kind=KeyKind.GPG,
            key_plan=ExistingGPGKey(info=gpg.info, managed=gpg.managed),
            needs_email=not bool(gpg.info.email),
            automated=True,
        )

    @staticmethod
    def _existing_gpg_github(gpg: ExistingGPGKey) -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.EXISTING_GPG_GITHUB,
            title="Verify with GitHub GPG",
            detail="Adds this public GPG key to your GitHub account.",
            primary_label="Add GPG key",
            secondary_label="Use email instead",
            publish_method=PublishMethod.GITHUB_GPG,
            key_kind=KeyKind.GPG,
            key_plan=ExistingGPGKey(info=gpg.info, managed=gpg.managed),
            account_key_warning=ACCOUNT_GPG_WARNING,
            automated=True,
        )

@dataclass(frozen=True, slots=True)
class GistRef:
    owner: str
    gist_id: str


@dataclass(frozen=True, slots=True)
class GistMetadata:
    ref: GistRef
    description: str
    public_key: str


class GistDiscovery:
    GIST_ID_PATTERN = re.compile(r"[0-9a-f]{20,}")

    @classmethod
    def parse_ref(cls, value: str, fallback_owner: str = "") -> GistRef | None:
        raw = value.strip()
        parsed = urlparse(raw)
        if parsed.netloc == "gist.github.com":
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and cls.GIST_ID_PATTERN.fullmatch(parts[1]):
                return GistRef(owner=parts[0], gist_id=parts[1])
            return None
        candidate = raw.rsplit("/", 1)[-1].split("?", 1)[0].split("#", 1)[0]
        if fallback_owner and cls.GIST_ID_PATTERN.fullmatch(candidate):
            return GistRef(owner=fallback_owner, gist_id=candidate)
        return None

    @staticmethod
    def find_cc_sentiment_gists(username: str) -> tuple[GistRef, ...]:
        response = httpx.get(
            f"https://api.github.com/users/{username}/gists",
            params={"per_page": "100"},
            timeout=10.0,
        )
        if response.status_code != 200:
            return ()
        return tuple(
            GistRef(owner=username, gist_id=str(gist["id"]))
            for gist in sorted(
                response.json(),
                key=lambda gist: gist.get("updated_at") or gist.get("created_at") or "",
                reverse=True,
            )
            if gist.get("description") == GIST_DESCRIPTION and gist.get("public")
        )

    @staticmethod
    def fetch_metadata(ref: GistRef) -> GistMetadata | None:
        response = httpx.get(
            f"https://api.github.com/gists/{ref.gist_id}",
            timeout=10.0,
        )
        if response.status_code != 200:
            return None
        data = response.json()
        if data.get("owner", {}).get("login") != ref.owner:
            return None
        return GistMetadata(
            ref=ref,
            description=data.get("description") or "",
            public_key=data.get("files", {}).get(GIST_PUB_FILENAME, {}).get("content", "").strip(),
        )


class DiscoveryRunner:
    @classmethod
    def run(cls, saved_username: str = "", github_lookup_allowed: bool = True) -> DiscoveryResult:
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
        plan = SetupRoutePlanner.plan(
            capabilities, identity, ssh_keys, gpg_keys,
            github_lookup_allowed=github_lookup_allowed,
        )
        rows = cls._build_rows(capabilities, identity, ssh_keys, gpg_keys)
        return DiscoveryResult(
            capabilities=capabilities,
            identity=identity,
            existing_ssh=ssh_keys,
            existing_gpg=gpg_keys,
            rows=rows,
            plan=plan,
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
            f"@{identity.github_username} signed in"
            if capabilities.gh_authed and identity.github_username
            else "Installed, not signed in"
            if capabilities.has_gh
            else "Not installed"
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
            if ssh_keys or gpg_keys else "None found"
        )
        return (
            DiscoverRow("GitHub CLI", gh_state, gh_detail),
            DiscoverRow(
                "GitHub",
                username_state,
                identity.github_username or "Not found",
            ),
            DiscoverRow(
                "Email",
                email_state,
                identity.github_email if identity.email_usable else "Not needed",
            ),
            DiscoverRow("Local keys", local_state, local_detail),
            DiscoverRow(
                "Public key",
                DiscoverRowState.WAITING,
                "",
            ),
            DiscoverRow(
                "Verification",
                DiscoverRowState.WAITING,
                "",
            ),
        )

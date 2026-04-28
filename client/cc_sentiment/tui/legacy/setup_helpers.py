from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx

from cc_sentiment.signing import KeyDiscovery
from cc_sentiment.tui.legacy.system import Browser, Clipboard
from cc_sentiment.tui.legacy.setup_state import (
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
GITHUB_API_USERS_URL = "https://api.github.com/users"


class Sanitizer:
    HOME_PATTERN = re.compile(re.escape(str(Path.home())))
    TOKEN_PATTERN = re.compile(r"(gho_|ghp_|ghu_|ghs_)[A-Za-z0-9]{20,}")

    @classmethod
    def error(cls, text: str, max_len: int = 240) -> str:
        cleaned = cls.TOKEN_PATTERN.sub("[redacted]", text or "")
        cleaned = cls.HOME_PATTERN.sub("~", cleaned)
        cleaned = cleaned.strip()
        return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


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
        github_lookup_allowed: bool = True,
    ) -> SetupPlan:
        if not capabilities.has_ssh_keygen:
            return SetupPlan(intervention=SetupIntervention.BLOCKED)
        if not github_lookup_allowed:
            return SetupPlan(intervention=SetupIntervention.BLOCKED)
        if capabilities.gh_authed:
            return SetupPlan(recommended=cls._managed_ssh_gist())
        if identity.github_username:
            return SetupPlan(recommended=cls._managed_ssh_manual_gist())
        return SetupPlan(intervention=SetupIntervention.USERNAME)

    @classmethod
    def alternate_openpgp_route(cls) -> SetupRoute:
        return cls._managed_gpg_openpgp()

    @staticmethod
    def _managed_ssh_gist() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_SSH_GIST,
            publish_method=PublishMethod.GIST_AUTO,
            key_kind=KeyKind.SSH,
            key_plan=GenerateSSHKey(),
        )

    @staticmethod
    def _managed_gpg_openpgp() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_GPG_OPENPGP,
            publish_method=PublishMethod.OPENPGP,
            key_kind=KeyKind.GPG,
            key_plan=GenerateGPGKey(),
        )

    @staticmethod
    def _managed_ssh_manual_gist() -> SetupRoute:
        return SetupRoute(
            route_id=RouteId.MANAGED_SSH_MANUAL_GIST,
            publish_method=PublishMethod.GIST_MANUAL,
            key_kind=KeyKind.SSH,
            key_plan=GenerateSSHKey(),
        )


@dataclass(frozen=True, slots=True)
class GistRef:
    owner: str
    gist_id: str


@dataclass(frozen=True, slots=True)
class GistMetadata:
    ref: GistRef
    description: str
    file_contents: dict[str, str]


class GistDiscovery:
    POLL_LIMIT = 10

    @classmethod
    def list_public_gists(cls, username: str, limit: int = POLL_LIMIT) -> tuple[GistRef, ...]:
        data = cls._fetch_json(f"users/{username}/gists", params={"per_page": str(limit)})
        if not isinstance(data, list):
            return ()
        return tuple(
            GistRef(owner=username, gist_id=str(gist["id"]))
            for gist in sorted(
                data,
                key=lambda gist: gist.get("updated_at") or gist.get("created_at") or "",
                reverse=True,
            )
            if gist.get("public")
        )[:limit]

    @classmethod
    def fetch_metadata(cls, ref: GistRef) -> GistMetadata | None:
        data = cls._fetch_json(f"gists/{ref.gist_id}")
        if not isinstance(data, dict):
            return None
        if data.get("owner", {}).get("login") != ref.owner:
            return None
        files = data.get("files") or {}
        return GistMetadata(
            ref=ref,
            description=data.get("description") or "",
            file_contents={
                name: (entry.get("content") or "")
                for name, entry in files.items()
                if isinstance(entry, dict)
            },
        )

    @classmethod
    def find_gist_with_public_key(cls, username: str, public_key: str) -> GistRef | None:
        needle = public_key.strip()
        if not needle or not username:
            return None
        for ref in cls.list_public_gists(username):
            metadata = cls.fetch_metadata(ref)
            if metadata is None:
                continue
            if any(needle in (content or "") for content in metadata.file_contents.values()):
                return ref
        return None

    @staticmethod
    def _fetch_json(path: str, params: dict[str, str] | None = None):
        if KeyDiscovery.gh_authenticated():
            args = ["gh", "api", path]
            for key, value in (params or {}).items():
                args.extend(["-f", f"{key}={value}"])
            result = subprocess.run(args, capture_output=True, text=True, timeout=15)
            if result.returncode != 0:
                return None
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                return None
        response = httpx.get(
            f"https://api.github.com/{path}",
            params=params,
            timeout=10.0,
        )
        if response.status_code != 200:
            return None
        return response.json()


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
            capabilities, identity,
            github_lookup_allowed=github_lookup_allowed,
        )
        return DiscoveryResult(
            capabilities=capabilities,
            identity=identity,
            existing_ssh=ssh_keys,
            existing_gpg=gpg_keys,
            plan=plan,
        )

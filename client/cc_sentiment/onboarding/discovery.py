from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import anyio
import httpx

from cc_sentiment.models import (
    ContributorId,
    GistConfig,
    GistGPGConfig,
    GPGConfig,
    SSHConfig,
)
from cc_sentiment.onboarding.state import ExistingKey, ExistingKeys, Identity
from cc_sentiment.signing import (
    GPGBackend,
    KeyDiscovery,
    SSHBackend,
    SSHKeyInfo,
)
from cc_sentiment.upload import AuthOk, Uploader


GIST_NEW_URL = "https://gist.github.com/"
GH_KEYS_URL = "https://github.com/settings/keys/new"
GITHUB_API_USERS_URL = "https://api.github.com/users"
CC_SENTIMENT_DIR = Path.home() / ".cc-sentiment" / "keys"


ClientConfig = SSHConfig | GPGConfig | GistConfig | GistGPGConfig


@dataclass(frozen=True, slots=True)
class GistRef:
    owner: str
    gist_id: str


class Sanitizer:
    HOME_PATTERN = re.compile(re.escape(str(Path.home())))
    TOKEN_PATTERN = re.compile(r"(gho_|ghp_|ghu_|ghs_)[A-Za-z0-9]{20,}")

    @classmethod
    def error(cls, text: str, max_len: int = 240) -> str:
        cleaned = cls.TOKEN_PATTERN.sub("[redacted]", text or "")
        cleaned = cls.HOME_PATTERN.sub("~", cleaned)
        cleaned = cleaned.strip()
        return cleaned if len(cleaned) <= max_len else cleaned[: max_len - 1] + "…"


class IdentityProbe:
    @staticmethod
    async def detect(saved_username: str = "") -> Identity:
        if username := await KeyDiscovery.gh_login():
            email = await KeyDiscovery.gh_primary_email() or ""
            return Identity(
                github_username=username,
                email=email,
                email_usable=bool(email) and not KeyDiscovery.is_noreply_email(email),
            )
        if saved_username:
            return Identity(github_username=saved_username)
        return Identity()

    @staticmethod
    async def mine_email(username: str) -> tuple[str, bool]:
        try:
            repo = await KeyDiscovery.fetch_latest_public_repo(username)
        except httpx.HTTPError:
            return "", False
        if not repo:
            return "", False
        try:
            email = await KeyDiscovery.fetch_commit_email(username, repo)
        except httpx.HTTPError:
            return "", False
        if not email:
            return "", False
        return email, not KeyDiscovery.is_noreply_email(email)

    @staticmethod
    async def validate_username(username: str) -> Literal["ok", "not-found", "unreachable"]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{GITHUB_API_USERS_URL}/{username}")
        except httpx.HTTPError:
            return "unreachable"
        match response.status_code:
            case 200:
                return "ok"
            case 404:
                return "not-found"
            case _:
                return "unreachable"


class LocalKeysProbe:
    @classmethod
    def detect_ssh(cls) -> tuple[ExistingKey, ...]:
        keys = tuple(cls._existing(info, managed=cls._is_managed(info.path)) for info in KeyDiscovery.find_ssh_keys())
        managed_path = KeyDiscovery.find_gist_keypair()
        if managed_path is not None and not any(k.path == managed_path for k in keys):
            info = KeyDiscovery.ssh_key_info(managed_path, default_comment="cc-sentiment")
            assert info is not None
            return keys + (cls._existing(info, managed=True),)
        return keys

    @staticmethod
    async def detect_gpg() -> tuple[ExistingKey, ...]:
        return tuple(
            ExistingKey(fingerprint=info.fpr, label=info.email, algorithm=info.algo)
            for info in await KeyDiscovery.find_gpg_keys()
        )

    @classmethod
    async def detect_all(cls) -> ExistingKeys:
        return ExistingKeys(ssh=cls.detect_ssh(), gpg=await cls.detect_gpg())

    @staticmethod
    def _is_managed(path: Path) -> bool:
        return path.parent.name == "keys" and path.parent.parent.name == ".cc-sentiment"

    @staticmethod
    def _existing(info: SSHKeyInfo, *, managed: bool) -> ExistingKey:
        return ExistingKey(
            fingerprint=SSHBackend(private_key_path=info.path).fingerprint(),
            label=info.path.name,
            managed=managed,
            path=info.path,
            algorithm=info.algorithm,
        )


class GistDiscovery:
    POLL_LIMIT = 10

    @classmethod
    async def list_public_gists(cls, username: str, limit: int = POLL_LIMIT) -> tuple[GistRef, ...]:
        data = await cls._fetch_json(f"users/{username}/gists", params={"per_page": str(limit)})
        if not isinstance(data, list):
            return ()
        return tuple(
            GistRef(owner=username, gist_id=str(gist["id"]))
            for gist in sorted(
                data,
                key=lambda g: g.get("updated_at") or g.get("created_at") or "",
                reverse=True,
            )
            if gist.get("public")
        )[:limit]

    @classmethod
    async def fetch_metadata(cls, ref: GistRef) -> dict[str, str] | None:
        data = await cls._fetch_json(f"gists/{ref.gist_id}")
        if not isinstance(data, dict):
            return None
        if data.get("owner", {}).get("login") != ref.owner:
            return None
        files = data.get("files") or {}
        return {
            name: (entry.get("content") or "")
            for name, entry in files.items()
            if isinstance(entry, dict)
        }

    @classmethod
    async def find_gist_with_public_key(cls, username: str, public_key: str) -> GistRef | None:
        needle = public_key.strip()
        if not needle or not username:
            return None
        for ref in await cls.list_public_gists(username):
            files = await cls.fetch_metadata(ref)
            if files is None:
                continue
            if any(needle in (content or "") for content in files.values()):
                return ref
        return None

    @staticmethod
    async def _fetch_json(path: str, params: dict[str, str] | None = None):
        if await KeyDiscovery.gh_authenticated():
            args = ["gh", "api", path]
            for key, value in (params or {}).items():
                args.extend(["-f", f"{key}={value}"])
            with anyio.fail_after(15):
                result = await anyio.run_process(args, check=False)
            if result.returncode != 0:
                return None
            try:
                return json.loads(result.stdout.decode())
            except json.JSONDecodeError:
                return None
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.github.com/{path}",
                params=params,
            )
        if response.status_code != 200:
            return None
        return response.json()


class AutoVerify:
    @classmethod
    async def probe(cls, identity: Identity, existing: ExistingKeys) -> ClientConfig | None:
        username = identity.github_username
        if username:
            for ssh in existing.ssh:
                assert ssh.path is not None
                config: ClientConfig = SSHConfig(
                    contributor_id=ContributorId(username),
                    key_path=ssh.path,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    return config
            for gpg in existing.gpg:
                config = GPGConfig(
                    contributor_type="github",
                    contributor_id=ContributorId(username),
                    fpr=gpg.fingerprint,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    return config
            for ssh in existing.ssh:
                assert ssh.path is not None
                public_key = (await SSHBackend(private_key_path=ssh.path).public_key_text()).strip()
                if (ref := await GistDiscovery.find_gist_with_public_key(username, public_key)) is None:
                    continue
                config = GistConfig(
                    contributor_id=ContributorId(ref.owner),
                    key_path=ssh.path,
                    gist_id=ref.gist_id,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    return config
            for gpg in existing.gpg:
                public_key = (await GPGBackend(fpr=gpg.fingerprint).public_key_text()).strip()
                if (ref := await GistDiscovery.find_gist_with_public_key(username, public_key)) is None:
                    continue
                config = GistGPGConfig(
                    contributor_id=ContributorId(ref.owner),
                    fpr=gpg.fingerprint,
                    gist_id=ref.gist_id,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    return config
        for gpg in existing.gpg:
            config = GPGConfig(
                contributor_type="gpg",
                contributor_id=ContributorId(gpg.fingerprint),
                fpr=gpg.fingerprint,
            )
            if isinstance(await Uploader().probe_credentials(config), AuthOk):
                return config
        return None

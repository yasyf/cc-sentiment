from __future__ import annotations

import asyncio
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

import gnupg
import httpx

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
GIST_ID_PATTERN = re.compile(r"^[a-f0-9]{20,40}$")
GIST_DESCRIPTION = "cc-sentiment public key"
GIST_PUB_FILENAME = "cc-sentiment.pub"

__all__ = ["Verifier"]


class KeyCache:
    async def get(self, key: str) -> object | None: ...
    async def put(self, key: str, value: object) -> None: ...


@dataclass
class DictKeyCache(KeyCache):
    d: dict[str, object] = field(default_factory=dict)

    async def get(self, key: str) -> object | None:
        return self.d.get(key)

    async def put(self, key: str, value: object) -> None:
        self.d[key] = value


@dataclass(frozen=True)
class ModalKeyCache(KeyCache):
    modal_dict: object

    async def get(self, key: str) -> object | None:
        return await self.modal_dict.get.aio(key)

    async def put(self, key: str, value: object) -> None:
        await self.modal_dict.put.aio(key, value)


@dataclass
class Verifier:
    key_cache: KeyCache | None = None

    async def fetch_github_ssh_keys(self, username: str) -> list[str]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"https://github.com/{username}.keys", timeout=10.0)
            if response.status_code == 404:
                raise ValueError(f"GitHub user not found: {username!r}")
            response.raise_for_status()
        return [line for line in response.text.strip().split("\n") if line]

    async def fetch_github_gpg_keys(self, username: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"https://github.com/{username}.gpg", timeout=10.0)
        return response.text if response.status_code == 200 else ""

    async def fetch_openpgp_key(self, fpr: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://keys.openpgp.org/vks/v1/by-fingerprint/{fpr.upper()}",
                timeout=10.0,
            )
        return response.text if response.status_code == 200 else ""

    async def get_or_fetch(self, cache_key: str, fetcher: Any, *args: Any, force: bool = False) -> Any:
        if not force and self.key_cache is not None and (cached := await self.key_cache.get(cache_key)) is not None:
            return cached
        result = await fetcher(*args)
        if self.key_cache is not None:
            await self.key_cache.put(cache_key, result)
        return result

    async def verify_signature(self, contributor_type: str, contributor_id: str,
                               payload: str, signature: str) -> bool:
        match contributor_type:
            case "github":
                match signature.split("\n", 1)[0].strip():
                    case s if "SSH" in s:
                        return await self.verify_github_ssh(contributor_id, payload, signature)
                    case s if "PGP" in s:
                        return await self.verify_github_gpg(contributor_id, payload, signature)
                    case _:
                        raise ValueError("Unknown signature format")
            case "gpg":
                return await self.verify_openpgp(contributor_id, payload, signature)
            case "gist":
                return await self.verify_gist(contributor_id, payload, signature)
            case _:
                raise ValueError(f"Unknown contributor type: {contributor_type!r}")

    async def verify_github_ssh(self, username: str, payload: str, signature: str) -> bool:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(f"Invalid GitHub username: {username!r}")

        cached_keys: list[str] = await self.get_or_fetch(f"ssh:{username}", self.fetch_github_ssh_keys, username)
        if any(await asyncio.gather(*(
            self.verify_with_ssh_key(username, key, payload, signature)
            for key in cached_keys
        ))):
            return True

        if self.key_cache is None:
            return False

        fresh_keys: list[str] = await self.get_or_fetch(f"ssh:{username}", self.fetch_github_ssh_keys, username, force=True)
        return any(await asyncio.gather(*(
            self.verify_with_ssh_key(username, key, payload, signature)
            for key in fresh_keys
            if key not in cached_keys
        )))

    async def verify_github_gpg(self, username: str, payload: str, signature: str) -> bool:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(f"Invalid GitHub username: {username!r}")

        armor: str = await self.get_or_fetch(f"gpg-github:{username}", self.fetch_github_gpg_keys, username)
        if armor and await self.check_gpg_signature(armor, payload, signature):
            return True

        if self.key_cache is None:
            return False

        fresh_armor: str = await self.get_or_fetch(f"gpg-github:{username}", self.fetch_github_gpg_keys, username, force=True)
        if fresh_armor != armor and fresh_armor:
            return await self.check_gpg_signature(fresh_armor, payload, signature)

        return False

    async def verify_openpgp(self, fingerprint: str, payload: str, signature: str) -> bool:
        armor: str = await self.get_or_fetch(f"gpg-openpgp:{fingerprint}", self.fetch_openpgp_key, fingerprint)
        if armor and await self.check_gpg_signature(armor, payload, signature):
            return True

        if self.key_cache is None:
            return False

        fresh_armor: str = await self.get_or_fetch(f"gpg-openpgp:{fingerprint}", self.fetch_openpgp_key, fingerprint, force=True)
        if fresh_armor != armor and fresh_armor:
            return await self.check_gpg_signature(fresh_armor, payload, signature)

        return False

    def github_headers(self) -> dict[str, str]:
        token = os.environ.get("GITHUB_TOKEN")
        return {"Authorization": f"Bearer {token}"} if token else {}

    async def fetch_gist(self, gist_id: str) -> dict:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://api.github.com/gists/{gist_id}",
                headers=self.github_headers(),
                timeout=10.0,
            )
            if response.status_code == 404:
                raise ValueError(f"Gist not found: {gist_id!r}")
            response.raise_for_status()
        return response.json()

    async def fetch_gist_pubkey(self, gist_id: str, expected_owner: str) -> str:
        gist = await self.fetch_gist(gist_id)
        if gist.get("owner", {}).get("login") != expected_owner:
            raise ValueError(f"Gist {gist_id!r} not owned by {expected_owner!r}")
        if gist.get("description") != GIST_DESCRIPTION:
            raise ValueError(f"Gist {gist_id!r} is not a cc-sentiment gist")
        return gist.get("files", {}).get(GIST_PUB_FILENAME, {}).get("content", "").strip()

    async def verify_gist(self, combined_id: str, payload: str, signature: str) -> bool:
        username, _, gist_id = combined_id.partition("/")
        if not username or not gist_id:
            raise ValueError(f"Invalid gist contributor id: {combined_id!r}")
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(f"Invalid GitHub username: {username!r}")
        if not GIST_ID_PATTERN.fullmatch(gist_id):
            raise ValueError(f"Invalid gist id: {gist_id!r}")

        cached_key: str = await self.get_or_fetch(
            f"gist:{gist_id}", self.fetch_gist_pubkey, gist_id, username,
        )
        if cached_key and await self.verify_with_ssh_key(username, cached_key, payload, signature):
            return True

        if self.key_cache is None:
            return False

        fresh_key: str = await self.get_or_fetch(
            f"gist:{gist_id}", self.fetch_gist_pubkey, gist_id, username, force=True,
        )
        if fresh_key and fresh_key != cached_key:
            return await self.verify_with_ssh_key(username, fresh_key, payload, signature)
        return False

    async def check_gpg_signature(self, public_key_armor: str, payload: str, signature: str) -> bool:
        return await asyncio.to_thread(self._check_gpg_signature, public_key_armor, payload, signature)

    @staticmethod
    def _check_gpg_signature(public_key_armor: str, payload: str, signature: str) -> bool:
        with tempfile.TemporaryDirectory() as tmpdir:
            g = gnupg.GPG(gnupghome=tmpdir)
            g.import_keys(public_key_armor)

            sig_path = f"{tmpdir}/sig.asc"
            data_path = f"{tmpdir}/data.json"
            with open(sig_path, "wb") as f:
                f.write(signature.encode())
            with open(data_path, "wb") as f:
                f.write(payload.encode())

            with open(sig_path, "rb") as sig_fh:
                return bool(g.verify_file(sig_fh, data_filename=data_path))

    async def verify_with_ssh_key(self, username: str, key: str, payload: str, signature: str) -> bool:
        return await asyncio.to_thread(self._verify_with_ssh_key, username, key, payload, signature)

    @staticmethod
    def _verify_with_ssh_key(username: str, key: str, payload: str, signature: str) -> bool:
        with (
            tempfile.NamedTemporaryFile(mode="w", suffix=".pub") as allowed_signers_file,
            tempfile.NamedTemporaryFile(mode="w", suffix=".sig") as sig_file,
            tempfile.NamedTemporaryFile(mode="w", suffix=".json") as payload_file,
        ):
            allowed_signers_file.write(f"{username} {key}\n")
            allowed_signers_file.flush()

            sig_file.write(signature)
            sig_file.flush()

            payload_file.write(payload)
            payload_file.flush()

            with open(payload_file.name) as stdin_fh:
                return subprocess.run(
                    [
                        "ssh-keygen",
                        "-Y", "verify",
                        "-f", allowed_signers_file.name,
                        "-I", username,
                        "-n", "cc-sentiment",
                        "-s", sig_file.name,
                    ],
                    stdin=stdin_fh,
                    capture_output=True,
                    timeout=10,
                ).returncode == 0

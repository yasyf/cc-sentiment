from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile

import gnupg
import httpx

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")

__all__ = ["Verifier"]


class Verifier:
    def __init__(self, key_cache: dict | None = None) -> None:
        self.key_cache = key_cache

    async def fetch_ssh_keys(self, username: str) -> list[str]:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"https://github.com/{username}.keys", timeout=10.0)
            if response.status_code == 404:
                raise ValueError(f"GitHub user not found: {username!r}")
            response.raise_for_status()
        return [line for line in response.text.strip().split("\n") if line]

    async def fetch_gpg_keys(self, username: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(f"https://github.com/{username}.gpg", timeout=10.0)
        return response.text if response.status_code == 200 else ""

    async def get_or_fetch_ssh_keys(self, username: str, *, force: bool = False) -> list[str]:
        cache_key = f"ssh:{username}"
        if not force and self.key_cache is not None and cache_key in self.key_cache:
            return self.key_cache[cache_key]
        keys = await self.fetch_ssh_keys(username)
        if self.key_cache is not None:
            self.key_cache[cache_key] = keys
        return keys

    async def get_or_fetch_gpg_keys(self, username: str, *, force: bool = False) -> str:
        cache_key = f"gpg:{username}"
        if not force and self.key_cache is not None and cache_key in self.key_cache:
            return self.key_cache[cache_key]
        armor = await self.fetch_gpg_keys(username)
        if self.key_cache is not None:
            self.key_cache[cache_key] = armor
        return armor

    async def verify_signature(self, username: str, payload_json: str, signature: str) -> bool:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(f"Invalid GitHub username: {username!r}")

        match signature.split("\n", 1)[0].strip():
            case s if "SSH" in s:
                return await self.verify_ssh(username, payload_json, signature)
            case s if "PGP" in s:
                return await self.verify_gpg(username, payload_json, signature)
            case _:
                raise ValueError("Unknown signature format")

    async def verify_ssh(self, username: str, payload_json: str, signature: str) -> bool:
        cached_keys = await self.get_or_fetch_ssh_keys(username)
        if any(await asyncio.gather(*(
            self.verify_with_ssh_key(username, key, payload_json, signature)
            for key in cached_keys
        ))):
            return True

        if self.key_cache is None:
            return False

        fresh_keys = await self.get_or_fetch_ssh_keys(username, force=True)
        return any(await asyncio.gather(*(
            self.verify_with_ssh_key(username, key, payload_json, signature)
            for key in fresh_keys
            if key not in cached_keys
        )))

    async def verify_gpg(self, username: str, payload_json: str, signature: str) -> bool:
        armor = await self.get_or_fetch_gpg_keys(username)
        if armor and await self.check_gpg_signature(armor, payload_json, signature):
            return True

        if self.key_cache is None:
            return False

        fresh_armor = await self.get_or_fetch_gpg_keys(username, force=True)
        if fresh_armor != armor and fresh_armor:
            return await self.check_gpg_signature(fresh_armor, payload_json, signature)

        return False

    async def check_gpg_signature(self, public_key_armor: str, payload_json: str, signature: str) -> bool:
        return await asyncio.to_thread(self._check_gpg_signature, public_key_armor, payload_json, signature)

    @staticmethod
    def _check_gpg_signature(public_key_armor: str, payload_json: str, signature: str) -> bool:
        with tempfile.TemporaryDirectory() as tmpdir:
            g = gnupg.GPG(gnupghome=tmpdir)
            g.import_keys(public_key_armor)
            return bool(g.verify(payload_json, sig=signature))

    async def verify_with_ssh_key(self, username: str, key: str, payload_json: str, signature: str) -> bool:
        return await asyncio.to_thread(self._verify_with_ssh_key, username, key, payload_json, signature)

    @staticmethod
    def _verify_with_ssh_key(username: str, key: str, payload_json: str, signature: str) -> bool:
        with (
            tempfile.NamedTemporaryFile(mode="w", suffix=".pub") as allowed_signers_file,
            tempfile.NamedTemporaryFile(mode="w", suffix=".sig") as sig_file,
            tempfile.NamedTemporaryFile(mode="w", suffix=".json") as payload_file,
        ):
            allowed_signers_file.write(f"{username} {key}\n")
            allowed_signers_file.flush()

            sig_file.write(signature)
            sig_file.flush()

            payload_file.write(payload_json)
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

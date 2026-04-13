from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile

import gnupg
import httpx

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
GPG_FPR_PATTERN = re.compile(r"^[0-9A-Fa-f]{16,40}$")

__all__ = ["Verifier"]


class KeyCache:
    async def get(self, key: str) -> object | None: ...
    async def put(self, key: str, value: object) -> None: ...


class DictKeyCache(KeyCache):
    def __init__(self) -> None:
        self.d: dict = {}

    async def get(self, key: str) -> object | None:
        return self.d.get(key)

    async def put(self, key: str, value: object) -> None:
        self.d[key] = value


class ModalKeyCache(KeyCache):
    def __init__(self, modal_dict: object) -> None:
        self.modal_dict = modal_dict

    async def get(self, key: str) -> object | None:
        return await self.modal_dict.get.aio(key)

    async def put(self, key: str, value: object) -> None:
        await self.modal_dict.put.aio(key, value)


class Verifier:
    def __init__(self, key_cache: KeyCache | None = None) -> None:
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

    async def fetch_openpgp_key_by_fpr(self, fpr: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://keys.openpgp.org/vks/v1/by-fingerprint/{fpr.upper()}",
                timeout=10.0,
            )
        return response.text if response.status_code == 200 else ""

    async def fetch_openpgp_key_by_keyid(self, keyid: str) -> str:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://keys.openpgp.org/vks/v1/by-keyid/{keyid.upper()}",
                timeout=10.0,
            )
        return response.text if response.status_code == 200 else ""

    async def get_or_fetch_ssh_keys(self, username: str, *, force: bool = False) -> list[str]:
        cache_key = f"ssh:{username}"
        if not force and self.key_cache is not None and (cached := await self.key_cache.get(cache_key)) is not None:
            return cached
        keys = await self.fetch_ssh_keys(username)
        if self.key_cache is not None:
            await self.key_cache.put(cache_key, keys)
        return keys

    async def get_or_fetch_gpg_keys(self, username: str, *, force: bool = False) -> str:
        cache_key = f"gpg:{username}"
        if not force and self.key_cache is not None and (cached := await self.key_cache.get(cache_key)) is not None:
            return cached
        armor = await self.fetch_gpg_keys(username)
        if self.key_cache is not None:
            await self.key_cache.put(cache_key, armor)
        return armor

    async def verify_signature(self, username: str, payload_json: str, signature: str) -> bool:
        match signature.split("\n", 1)[0].strip():
            case s if "SSH" in s:
                if not USERNAME_PATTERN.fullmatch(username):
                    raise ValueError(f"Invalid GitHub username: {username!r}")
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
        if USERNAME_PATTERN.fullmatch(username):
            armor = await self.get_or_fetch_gpg_keys(username)
            if armor and await self.check_gpg_signature(armor, payload_json, signature):
                return True

            if self.key_cache is not None:
                fresh_armor = await self.get_or_fetch_gpg_keys(username, force=True)
                if fresh_armor != armor and fresh_armor:
                    if await self.check_gpg_signature(fresh_armor, payload_json, signature):
                        return True

        if GPG_FPR_PATTERN.fullmatch(username):
            openpgp_armor = await self.fetch_openpgp_key_by_fpr(username)
            if openpgp_armor and await self.check_gpg_signature(openpgp_armor, payload_json, signature):
                return True

        keyid = self._extract_gpg_key_id(signature)
        if keyid:
            openpgp_armor = await self.fetch_openpgp_key_by_keyid(keyid)
            if openpgp_armor and await self.check_gpg_signature(openpgp_armor, payload_json, signature):
                return True

        return False

    @staticmethod
    def _extract_gpg_key_id(signature: str) -> str | None:
        with tempfile.TemporaryDirectory() as tmpdir:
            sig_path = f"{tmpdir}/sig.asc"
            with open(sig_path, "w") as f:
                f.write(signature)
            result = subprocess.run(
                ["gpg", "--homedir", tmpdir, "--list-packets", sig_path],
                capture_output=True, text=True, timeout=10,
            )
            for line in result.stdout.splitlines():
                if "keyid" in line.lower():
                    match = re.search(r"([0-9A-Fa-f]{16})", line)
                    if match:
                        return match.group(1)
        return None

    async def check_gpg_signature(self, public_key_armor: str, payload_json: str, signature: str) -> bool:
        return await asyncio.to_thread(self._check_gpg_signature, public_key_armor, payload_json, signature)

    @staticmethod
    def _check_gpg_signature(public_key_armor: str, payload_json: str, signature: str) -> bool:
        with tempfile.TemporaryDirectory() as tmpdir:
            g = gnupg.GPG(gnupghome=tmpdir)
            g.import_keys(public_key_armor)

            sig_path = f"{tmpdir}/sig.asc"
            data_path = f"{tmpdir}/data.json"
            with open(sig_path, "wb") as f:
                f.write(signature.encode())
            with open(data_path, "wb") as f:
                f.write(payload_json.encode())

            with open(sig_path, "rb") as sig_fh:
                return bool(g.verify_file(sig_fh, data_filename=data_path))

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

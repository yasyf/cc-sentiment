from __future__ import annotations

import asyncio
import re
import subprocess
import tempfile

import httpx

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")

__all__ = ["Verifier"]


class Verifier:
    def __init__(self, key_cache: dict | None = None) -> None:
        self.key_cache = key_cache

    async def fetch_github_keys(self, username: str) -> list[str]:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://github.com/{username}.keys", timeout=10.0,
            )
            if response.status_code == 404:
                raise ValueError(f"GitHub user not found: {username!r}")
            response.raise_for_status()
        return [line for line in response.text.strip().split("\n") if line]

    async def get_keys(self, username: str) -> list[str]:
        if self.key_cache is not None:
            try:
                return self.key_cache[username]
            except KeyError:
                pass

        keys = await self.fetch_github_keys(username)
        if self.key_cache is not None:
            self.key_cache[username] = keys
        return keys

    async def refresh_keys(self, username: str) -> list[str]:
        keys = await self.fetch_github_keys(username)
        if self.key_cache is not None:
            self.key_cache[username] = keys
        return keys

    async def verify_signature(self, username: str, payload_json: str, signature: str) -> bool:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(f"Invalid GitHub username: {username!r}")

        keys = await self.get_keys(username)
        if any(await asyncio.gather(*(
            self.verify_with_key(username, key, payload_json, signature)
            for key in keys
        ))):
            return True

        # Cache miss or stale keys -- re-fetch and retry
        if self.key_cache is not None:
            fresh_keys = await self.refresh_keys(username)
            new_keys = [k for k in fresh_keys if k not in keys]
            if new_keys:
                return any(await asyncio.gather(*(
                    self.verify_with_key(username, key, payload_json, signature)
                    for key in new_keys
                )))

        return False

    async def verify_with_key(self, username: str, key: str, payload_json: str, signature: str) -> bool:
        return await asyncio.to_thread(
            self._verify_with_key_sync, username, key, payload_json, signature,
        )

    @staticmethod
    def _verify_with_key_sync(username: str, key: str, payload_json: str, signature: str) -> bool:
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
                result = subprocess.run(
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
                )
            return result.returncode == 0

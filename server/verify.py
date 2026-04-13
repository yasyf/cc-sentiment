from __future__ import annotations

import re
import subprocess
import tempfile

import httpx

USERNAME_PATTERN = re.compile(r"^[a-zA-Z0-9-]+$")

__all__ = ["Verifier"]


class Verifier:
    def fetch_github_keys(self, username: str) -> list[str]:
        response = httpx.get(
            f"https://github.com/{username}.keys", timeout=10.0,
        )
        response.raise_for_status()
        return [line for line in response.text.strip().split("\n") if line]

    def verify_signature(self, username: str, payload_json: str, signature: str) -> bool:
        if not USERNAME_PATTERN.fullmatch(username):
            raise ValueError(f"Invalid GitHub username: {username!r}")
        keys = self.fetch_github_keys(username)
        return any(
            self.verify_with_key(username, key, payload_json, signature)
            for key in keys
        )

    def verify_with_key(self, username: str, key: str, payload_json: str, signature: str) -> bool:
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

from __future__ import annotations

import subprocess
import tempfile

import httpx


class Verifier:
    def fetch_github_keys(self, username: str) -> list[str]:
        response = httpx.get(f"https://github.com/{username}.keys")
        response.raise_for_status()
        return [line for line in response.text.strip().split("\n") if line]

    def verify_signature(self, username: str, payload_json: str, signature: str) -> bool:
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

            result = subprocess.run(
                [
                    "ssh-keygen",
                    "-Y", "verify",
                    "-f", allowed_signers_file.name,
                    "-I", username,
                    "-n", "cc-sentiment",
                    "-s", sig_file.name,
                ],
                stdin=open(payload_file.name),
                capture_output=True,
            )
            return result.returncode == 0

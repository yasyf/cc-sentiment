from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import httpx

from cc_sentiment.models import SentimentRecord

SSH_DIR = Path.home() / ".ssh"
KEY_CANDIDATES = ["id_ed25519", "id_rsa"]
NAMESPACE = "cc-sentiment"


class KeyDiscovery:
    @staticmethod
    def find_private_key() -> Path:
        for name in KEY_CANDIDATES:
            path = SSH_DIR / name
            if path.exists():
                return path
        raise FileNotFoundError(f"No SSH key found in {SSH_DIR}")

    @staticmethod
    def read_public_key(private_key_path: Path) -> str:
        pub_path = private_key_path.with_suffix(
            private_key_path.suffix + ".pub"
        )
        return pub_path.read_text().strip()

    @staticmethod
    def fetch_github_keys(username: str) -> list[str]:
        response = httpx.get(f"https://github.com/{username}.keys", timeout=10.0)
        response.raise_for_status()
        return [line.strip() for line in response.text.splitlines() if line.strip()]

    @classmethod
    def match_github_key(cls, username: str) -> Path:
        github_keys = cls.fetch_github_keys(username)
        private_key = cls.find_private_key()
        local_pub = cls.read_public_key(private_key)

        local_key_data = " ".join(local_pub.split()[:2])
        for gh_key in github_keys:
            gh_key_data = " ".join(gh_key.split()[:2])
            if local_key_data == gh_key_data:
                return private_key

        raise ValueError(
            f"No local SSH key matches GitHub keys for {username}. "
            f"Add your key at https://github.com/settings/keys"
        )


class PayloadSigner:
    @staticmethod
    def canonical_json(records: list[SentimentRecord]) -> str:
        return json.dumps(
            [r.model_dump(mode="json", by_alias=True) for r in records],
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def sign(data: str, key_path: Path) -> str:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(data)
            data_path = Path(f.name)

        try:
            subprocess.run(
                [
                    "ssh-keygen",
                    "-Y",
                    "sign",
                    "-f",
                    str(key_path),
                    "-n",
                    NAMESPACE,
                    str(data_path),
                ],
                check=True,
                capture_output=True,
                timeout=10,
            )
            sig_path = Path(f"{data_path}.sig")
            signature = sig_path.read_text()
            sig_path.unlink()
            return signature
        finally:
            data_path.unlink(missing_ok=True)

    @classmethod
    def sign_records(cls, records: list[SentimentRecord], key_path: Path) -> str:
        canonical = cls.canonical_json(records)
        return cls.sign(canonical, key_path)

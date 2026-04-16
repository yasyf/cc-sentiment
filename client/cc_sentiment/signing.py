from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import gnupg
import httpx

from cc_sentiment.models import SentimentRecord

SSH_DIR = Path.home() / ".ssh"
SSH_KEY_CANDIDATES = ("id_ed25519", "id_rsa")
NAMESPACE = "cc-sentiment"


class SigningBackend(Protocol):
    key_type: Literal["ssh", "gpg"]

    def sign(self, data: str) -> str: ...
    def public_key_text(self) -> str: ...
    def fingerprint(self) -> str: ...


@dataclass(frozen=True)
class SSHKeyInfo:
    path: Path
    algorithm: str
    comment: str


@dataclass(frozen=True)
class GPGKeyInfo:
    fpr: str
    email: str
    algo: str


@dataclass(frozen=True)
class SSHBackend:
    private_key_path: Path
    key_type: Literal["ssh"] = "ssh"

    def sign(self, data: str) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(data)
            data_path = Path(f.name)

        try:
            subprocess.run(
                ["ssh-keygen", "-Y", "sign", "-f", str(self.private_key_path), "-n", NAMESPACE, str(data_path)],
                check=True, capture_output=True, timeout=10,
            )
            sig_path = Path(f"{data_path}.sig")
            signature = sig_path.read_text()
            sig_path.unlink()
            return signature
        finally:
            data_path.unlink(missing_ok=True)

    def public_key_text(self) -> str:
        return self.private_key_path.with_suffix(self.private_key_path.suffix + ".pub").read_text().strip()

    def fingerprint(self) -> str:
        return " ".join(self.public_key_text().split()[:2])


@dataclass(frozen=True)
class GPGBackend:
    fpr: str
    key_type: Literal["gpg"] = "gpg"

    def sign(self, data: str) -> str:
        signed = gnupg.GPG().sign(data, keyid=self.fpr, detach=True)
        assert signed.data, f"GPG signing failed: {signed.status}"
        return str(signed)

    def public_key_text(self) -> str:
        return gnupg.GPG().export_keys(self.fpr)

    def fingerprint(self) -> str:
        return self.fpr


class KeyDiscovery:
    @staticmethod
    def has_tool(name: str) -> bool:
        return shutil.which(name) is not None

    @staticmethod
    def find_ssh_keys() -> tuple[SSHKeyInfo, ...]:
        return tuple(
            SSHKeyInfo(
                path=path,
                algorithm=parts[0] if len(parts := path.with_suffix(path.suffix + ".pub").read_text().strip().split()) >= 2 else "unknown",
                comment=parts[2] if len(parts) >= 3 else "",
            )
            for name in SSH_KEY_CANDIDATES
            if (path := SSH_DIR / name).exists()
        )

    @staticmethod
    def find_gpg_keys() -> tuple[GPGKeyInfo, ...]:
        if not KeyDiscovery.has_tool("gpg"):
            return ()
        return tuple(
            GPGKeyInfo(
                fpr=key["fingerprint"],
                email=next((uid.split("<")[-1].rstrip(">") for uid in key.get("uids", []) if "<" in uid), ""),
                algo=f"{key.get('algo', 'unknown')}{key.get('length', '')}",
            )
            for key in gnupg.GPG().list_keys(True)
        )

    @staticmethod
    def fetch_github_ssh_keys(username: str) -> tuple[str, ...]:
        response = httpx.get(f"https://github.com/{username}.keys", timeout=10.0)
        response.raise_for_status()
        return tuple(line.strip() for line in response.text.splitlines() if line.strip())

    @staticmethod
    def fetch_github_gpg_keys(username: str) -> str:
        response = httpx.get(f"https://github.com/{username}.gpg", timeout=10.0)
        return response.text if response.status_code == 200 else ""

    @staticmethod
    def fetch_openpgp_key(fpr: str) -> str | None:
        response = httpx.get(f"https://keys.openpgp.org/vks/v1/by-fingerprint/{fpr.upper()}", timeout=10.0)
        return response.text if response.status_code == 200 else None

    @staticmethod
    def upload_openpgp_key(armored_key: str) -> tuple[str, dict[str, str]]:
        response = httpx.post(
            "https://keys.openpgp.org/vks/v1/upload",
            json={"keytext": armored_key},
            timeout=30.0,
        )
        response.raise_for_status()
        data = response.json()
        return data["token"], data["status"]

    @staticmethod
    def request_openpgp_verify(token: str, addresses: list[str]) -> dict[str, str]:
        response = httpx.post(
            "https://keys.openpgp.org/vks/v1/request-verify",
            json={"token": token, "addresses": addresses},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()["status"]

    @staticmethod
    def generate_ssh_key() -> SSHKeyInfo:
        path = SSH_DIR / "id_ed25519"
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", "", "-C", "cc-sentiment"],
            check=True, capture_output=True, timeout=10,
        )
        parts = path.with_suffix(path.suffix + ".pub").read_text().strip().split()
        return SSHKeyInfo(
            path=path,
            algorithm=parts[0] if len(parts) >= 2 else "unknown",
            comment=parts[2] if len(parts) >= 3 else "",
        )

    @staticmethod
    def upload_github_ssh_key(info: SSHKeyInfo) -> bool:
        pub_path = info.path.with_suffix(info.path.suffix + ".pub")
        result = subprocess.run(
            ["gh", "ssh-key", "add", str(pub_path), "-t", "cc-sentiment"],
            capture_output=True, text=True, timeout=30,
        )
        return result.returncode == 0

    @staticmethod
    def upload_github_gpg_key(info: GPGKeyInfo) -> bool:
        armored = gnupg.GPG().export_keys(info.fpr)
        if not armored:
            return False
        with tempfile.NamedTemporaryFile(mode="w", suffix=".asc", delete=False) as f:
            f.write(armored)
            tmp_path = Path(f.name)
        try:
            result = subprocess.run(
                ["gh", "gpg-key", "add", str(tmp_path)],
                capture_output=True, text=True, timeout=30,
            )
            return result.returncode == 0
        finally:
            tmp_path.unlink(missing_ok=True)

    @classmethod
    def match_ssh_key(cls, username: str) -> SSHBackend | None:
        github_keys = cls.fetch_github_ssh_keys(username)
        if not github_keys:
            return None
        for info in cls.find_ssh_keys():
            local_fp = SSHBackend(private_key_path=info.path).fingerprint()
            if any(" ".join(gk.split()[:2]) == local_fp for gk in github_keys):
                return SSHBackend(private_key_path=info.path)
        return None

    @staticmethod
    def parse_armored_fingerprints(armor: str) -> frozenset[str]:
        with tempfile.TemporaryDirectory(prefix="cc-sentiment-gpg-") as home:
            return frozenset(
                gnupg.GPG(gnupghome=home).scan_keys_mem(armor).fingerprints
            )

    @classmethod
    def match_gpg_key(cls, username: str) -> GPGBackend | None:
        armor = cls.fetch_github_gpg_keys(username)
        if not armor:
            return None
        github_fprs = cls.parse_armored_fingerprints(armor)
        return next(
            (GPGBackend(fpr=info.fpr) for info in cls.find_gpg_keys() if info.fpr in github_fprs),
            None,
        )

    @classmethod
    def gpg_key_on_github(cls, username: str, fpr: str) -> bool:
        armor = cls.fetch_github_gpg_keys(username)
        return bool(armor) and fpr in cls.parse_armored_fingerprints(armor)


class PayloadSigner:
    @staticmethod
    def canonical_json(records: list[SentimentRecord]) -> str:
        return json.dumps(
            [r.model_dump(mode="json", by_alias=True) for r in records],
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def sign(data: str, backend: SigningBackend) -> str:
        return backend.sign(data)

    @classmethod
    def sign_records(cls, records: list[SentimentRecord], backend: SigningBackend) -> str:
        return cls.sign(cls.canonical_json(records), backend)

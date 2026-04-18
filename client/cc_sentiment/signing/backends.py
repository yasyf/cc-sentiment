from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import gnupg

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

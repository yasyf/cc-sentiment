from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

import anyio
import anyio.to_thread
import gnupg

NAMESPACE = "cc-sentiment"


class SigningBackend(Protocol):
    key_type: Literal["ssh", "gpg"]

    async def sign(self, data: str) -> str: ...
    async def public_key_text(self) -> str: ...
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

    def _pub_path(self) -> Path:
        return self.private_key_path.with_suffix(self.private_key_path.suffix + ".pub")

    async def sign(self, data: str) -> str:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(data)
            data_path = Path(f.name)
        try:
            with anyio.fail_after(10):
                await anyio.run_process(
                    ["ssh-keygen", "-Y", "sign", "-f", str(self.private_key_path), "-n", NAMESPACE, str(data_path)],
                    check=True,
                )
            sig_path = Path(f"{data_path}.sig")
            signature = sig_path.read_text()
            sig_path.unlink()
            return signature
        finally:
            data_path.unlink(missing_ok=True)

    async def public_key_text(self) -> str:
        return self._pub_path().read_text().strip()

    def fingerprint(self) -> str:
        return " ".join(self._pub_path().read_text().split()[:2])


@dataclass(frozen=True)
class GPGBackend:
    fpr: str
    key_type: Literal["gpg"] = "gpg"

    async def sign(self, data: str) -> str:
        signed = await anyio.to_thread.run_sync(self._sign_sync, data)
        assert signed.data, f"GPG signing failed: {signed.status}"
        return str(signed)

    def _sign_sync(self, data: str):
        return gnupg.GPG().sign(data, keyid=self.fpr, detach=True)

    async def public_key_text(self) -> str:
        return await anyio.to_thread.run_sync(lambda: gnupg.GPG().export_keys(self.fpr))

    def fingerprint(self) -> str:
        return self.fpr

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import anyio
import anyio.to_thread
import gnupg
import httpx

from cc_sentiment.signing.backends import GPGKeyInfo, SSHKeyInfo

SSH_DIR = Path.home() / ".ssh"
SSH_KEY_CANDIDATES = ("id_ed25519", "id_rsa")

CC_SENTIMENT_KEY_DIR = Path.home() / ".cc-sentiment" / "keys"
GIST_KEY_NAME = "id_ed25519"
GIST_PUB_FILENAME = "cc-sentiment.pub"
GIST_README_FILENAME = "README.md"
GIST_DESCRIPTION = "cc-sentiment public key"
GIST_README_TEMPLATE = """\
# cc-sentiment signing key

This gist lets sentiments.cc verify uploads from [cc-sentiment](https://github.com/yasyf/cc-sentiment).

If you didn't set this up, you can delete this gist.
"""


class KeyDiscovery:
    @staticmethod
    def has_tool(name: str) -> bool:
        return shutil.which(name) is not None

    @staticmethod
    def ssh_key_info(path: Path, default_comment: str = "") -> SSHKeyInfo | None:
        pub_path = path.with_suffix(path.suffix + ".pub")
        if not path.exists() or not pub_path.exists():
            return None
        parts = pub_path.read_text().strip().split()
        if not parts:
            return None
        return SSHKeyInfo(
            path=path,
            algorithm=parts[0] if len(parts) >= 2 else "unknown",
            comment=parts[2] if len(parts) >= 3 else default_comment,
        )

    @staticmethod
    def find_ssh_keys() -> tuple[SSHKeyInfo, ...]:
        return tuple(
            info
            for name in SSH_KEY_CANDIDATES
            if (info := KeyDiscovery.ssh_key_info(SSH_DIR / name)) is not None
        )

    @staticmethod
    async def find_gpg_keys() -> tuple[GPGKeyInfo, ...]:
        if not KeyDiscovery.has_tool("gpg"):
            return ()
        keys = await anyio.to_thread.run_sync(lambda: gnupg.GPG().list_keys(True))
        return tuple(
            GPGKeyInfo(
                fpr=key["fingerprint"],
                email=next((uid.split("<")[-1].rstrip(">") for uid in key.get("uids", []) if "<" in uid), ""),
                algo=f"{key.get('algo', 'unknown')}{key.get('length', '')}",
            )
            for key in keys
        )

    @staticmethod
    async def upload_openpgp_key(armored_key: str) -> tuple[str, dict[str, str]]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://keys.openpgp.org/vks/v1/upload",
                json={"keytext": armored_key},
            )
        response.raise_for_status()
        data = response.json()
        return data["token"], data["status"]

    @staticmethod
    async def request_openpgp_verify(token: str, addresses: list[str]) -> dict[str, str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://keys.openpgp.org/vks/v1/request-verify",
                json={"token": token, "addresses": addresses},
            )
        response.raise_for_status()
        return response.json()["status"]

    @staticmethod
    async def upload_github_ssh_key(info: SSHKeyInfo) -> bool:
        pub_path = info.path.with_suffix(info.path.suffix + ".pub")
        with anyio.fail_after(30):
            result = await anyio.run_process(
                ["gh", "ssh-key", "add", str(pub_path), "-t", "cc-sentiment"],
                check=False,
            )
        return result.returncode == 0

    @staticmethod
    async def upload_github_gpg_key(info: GPGKeyInfo) -> bool:
        armored = await anyio.to_thread.run_sync(lambda: gnupg.GPG().export_keys(info.fpr))
        if not armored:
            return False
        with tempfile.NamedTemporaryFile(mode="w", suffix=".asc", delete=False) as f:
            f.write(armored)
            tmp_path = Path(f.name)
        try:
            with anyio.fail_after(30):
                result = await anyio.run_process(
                    ["gh", "gpg-key", "add", str(tmp_path)],
                    check=False,
                )
            return result.returncode == 0
        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    async def gh_authenticated() -> bool:
        if not KeyDiscovery.has_tool("gh"):
            return False
        with anyio.fail_after(10):
            result = await anyio.run_process(["gh", "auth", "status"], check=False)
        return result.returncode == 0

    @staticmethod
    async def gh_login() -> str | None:
        if not KeyDiscovery.has_tool("gh"):
            return None
        with anyio.fail_after(10):
            result = await anyio.run_process(
                ["gh", "api", "user", "--jq", ".login"],
                check=False,
            )
        if result.returncode != 0:
            return None
        return result.stdout.decode().strip() or None

    @staticmethod
    async def gh_primary_email() -> str | None:
        if not KeyDiscovery.has_tool("gh"):
            return None
        with anyio.fail_after(10):
            result = await anyio.run_process(
                [
                    "gh", "api", "user/emails",
                    "--jq", ".[] | select(.primary and .verified) | .email",
                ],
                check=False,
            )
        if result.returncode != 0:
            return None
        return next((line.strip() for line in result.stdout.decode().splitlines() if line.strip()), None)

    @staticmethod
    def gh_auth_login_interactive() -> bool:
        # Interactive; must run synchronously to share TTY with the user.
        if not KeyDiscovery.has_tool("gh"):
            return False
        result = subprocess.run(["gh", "auth", "login"], timeout=600)
        return result.returncode == 0

    @staticmethod
    def has_brew() -> bool:
        return KeyDiscovery.has_tool("brew")

    @staticmethod
    async def install_with_brew(package: str) -> tuple[bool, str]:
        with anyio.fail_after(600):
            result = await anyio.run_process(
                ["brew", "install", package],
                check=False,
            )
        return (
            result.returncode == 0,
            (result.stderr.decode() or result.stdout.decode()).strip(),
        )

    @staticmethod
    def is_noreply_email(email: str) -> bool:
        return "noreply.github.com" in (email or "").lower()

    @staticmethod
    async def fetch_latest_public_repo(username: str) -> str | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.github.com/users/{username}/repos",
                params={"sort": "pushed", "per_page": "5", "type": "owner"},
            )
        if response.status_code != 200:
            return None
        return next(
            (repo["name"] for repo in response.json() if not repo.get("fork") and not repo.get("private")),
            None,
        )

    @staticmethod
    async def fetch_commit_email(username: str, repo: str) -> str | None:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"https://api.github.com/repos/{username}/{repo}/commits",
                params={"author": username, "per_page": "10"},
            )
        if response.status_code != 200:
            return None
        for commit in response.json():
            email = commit.get("commit", {}).get("author", {}).get("email", "")
            if email and not KeyDiscovery.is_noreply_email(email):
                return email
        return None

    @staticmethod
    def find_gist_keypair() -> Path | None:
        key = CC_SENTIMENT_KEY_DIR / GIST_KEY_NAME
        pub = key.with_suffix(key.suffix + ".pub")
        return key if key.exists() and pub.exists() else None

    @staticmethod
    async def generate_managed_ssh_key() -> SSHKeyInfo:
        if (existing := KeyDiscovery.find_gist_keypair()) is not None:
            path = existing
        else:
            CC_SENTIMENT_KEY_DIR.mkdir(parents=True, exist_ok=True)
            path = CC_SENTIMENT_KEY_DIR / GIST_KEY_NAME
            with anyio.fail_after(10):
                await anyio.run_process(
                    ["ssh-keygen", "-t", "ed25519", "-f", str(path), "-N", "", "-C", "cc-sentiment"],
                    check=True,
                )
        info = KeyDiscovery.ssh_key_info(path, "cc-sentiment")
        assert info is not None
        return info

    @staticmethod
    async def create_gist_from_text(pub_text: str) -> str:
        with tempfile.TemporaryDirectory(prefix="cc-sentiment-gist-") as tmpdir:
            tmp = Path(tmpdir)
            pub_file = tmp / GIST_PUB_FILENAME
            readme_file = tmp / GIST_README_FILENAME
            pub_file.write_text(pub_text if pub_text.endswith("\n") else pub_text + "\n")
            readme_file.write_text(GIST_README_TEMPLATE)
            with anyio.fail_after(30):
                result = await anyio.run_process(
                    ["gh", "gist", "create", "--public", "-d", GIST_DESCRIPTION, str(pub_file), str(readme_file)],
                    check=True,
                )
        url = result.stdout.decode().strip().splitlines()[-1].strip()
        return url.rsplit("/", 1)[-1]

    @staticmethod
    async def generate_managed_gpg_key(identity: str, email: str) -> GPGKeyInfo:
        before = {key.fpr for key in await KeyDiscovery.find_gpg_keys()}
        batch = (
            "%no-protection\n"
            "Key-Type: eddsa\n"
            "Key-Curve: ed25519\n"
            f"Name-Real: {identity}\n"
            f"Name-Email: {email}\n"
            "Expire-Date: 0\n"
            "%commit\n"
        )
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt") as f:
            f.write(batch)
            f.flush()
            with anyio.fail_after(60):
                await anyio.run_process(
                    ["gpg", "--batch", "--gen-key", f.name],
                    check=True,
                )
        new_keys = [key for key in await KeyDiscovery.find_gpg_keys() if key.fpr not in before]
        assert new_keys, "GPG key generated but not found in keyring"
        return new_keys[0]

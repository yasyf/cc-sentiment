from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field

import anyio
import anyio.to_thread
import httpx
from textual.widgets import Static

from cc_sentiment.models import (
    AppState,
    ContributorId,
    GistConfig,
    GPGConfig,
    SSHConfig,
)
from cc_sentiment.signing import GPGKeyInfo, KeyDiscovery
from cc_sentiment.tui.widgets import PendingStatus


@dataclass(frozen=True)
class Phase:
    emitter: StatusEmitter
    idx: int

    def ok(self, label: str) -> None:
        self.emitter.replace(self.idx, "[green]✓[/]", label)

    def skip(self, label: str) -> None:
        self.emitter.replace(self.idx, "[yellow]—[/]", label)

    def unreachable(self, label: str) -> None:
        self.emitter.replace(self.idx, "[yellow]?[/]", label)


@dataclass
class StatusEmitter:
    widget: Static | PendingStatus
    lines: list[str] = field(default_factory=list)

    def update(self) -> None:
        text = "\n".join(self.lines)
        match self.widget:
            case PendingStatus():
                self.widget.label = text
            case _:
                self.widget.update(text)

    def begin(self, label: str) -> Phase:
        self.lines.append(f"  [dim]...[/] [dim]{label}[/]")
        self.update()
        return Phase(self, len(self.lines) - 1)

    def replace(self, idx: int, marker: str, label: str) -> None:
        self.lines[idx] = f"  {marker} [dim]{label}[/]"
        self.update()


@dataclass(frozen=True)
class AutoSetup:
    state: AppState
    emit: StatusEmitter

    async def run(self) -> tuple[bool, str | None]:
        username = await self.detect_username()
        if username:
            if (c := await self.try_github_ssh(username)) and await self.probe_and_save(c):
                return True, username
            if (c := await self.try_github_gpg(username)) and await self.probe_and_save(c):
                return True, username
            if (c := await self.try_existing_gist(username)) and await self.probe_and_save(c):
                return True, username
        for info in await self.find_local_gpg():
            if (c := await self.try_openpgp(info, username)) and await self.probe_and_save(c):
                return True, username
        return False, username

    @staticmethod
    def find_git_username() -> str | None:
        for cmd in (
            ["gh", "api", "user", "--jq", ".login"],
            ["git", "config", "github.user"],
        ):
            if not shutil.which(cmd[0]):
                continue
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                continue
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        return None

    async def detect_username(self) -> str | None:
        phase = self.emit.begin("Looking for your GitHub username")
        username = await anyio.to_thread.run_sync(self.find_git_username)
        if not username:
            phase.skip("No GitHub username on this machine")
            return None
        phase.ok(f"Found @{username}")
        return username

    async def try_github_ssh(self, username: str) -> SSHConfig | None:
        phase = self.emit.begin(f"Looking for SSH keys you've published at github.com/{username}.keys")
        try:
            backend = await anyio.to_thread.run_sync(KeyDiscovery.match_ssh_key, username)
        except httpx.HTTPError:
            phase.unreachable("Couldn't reach GitHub")
            return None
        if not backend:
            phase.skip("No matching SSH key on GitHub")
            return None
        phase.ok(f"Matched your local SSH key {backend.private_key_path.name}")
        return SSHConfig(
            contributor_id=ContributorId(username),
            key_path=backend.private_key_path,
        )

    async def try_github_gpg(self, username: str) -> GPGConfig | None:
        phase = self.emit.begin(f"Looking for GPG keys you've published at github.com/{username}.gpg")
        try:
            backend = await anyio.to_thread.run_sync(KeyDiscovery.match_gpg_key, username)
        except httpx.HTTPError:
            phase.unreachable("Couldn't reach GitHub")
            return None
        if not backend:
            phase.skip("No matching GPG key on GitHub")
            return None
        phase.ok("Matched your local GPG key")
        return GPGConfig(
            contributor_type="github",
            contributor_id=ContributorId(username),
            fpr=backend.fpr,
        )

    async def try_existing_gist(self, username: str) -> GistConfig | None:
        phase = self.emit.begin("Checking for a cc-sentiment gist")
        key_path = await anyio.to_thread.run_sync(KeyDiscovery.find_gist_keypair)
        if key_path is None:
            phase.skip("No cc-sentiment key on this Mac")
            return None
        if not await anyio.to_thread.run_sync(KeyDiscovery.gh_authenticated):
            phase.skip("gh CLI not authenticated")
            return None
        gist_id = await anyio.to_thread.run_sync(KeyDiscovery.find_cc_sentiment_gist_id)
        if gist_id is None:
            phase.skip("No cc-sentiment gist on this account")
            return None
        phase.ok("Found your cc-sentiment gist")
        return GistConfig(
            contributor_id=ContributorId(username),
            key_path=key_path,
            gist_id=gist_id,
        )

    async def find_local_gpg(self) -> tuple[GPGKeyInfo, ...]:
        phase = self.emit.begin("Looking through your local GPG keys")
        keys = await anyio.to_thread.run_sync(KeyDiscovery.find_gpg_keys)
        if not keys:
            phase.skip("No local GPG keys")
            return ()
        plural = "s" if len(keys) != 1 else ""
        phase.ok(f"Found {len(keys)} GPG key{plural}")
        return keys

    async def try_openpgp(
        self, info: GPGKeyInfo, username: str | None
    ) -> GPGConfig | None:
        phase = self.emit.begin("Checking public keyservers")
        try:
            armored = await anyio.to_thread.run_sync(
                KeyDiscovery.fetch_openpgp_key, info.fpr
            )
        except httpx.HTTPError:
            phase.unreachable("Couldn't reach public keyservers")
            return None
        if not armored:
            phase.skip("Not on a public keyserver yet")
            return None
        phase.ok("Published on a public keyserver")
        return (
            GPGConfig(contributor_type="github", contributor_id=ContributorId(username), fpr=info.fpr)
            if username
            else GPGConfig(contributor_type="gpg", contributor_id=ContributorId(info.fpr), fpr=info.fpr)
        )

    async def probe_and_save(self, config: SSHConfig | GPGConfig | GistConfig) -> bool:
        from cc_sentiment.upload import AuthOk, Uploader
        phase = self.emit.begin("Checking with sentiments.cc")
        result = await Uploader().probe_credentials(config)
        if not isinstance(result, AuthOk):
            phase.skip("sentiments.cc can't see this key yet")
            return False
        phase.ok("Verified by sentiments.cc")
        self.state.config = config
        await anyio.to_thread.run_sync(self.state.save)
        return True

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from cc_sentiment.models import AppState
from tests.tuistory.harness import HarnessResult, HarnessRunner

USERNAME = "alice"
VERIFY_URL = "https://anetaco--cc-sentiment-api-serve.modal.run/verify"
SNAPSHOT_ROOT = Path(__file__).parent / "__snapshots__"
SPINNER_PATTERN = re.compile("[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")
KEY_MATERIAL_PATTERN = re.compile("[A-Za-z0-9+/]{12,}")
ELAPSED_PATTERN = re.compile(r"Waiting for your key to propagate… \d+:\d{2}")
FINGERPRINT_HEAD_PATTERN = re.compile(r"GPG [0-9A-F]{4} [0-9A-F]{4} \.\.\. [0-9A-F]{4}")
FINGERPRINT_TAIL_PATTERN = re.compile(r"(?m)^(\s*┃\s*│\s*)[0-9A-F]{4}\.(\s*│\s*┃)$")
SSH_KEY_PATTERN = re.compile(r"AAAAC3NzaC1lZDI1NTE5[A-Za-z0-9+/=]+\s+[A-Za-z0-9+/=]+")


def normalize_snapshot(text: str) -> str:
    return ELAPSED_PATTERN.sub(
        "Waiting for your key to propagate… <ELAPSED>",
        FINGERPRINT_TAIL_PATTERN.sub(
            r"\1<FPR>.\2",
            FINGERPRINT_HEAD_PATTERN.sub(
                "GPG <FPR>",
                SSH_KEY_PATTERN.sub(
                    "<SSH_KEY>",
                    KEY_MATERIAL_PATTERN.sub("<KEY>", SPINNER_PATTERN.sub("⠋", text)),
                ),
            ),
        ),
    )


def snapshot_path(scenario: str, step: str) -> Path:
    return SNAPSHOT_ROOT / scenario / f"{step}_80x24.txt"


def assert_snapshot(result: HarnessResult, scenario: str, step: str) -> None:
    expected_path = snapshot_path(scenario, step)
    assert expected_path.exists(), f"Missing golden snapshot: {expected_path}"
    assert normalize_snapshot(result.snapshot(step)) == normalize_snapshot(expected_path.read_text())


def assert_steps(result: HarnessResult, expected_steps: tuple[str, ...]) -> None:
    assert tuple(result.state["snapshots"]) == expected_steps


def matched_requests(result: HarnessResult) -> list[tuple[str, str, int]]:
    return [
        (str(entry["method"]), str(entry["url"]), int(entry["status"]))
        for entry in result.addon_requests()
        if entry.get("matched")
    ]


def fake_commands(result: HarnessResult, tool: str) -> list[dict[str, object]]:
    return [entry for entry in result.fake_commands() if entry["tool"] == tool]


def make_keypair(tmp_path: Path, relative_dir: str) -> tuple[dict[str, dict[str, object]], str]:
    seed_dir = tmp_path / "seed" / relative_dir.replace("/", "_")
    seed_dir.mkdir(parents=True, exist_ok=True)
    key_path = seed_dir / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", "cc-sentiment"],
        check=True,
        capture_output=True,
        text=True,
    )
    private_text = key_path.read_text()
    public_text = key_path.with_suffix(".pub").read_text()
    prefix = f"{relative_dir}/id_ed25519"
    return {
        prefix: {"content": private_text, "mode": 0o600},
        f"{prefix}.pub": {"content": public_text, "mode": 0o644},
    }, public_text


def generate_gpg_key(home_dir: Path, email: str) -> str:
    gnupg_home = home_dir / ".gnupg"
    shutil.rmtree(gnupg_home, ignore_errors=True)
    gnupg_home.mkdir(parents=True, exist_ok=True)
    gnupg_home.chmod(0o700)
    env = {**os.environ, "HOME": str(home_dir), "GNUPGHOME": str(gnupg_home)}
    subprocess.run(
        [
            "gpg",
            "--batch",
            "--pinentry-mode",
            "loopback",
            "--passphrase",
            "",
            "--quick-generate-key",
            f"Alice Example <{email}>",
            "ed25519",
            "sign",
            "0",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    listing = subprocess.run(
        ["gpg", "--with-colons", "--list-secret-keys", email],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    ).stdout.splitlines()
    return next(line.split(":")[9] for line in listing if line.startswith("fpr:"))


def prepare_fake_bin_dir(
    harness: HarnessRunner,
    tmp_path: Path,
    slug: str,
    exclude: tuple[str, ...],
) -> Path:
    if not exclude:
        return harness.fake_bin_dir
    fake_bin_dir = tmp_path / slug / "fake-bin"
    fake_bin_dir.mkdir(parents=True, exist_ok=True)
    for source in harness.fake_bin_dir.iterdir():
        if source.name in exclude:
            continue
        target = fake_bin_dir / source.name
        shutil.copy2(source, target)
        target.chmod(source.stat().st_mode)
    return fake_bin_dir


def run_scenario(
    harness: HarnessRunner,
    tmp_path: Path,
    scenario_name: str,
    scenario: dict[str, object],
    *,
    output_slug: str | None = None,
    home_dir: Path | None = None,
    size: str = "80x24",
) -> HarnessResult:
    slug = HarnessRunner.sanitize(output_slug or scenario_name)
    output_dir = tmp_path / slug / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    home_dir = home_dir or (tmp_path / slug / "home")
    home_dir.mkdir(parents=True, exist_ok=True)
    HarnessRunner.write_home_files(home_dir, scenario)
    fake_bin_dir = prepare_fake_bin_dir(
        harness,
        tmp_path,
        slug,
        tuple(str(name) for name in tuple(scenario.get("fake_bin_exclude", ()))),
    )
    scenario_data = {
        **scenario,
        "addon_log_path": str(output_dir / "addon.jsonl"),
    }
    harness.mitm.scenario_path.write_text(json.dumps(scenario_data))
    completed = subprocess.run(
        [
            str(harness.wrapper_path),
            HarnessRunner.sanitize(scenario_name),
            str(output_dir),
            size,
            str(harness.mitm.port),
            str(harness.mitm.confdir),
            str(fake_bin_dir),
            str(home_dir),
            str(harness.mitm.scenario_path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=30,
    )
    state_path = output_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "session": slug,
        "size": size,
        "snapshots": {},
        "commands": [],
        "captures": {},
        "app_exit": None,
        "error": {"type": "missing-state", "message": "wrapper did not write state.json"},
    }
    harness.mitm.scenario_path.write_text("{}")
    return HarnessResult(completed=completed, output_dir=output_dir, state=state)


def assert_success(result: HarnessResult) -> None:
    assert result.returncode == 0, result.completed.stderr or result.completed.stdout
    assert result.state["error"] is None
    assert result.app_returncode == 0


def assert_no_setup_processes(result: HarnessResult) -> None:
    commands = subprocess.run(
        ["ps", "-ax", "-o", "command="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    home_root = str(result.output_dir.parent)
    assert not [line for line in commands if "uv run cc-sentiment setup" in line and home_root in line]


def state_path_for(home_dir: Path) -> Path:
    return home_dir / ".cc-sentiment" / "state.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def pending_seconds(text: str) -> int:
    match = re.search(r"Waiting for your key to propagate… (\d+):(\d{2})", text)
    assert match is not None, text
    return int(match.group(1)) * 60 + int(match.group(2))


def test_openpgp_publish_pending_resume(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "openpgp-publish-pending-resume"
    home_dir = Path(tempfile.mkdtemp(prefix="ccg-", dir="/tmp"))
    fingerprint = generate_gpg_key(home_dir, "alice@example.com")
    first_started = time.monotonic()
    first = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Who are you?", "timeout_ms": 10000},
                {"action": "type", "text": USERNAME},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "GPG ·", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Your key isn't linked yet", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Publish to keys.openpgp.org", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-upload-options"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "02-pending-t0"},
                {"action": "sleep", "seconds": 2.1},
                {"action": "snapshot", "name": "03-pending-t2"},
                {"action": "press", "keys": ("escape",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://api.github.com/users/{USERNAME}"},
                    "responses": ({"status": 200, "json": {"login": USERNAME}},),
                },
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.gpg"},
                    "responses": ({"status": 200, "text": ""},),
                },
                {
                    "match": {
                        "method": "GET",
                        "url": f"https://keys.openpgp.org/vks/v1/by-fingerprint/{fingerprint}",
                    },
                    "responses": ({"status": 404, "text": ""},),
                },
                {
                    "match": {"method": "POST", "url": "https://keys.openpgp.org/vks/v1/upload"},
                    "responses": ({
                        "status": 200,
                        "json": {"token": "publish-token", "status": {"alice@example.com": "unpublished"}},
                    },),
                },
                {
                    "match": {"method": "POST", "url": "https://keys.openpgp.org/vks/v1/request-verify"},
                    "responses": ({
                        "status": 200,
                        "json": {"status": {"alice@example.com": "pending"}},
                    },),
                },
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": ({"status": 401, "json": {}},),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("auth", "status"), "stderr": "not logged in\n", "returncode": 1},
                ),
            },
            "fake_bin_exclude": ("gpg",),
        },
        output_slug="openpgp-first",
        home_dir=home_dir,
    )
    first_elapsed = time.monotonic() - first_started

    assert_success(first)
    assert first_elapsed < 30
    assert_steps(first, ("01-upload-options", "02-pending-t0", "03-pending-t2"))
    for step in ("01-upload-options", "02-pending-t0", "03-pending-t2"):
        assert_snapshot(first, scenario_name, step)
    upload_options = first.snapshot("01-upload-options")
    assert "Publish to keys.openpgp.org" in upload_options
    assert "Show me the key; I'll add it myself" in upload_options
    assert "Link via GitHub (gh)" not in upload_options
    pending_t0 = first.snapshot("02-pending-t0")
    pending_t2 = first.snapshot("03-pending-t2")
    assert "Contribute my stats" not in pending_t0
    assert "Contribute my stats" not in pending_t2
    assert pending_seconds(pending_t2) > pending_seconds(pending_t0)
    assert matched_requests(first) == [
        ("GET", f"https://keys.openpgp.org/vks/v1/by-fingerprint/{fingerprint}", 404),
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
        ("GET", f"https://github.com/{USERNAME}.gpg", 200),
        ("GET", f"https://keys.openpgp.org/vks/v1/by-fingerprint/{fingerprint}", 404),
        ("POST", "https://keys.openpgp.org/vks/v1/upload", 200),
        ("POST", "https://keys.openpgp.org/vks/v1/request-verify", 200),
        ("POST", VERIFY_URL, 401),
    ]
    config = AppState.model_validate_json(state_path_for(home_dir).read_text()).config
    assert config is not None
    assert config.key_type == "gpg"
    assert str(config.fpr) == fingerprint
    first_hash = sha256(state_path_for(home_dir))

    second_started = time.monotonic()
    second = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 10000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "04-post-escape-rerun-pending"},
                {"action": "press", "keys": ("escape",)},
            ),
            "http": (
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": ({"status": 401, "json": {}},),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("auth", "status"), "stderr": "not logged in\n", "returncode": 1},
                ),
            },
            "fake_bin_exclude": ("gpg",),
        },
        output_slug="openpgp-second",
        home_dir=home_dir,
    )
    second_elapsed = time.monotonic() - second_started

    assert_success(second)
    assert second_elapsed < 30
    assert_steps(second, ("04-post-escape-rerun-pending",))
    assert_snapshot(second, scenario_name, "04-post-escape-rerun-pending")
    rerun_snapshot = second.snapshot("04-post-escape-rerun-pending")
    assert "GitHub username" not in rerun_snapshot
    assert "Contribute my stats" not in rerun_snapshot
    assert matched_requests(second) == [("POST", VERIFY_URL, 401)]
    assert sha256(state_path_for(home_dir)) == first_hash
    assert_no_setup_processes(first)
    assert_no_setup_processes(second)


def test_gh_link_failed(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "gh-link-failed"
    home_files, _ = make_keypair(tmp_path, ".ssh")
    state_hash_key = "failed-state"
    started = time.monotonic()
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name=scenario_name,
        scenario={
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Who are you?", "timeout_ms": 10000},
                {"action": "type", "text": USERNAME},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Your key isn't linked yet", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Link via GitHub (gh)", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "We couldn't verify your key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-failed"},
                {
                    "action": "capture-file",
                    "name": state_hash_key,
                    "path": str(tmp_path / HarnessRunner.sanitize(scenario_name) / "home" / ".cc-sentiment" / "state.json"),
                },
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Link via GitHub (gh)", "timeout_ms": 10000},
                {"action": "snapshot", "name": "02-retry-upload-no-dup"},
                {
                    "action": "capture-file",
                    "name": f"{state_hash_key}-after-retry",
                    "path": str(tmp_path / HarnessRunner.sanitize(scenario_name) / "home" / ".cc-sentiment" / "state.json"),
                },
                {"action": "press", "keys": ("escape",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://api.github.com/users/{USERNAME}"},
                    "responses": ({"status": 200, "json": {"login": USERNAME}},),
                },
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                    "responses": ({"status": 200, "text": ""},),
                },
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": ({"status": 401, "json": {}},),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("auth", "status"), "stdout": "", "returncode": 0},
                    {
                        "argv_prefix": ("ssh-key", "add"),
                        "stderr": "HTTP 422: key already exists\n",
                        "returncode": 1,
                    },
                ),
            },
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
    )
    elapsed = time.monotonic() - started

    assert_success(result)
    assert elapsed < 30
    assert_steps(result, ("01-failed", "02-retry-upload-no-dup"))
    assert_snapshot(result, scenario_name, "01-failed")
    assert_snapshot(result, scenario_name, "02-retry-upload-no-dup")
    failed_snapshot = result.snapshot("01-failed")
    assert "HTTP 422: key already exists" in failed_snapshot
    assert "Retry" in failed_snapshot
    assert "Contribute my stats" not in failed_snapshot
    retry_snapshot = result.snapshot("02-retry-upload-no-dup")
    assert retry_snapshot.count("Link via GitHub (gh)") == 1
    assert retry_snapshot.count("Show me the key; I'll add it myself") == 1
    assert matched_requests(result) == [
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
    ]
    gh_commands = fake_commands(result, "gh")
    ssh_add = next(command for command in gh_commands if command["argv"][:2] == ["ssh-key", "add"])
    assert ssh_add["returncode"] == 1
    assert ssh_add["stderr"] == "HTTP 422: key already exists\n"
    state_path = result.output_dir.parent / "home" / ".cc-sentiment" / "state.json"
    parsed = AppState.model_validate_json(state_path.read_text())
    assert parsed.config is not None
    assert parsed.config.key_type == "ssh"
    assert str(parsed.config.key_path).endswith("/.ssh/id_ed25519")
    captures = dict(result.state.get("captures", {}))
    assert captures[state_hash_key]["sha256"] == captures[f"{state_hash_key}-after-retry"]["sha256"]
    assert_no_setup_processes(result)

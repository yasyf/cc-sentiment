from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from tests.tuistory.harness import HarnessResult, HarnessRunner

USERNAME = "alice"
VERIFY_URL = "https://anetaco--cc-sentiment-api-serve.modal.run/verify"
SNAPSHOT_ROOT = Path(__file__).parent / "__snapshots__"
SPINNER_PATTERN = re.compile("[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")
KEY_MATERIAL_PATTERN = re.compile("[A-Za-z0-9+/]{12,}")


def normalize_snapshot(text: str) -> str:
    return KEY_MATERIAL_PATTERN.sub("<KEY>", SPINNER_PATTERN.sub("⠋", text))


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


def state_config(result: HarnessResult) -> dict[str, object]:
    state_path = result.output_dir.parent / "home" / ".cc-sentiment" / "state.json"
    assert state_path.exists(), f"Missing persisted state: {state_path}"
    return json.loads(state_path.read_text())["config"]


def assert_no_setup_processes(result: HarnessResult) -> None:
    commands = subprocess.run(
        ["ps", "-ax", "-o", "command="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    home_root = str(result.output_dir.parent)
    assert not [line for line in commands if "uv run cc-sentiment setup" in line and home_root in line]


def assert_success(result: HarnessResult) -> None:
    assert result.returncode == 0, result.completed.stderr or result.completed.stdout
    assert result.state["error"] is None
    assert result.app_returncode == 0


def test_auto_setup_success_happy_path(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "auto-setup-success"
    home_files, public_text = make_keypair(tmp_path, ".ssh")
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name=scenario_name,
        scenario={
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "sleep", "seconds": 0.3},
                {"action": "wait", "pattern": "Checking SSH keys on github.com/alice.keys", "timeout_ms": 5000},
                {"action": "snapshot", "name": "loading"},
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "snapshot", "name": "done"},
                {"action": "press", "keys": ("enter",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                    "responses": ({"status": 200, "text": public_text, "delay_ms": 600},),
                },
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": ({"status": 200, "json": {}},),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                ),
            },
            "home_files": home_files,
        },
    )

    assert_success(result)
    assert_steps(result, ("loading", "done"))
    assert_snapshot(result, scenario_name, "loading")
    assert_snapshot(result, scenario_name, "done")
    assert "Contribute my stats" in result.snapshot("done")
    assert matched_requests(result) == [
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("POST", VERIFY_URL, 200),
    ]
    config = state_config(result)
    assert config["key_type"] == "ssh"
    assert config["contributor_type"] == "github"
    assert config["contributor_id"] == USERNAME
    assert str(config["key_path"]).endswith("/.ssh/id_ed25519")
    gh_commands = fake_commands(result, "gh")
    assert [command["argv"] for command in gh_commands] == [["api", "user", "--jq", ".login"]]
    assert gh_commands[0]["returncode"] == 0
    assert gh_commands[0]["stdout"] == f"{USERNAME}\n"
    assert_no_setup_processes(result)


def test_ssh_via_gh_link_happy_path(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "ssh-via-gh-link"
    home_files, _ = make_keypair(tmp_path, ".ssh")
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name=scenario_name,
        scenario={
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Looking for your GitHub username", "timeout_ms": 5000},
                {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                {"action": "snapshot", "name": "username"},
                {"action": "click", "pattern": "Next", "timeout_ms": 5000},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                {"action": "snapshot", "name": "discovery"},
                {"action": "click", "pattern": "Next", "timeout_ms": 5000},
                {"action": "wait", "pattern": "Your key isn't linked yet", "timeout_ms": 10000},
                {"action": "snapshot", "name": "remote"},
                {"action": "click", "pattern": "Next", "timeout_ms": 5000},
                {"action": "wait", "pattern": "Link via GitHub (gh)", "timeout_ms": 10000},
                {"action": "snapshot", "name": "link"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "snapshot", "name": "done"},
                {"action": "press", "keys": ("enter",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                    "responses": ({"status": 200, "text": "", "delay_ms": 600}, {"status": 200, "text": ""}),
                },
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.gpg"},
                    "responses": ({"status": 200, "text": ""},),
                },
                {
                    "match": {"method": "GET", "url": f"https://api.github.com/users/{USERNAME}"},
                    "responses": ({"status": 200, "json": {"login": USERNAME}},),
                },
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": ({"status": 200, "json": {}},),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                    {"argv": ("auth", "status"), "stdout": "", "returncode": 0},
                    {
                        "argv_prefix": ("ssh-key", "add"),
                        "stdout": "",
                        "returncode": 0,
                    },
                ),
            },
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
    )

    assert_success(result)
    assert_steps(result, ("username", "discovery", "remote", "link", "done"))
    for step in ("username", "discovery", "remote", "link", "done"):
        assert_snapshot(result, scenario_name, step)
    assert "Your key isn't linked yet" in result.snapshot("remote")
    assert "Contribute my stats" in result.snapshot("done")
    assert matched_requests(result) == [
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("GET", f"https://github.com/{USERNAME}.gpg", 200),
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("POST", VERIFY_URL, 200),
    ]
    gh_commands = fake_commands(result, "gh")
    assert [command["argv"] for command in gh_commands[:3]] == [
        ["api", "user", "--jq", ".login"],
        ["auth", "status"],
        ["auth", "status"],
    ]
    assert gh_commands[3]["argv"][:2] == ["ssh-key", "add"]
    assert gh_commands[3]["argv"][-2:] == ["-t", "cc-sentiment"]
    assert gh_commands[3]["returncode"] == 0
    config = state_config(result)
    assert config["key_type"] == "ssh"
    assert config["contributor_type"] == "github"
    assert config["contributor_id"] == USERNAME
    assert str(config["key_path"]).endswith("/.ssh/id_ed25519")
    assert_no_setup_processes(result)


def test_generate_gist_happy_path(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "generate-gist"
    gist_id = "1234567890abcdef1234567890abcdef"
    home_files, _ = make_keypair(tmp_path, ".cc-sentiment/keys")
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name=scenario_name,
        scenario={
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Looking for your GitHub username", "timeout_ms": 5000},
                {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                {"action": "snapshot", "name": "username"},
                {"action": "click", "pattern": "Next", "timeout_ms": 5000},
                {"action": "wait", "pattern": "Create a new cc-sentiment key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "discovery"},
                {"action": "click", "pattern": "Next", "timeout_ms": 5000},
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "snapshot", "name": "done"},
                {"action": "press", "keys": ("enter",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                    "responses": ({"status": 200, "text": "", "delay_ms": 600},),
                },
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.gpg"},
                    "responses": ({"status": 200, "text": ""},),
                },
                {
                    "match": {"method": "GET", "url": f"https://api.github.com/users/{USERNAME}"},
                    "responses": ({"status": 200, "json": {"login": USERNAME}},),
                },
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": ({"status": 200, "json": {}},),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                    {"argv": ("gist", "list", "--limit", "100"), "stdout": "", "returncode": 0},
                    {"argv": ("auth", "status"), "stdout": "", "returncode": 0},
                    {
                        "argv_prefix": ("gist", "create", "--public", "-d", "cc-sentiment public key"),
                        "stdout": f"https://gist.github.com/{USERNAME}/{gist_id}\n",
                        "returncode": 0,
                    },
                ),
            },
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
    )

    assert_success(result)
    assert_steps(result, ("username", "discovery", "done"))
    for step in ("username", "discovery", "done"):
        assert_snapshot(result, scenario_name, step)
    assert "Contribute my stats" in result.snapshot("done")
    assert gist_id[:7] in result.snapshot("done")
    assert matched_requests(result) == [
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("GET", f"https://github.com/{USERNAME}.gpg", 200),
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
        ("POST", VERIFY_URL, 200),
    ]
    gh_commands = fake_commands(result, "gh")
    assert [command["argv"] for command in gh_commands[:4]] == [
        ["api", "user", "--jq", ".login"],
        ["auth", "status"],
        ["gist", "list", "--limit", "100"],
        ["auth", "status"],
    ]
    assert gh_commands[4]["argv"][:5] == ["gist", "create", "--public", "-d", "cc-sentiment public key"]
    assert gh_commands[4]["returncode"] == 0
    config = state_config(result)
    assert config["key_type"] == "gist"
    assert config["contributor_type"] == "gist"
    assert config["contributor_id"] == USERNAME
    assert config["gist_id"] == gist_id
    assert str(config["key_path"]).endswith("/.cc-sentiment/keys/id_ed25519")
    assert_no_setup_processes(result)

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest

from cc_sentiment.models import AppState, ContributorId, SSHConfig
from tests.tuistory.harness import HarnessResult, HarnessRunner

USERNAME = "alice"
VERIFY_URL = "https://anetaco--cc-sentiment-api-serve.modal.run/verify"
GPG_EMAIL = "a@b.co"
SNAPSHOT_ROOT = Path(__file__).parent / "__snapshots__"
SPINNER_PATTERN = re.compile("[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")
KEY_MATERIAL_PATTERN = re.compile("[A-Za-z0-9+/]{12,}")
ELAPSED_PATTERN = re.compile(r"Waiting for your key to propagate… \d+:\d{2}")
DISCOVERY_GPG_PATTERN = re.compile(r"GPG · [0-9A-F ]+(?:…|\.\.\.) [0-9A-F ]+ · a@b\.co")
RADIO_ROW_PATTERN = re.compile(r"([●○])▌\s+(SSH · .*?|GPG · .*?)\s+▎")
MOUSE_EVENT_PATTERN = re.compile(rb"\x1b\[<(?P<button>\d+);(?P<x>\d+);(?P<y>\d+)(?P<state>[Mm])")


def normalize_snapshot(text: str) -> str:
    return DISCOVERY_GPG_PATTERN.sub(
        "GPG · <FPR> · a@b.co",
        ELAPSED_PATTERN.sub(
            "Waiting for your key to propagate… <ELAPSED>",
            KEY_MATERIAL_PATTERN.sub("<KEY>", SPINNER_PATTERN.sub("⠋", text)),
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


def command_name(record: dict[str, Any]) -> str | None:
    return str(record["argv"][2]) if len(record["argv"]) >= 3 else None


def press_keys(record: dict[str, Any]) -> tuple[str, ...]:
    argv = list(record["argv"])
    if command_name(record) != "press":
        return ()
    return tuple(str(part) for part in argv[3 : argv.index("-s")])


def commands_before(result: HarnessResult, step: str) -> list[dict[str, Any]]:
    commands = list(result.state["commands"])
    end = next(index for index, record in enumerate(commands) if record["step"] == step)
    return commands[:end]


def enter_presses_before(result: HarnessResult, step: str) -> int:
    return sum(press_keys(record).count("enter") for record in commands_before(result, step))


def interaction_commands(result: HarnessResult) -> list[dict[str, Any]]:
    return [
        record
        for record in result.state["commands"]
        if command_name(record) in {"press", "click", "type", "resize"}
    ]


def interaction_commands_before(result: HarnessResult, step: str) -> list[dict[str, Any]]:
    return [
        record
        for record in commands_before(result, step)
        if command_name(record) in {"press", "click", "type", "resize"}
    ]


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
    home_dir: Path | None = None,
    output_slug: str | None = None,
    timeout_seconds: float = 30,
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
            "80x24",
            str(harness.mitm.port),
            str(harness.mitm.confdir),
            str(fake_bin_dir),
            str(home_dir),
            str(harness.mitm.scenario_path),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )
    state_path = output_dir / "state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "session": slug,
        "size": "80x24",
        "snapshots": {},
        "commands": [],
        "captures": {},
        "app_exit": None,
        "error": {"type": "missing-state", "message": "wrapper did not write state.json"},
    }
    harness.mitm.scenario_path.write_text("{}")
    return HarnessResult(completed=completed, output_dir=output_dir, state=state)


def state_path_for(home_dir: Path) -> Path:
    return home_dir / ".cc-sentiment" / "state.json"


def make_saved_state(home_dir: Path) -> str:
    return AppState(
        config=SSHConfig(
            contributor_id=ContributorId(USERNAME),
            key_path=home_dir / ".ssh" / "id_ed25519",
        ),
    ).model_dump_json()


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


def radio_rows(snapshot: str) -> list[tuple[bool, str]]:
    return [
        (match.group(1) == "●", match.group(2))
        for line in normalize_snapshot(snapshot).splitlines()
        if (match := RADIO_ROW_PATTERN.search(line))
    ]


def runtime_mouse_events(result: HarnessResult) -> list[dict[str, int | str]]:
    log_path = result.output_dir / "runtime-input.log"
    assert log_path.exists(), f"Missing runtime input log: {log_path}"
    events = [
        {
            "type": "mouse-down" if match.group("state") == b"M" else "mouse-up",
            "button": int(match.group("button")),
            "x": int(match.group("x")),
            "y": int(match.group("y")),
        }
        for match in MOUSE_EVENT_PATTERN.finditer(log_path.read_bytes())
        if int(match.group("button")) == 0
    ]
    return events + [
        {"type": "click", "button": first["button"], "x": first["x"], "y": first["y"]}
        for first, second in zip(events, events[1:])
        if first["type"] == "mouse-down"
        and second["type"] == "mouse-up"
        and first["button"] == second["button"]
        and first["x"] == second["x"]
        and first["y"] == second["y"]
    ]


def test_back_nav_no_duplicate_radios(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "back-nav-no-dup"
    home_dir = Path(tempfile.mkdtemp(prefix="ccg-", dir="/tmp"))
    generate_gpg_key(home_dir, GPG_EMAIL)
    home_files, _ = make_keypair(tmp_path, ".ssh")
    result = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 15000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 15000},
                {"action": "wait", "pattern": GPG_EMAIL, "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-discovery-first"},
                {"action": "click", "pattern": "Back", "timeout_ms": 5000},
                {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 15000},
                {"action": "snapshot", "name": "02-username-back"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 15000},
                {"action": "wait", "pattern": GPG_EMAIL, "timeout_ms": 10000},
                {"action": "snapshot", "name": "03-discovery-second"},
                {"action": "press", "keys": ("escape",)},
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
                    "responses": (
                        {"status": 200, "json": {"login": USERNAME}},
                        {"status": 200, "json": {"login": USERNAME}},
                    ),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                ),
            },
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
        home_dir=home_dir,
        timeout_seconds=45,
    )

    assert_success(result)
    assert_steps(result, ("01-discovery-first", "02-username-back", "03-discovery-second"))
    for step in ("01-discovery-first", "02-username-back", "03-discovery-second"):
        assert_snapshot(result, scenario_name, step)
    first_rows = radio_rows(result.snapshot("01-discovery-first"))
    second_rows = radio_rows(result.snapshot("03-discovery-second"))
    assert first_rows == second_rows
    assert len(first_rows) == 2
    assert len({label for _, label in first_rows}) == 2
    assert [label for _, label in first_rows] == [
        "SSH · id_ed25519 · ssh-ed25519",
        "GPG · <FPR> · a@b.co",
    ]
    assert [request for request in matched_requests(result) if request[1] == f"https://api.github.com/users/{USERNAME}"] == [
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
    ]
    assert_no_setup_processes(result)


def test_keyboard_only_happy_path_uses_only_keypresses_and_at_most_four_enters(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    scenario_name = "keyboard-only-happy-path"
    home_files, _ = make_keypair(tmp_path, ".ssh")
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name=scenario_name,
        scenario={
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "capture_runtime_events": True,
            "steps": (
                {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-username"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                {"action": "snapshot", "name": "02-discovery"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Not on GitHub yet", "timeout_ms": 15000},
                {"action": "snapshot", "name": "03-remote"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Link via GitHub (gh)", "timeout_ms": 15000},
                {"action": "snapshot", "name": "04-link"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 15000},
                {"action": "snapshot", "name": "05-done"},
                {"action": "press", "keys": ("escape",)},
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
    assert_steps(result, ("01-username", "02-discovery", "03-remote", "04-link", "05-done"))
    for step in ("01-username", "02-discovery", "03-remote", "04-link", "05-done"):
        assert_snapshot(result, scenario_name, step)
    assert "Contribute my stats" in result.snapshot("05-done")
    assert all(command_name(record) == "press" for record in interaction_commands(result))
    assert not [record for record in interaction_commands(result) if command_name(record) == "click"]
    mouse_events = runtime_mouse_events(result)
    assert mouse_events == []
    assert all(set(press_keys(record)) <= {"enter", "escape", "up", "down", "left", "right"} for record in interaction_commands(result))
    assert enter_presses_before(result, "05-done") <= 4
    assert matched_requests(result) == [
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("GET", f"https://github.com/{USERNAME}.gpg", 200),
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("POST", VERIFY_URL, 200),
    ]
    assert_no_setup_processes(result)


def test_auto_setup_done_requires_zero_enter_presses(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "auto-success-zero-enter"
    home_files, public_text = make_keypair(tmp_path, ".ssh")
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name=scenario_name,
        scenario={
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Checking SSH keys on github.com/alice.keys", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-loading"},
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "snapshot", "name": "02-done"},
                {"action": "press", "keys": ("escape",)},
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
    assert_steps(result, ("01-loading", "02-done"))
    for step in ("01-loading", "02-done"):
        assert_snapshot(result, scenario_name, step)
    assert "Contribute my stats" in result.snapshot("02-done")
    assert enter_presses_before(result, "02-done") == 0
    assert interaction_commands_before(result, "02-done") == []
    assert matched_requests(result) == [
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("POST", VERIFY_URL, 200),
    ]
    assert_no_setup_processes(result)


def escape_scenario(stage: str, home_files: dict[str, dict[str, object]], home_dir: Path) -> dict[str, object]:
    base = {
        "exit_timeout_ms": 15000,
        "require_app_exit": True,
        "fake_bin_exclude": ("gpg",),
        "home_files": home_files,
    }
    match stage:
        case "loading":
            return {
                **base,
                "steps": (
                    {"action": "wait", "pattern": "Checking SSH keys on github.com/alice.keys", "timeout_ms": 10000},
                    {"action": "snapshot", "name": "01-stage"},
                    {"action": "press", "keys": ("escape",)},
                ),
                "http": (
                    {
                        "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                        "responses": ({"status": 200, "text": "", "delay_ms": 3000},),
                    },
                ),
                "fake": {
                    "gh": (
                        {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                    ),
                },
            }
        case "username":
            return {
                **base,
                "steps": (
                    {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                    {"action": "snapshot", "name": "01-stage"},
                    {"action": "press", "keys": ("escape",)},
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
                ),
                "fake": {
                    "gh": (
                        {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                    ),
                },
            }
        case "discovery":
            return {
                **base,
                "steps": (
                    {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                    {"action": "press", "keys": ("enter",)},
                    {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                    {"action": "snapshot", "name": "01-stage"},
                    {"action": "press", "keys": ("escape",)},
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
                ),
                "fake": {
                    "gh": (
                        {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                    ),
                },
            }
        case "remote":
            return {
                **base,
                "steps": (
                    {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                    {"action": "press", "keys": ("enter",)},
                    {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                    {"action": "press", "keys": ("enter",)},
                    {"action": "wait", "pattern": "Not on GitHub yet", "timeout_ms": 10000},
                    {"action": "snapshot", "name": "01-stage"},
                    {"action": "press", "keys": ("escape",)},
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
                ),
                "fake": {
                    "gh": (
                        {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                    ),
                },
            }
        case "link":
            return {
                **base,
                "steps": (
                    {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                    {"action": "press", "keys": ("enter",)},
                    {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                    {"action": "press", "keys": ("enter",)},
                    {"action": "wait", "pattern": "Not on GitHub yet", "timeout_ms": 10000},
                    {"action": "press", "keys": ("enter",)},
                    {"action": "wait", "pattern": "Link via GitHub (gh)", "timeout_ms": 10000},
                    {"action": "snapshot", "name": "01-stage"},
                    {"action": "press", "keys": ("escape",)},
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
                ),
                "fake": {
                    "gh": (
                        {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                        {"argv": ("auth", "status"), "stdout": "", "returncode": 0},
                    ),
                },
            }
        case "pending-done":
            return {
                "exit_timeout_ms": 15000,
                "require_app_exit": True,
                "steps": (
                    {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                    {"action": "snapshot", "name": "01-stage"},
                    {"action": "press", "keys": ("escape",)},
                ),
                "http": (
                    {
                        "match": {"method": "POST", "path": "/verify"},
                        "responses": ({"status": 401, "json": {}},),
                    },
                ),
                "fake_bin_exclude": ("gpg",),
                "home_files": {
                    **home_files,
                    ".cc-sentiment/state.json": {"content": make_saved_state(home_dir)},
                },
            }
        case other:
            raise ValueError(f"Unsupported escape stage: {other}")


@pytest.mark.parametrize(
    ("stage", "state_should_exist"),
    (
        ("loading", False),
        ("username", False),
        ("discovery", False),
        ("remote", False),
        ("link", False),
        ("pending-done", True),
    ),
)
def test_escape_cancels_cleanly_from_each_stage(
    tmp_path: Path,
    harness: HarnessRunner,
    stage: str,
    state_should_exist: bool,
) -> None:
    scenario_name = f"escape-{stage}"
    slug = HarnessRunner.sanitize(scenario_name)
    home_dir = tmp_path / slug / "home"
    home_files, _ = make_keypair(tmp_path, ".ssh")
    result = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        escape_scenario(stage, home_files, home_dir),
        home_dir=home_dir,
    )

    assert_success(result)
    assert_steps(result, ("01-stage",))
    assert_snapshot(result, scenario_name, "01-stage")
    assert state_path_for(home_dir).exists() is state_should_exist
    assert_no_setup_processes(result)

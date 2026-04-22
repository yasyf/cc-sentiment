from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cc_sentiment.models import AppState, ContributorId, SSHConfig
from tests.tuistory.harness import HarnessResult, HarnessRunner

USERNAME = "alice"
VERIFY_URL = "https://anetaco--cc-sentiment-api-serve.modal.run/verify"
SNAPSHOT_ROOT = Path(__file__).parent / "__snapshots__"
SPINNER_PATTERN = re.compile("[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")
ELAPSED_PATTERN = re.compile(r"Waiting for your key to propagate… \d+:\d{2}")


def normalize_snapshot(text: str) -> str:
    return ELAPSED_PATTERN.sub(
        "Waiting for your key to propagate… <ELAPSED>",
        SPINNER_PATTERN.sub("⠋", text),
    )


def snapshot_path(scenario: str, step: str, size: str = "80x24") -> Path:
    return SNAPSHOT_ROOT / scenario / f"{step}_{size}.txt"


def assert_snapshot(result: HarnessResult, scenario: str, step: str, size: str = "80x24") -> None:
    expected_path = snapshot_path(scenario, step, size)
    assert expected_path.exists(), f"Missing golden snapshot: {expected_path}"
    assert normalize_snapshot(result.snapshot(step)) == normalize_snapshot(expected_path.read_text())


def assert_steps(result: HarnessResult, expected_steps: tuple[str, ...]) -> None:
    assert tuple(result.state["snapshots"]) == expected_steps


def verify_requests(result: HarnessResult) -> list[dict[str, Any]]:
    return [entry for entry in result.addon_requests() if entry.get("matched") and entry["url"] == VERIFY_URL]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home_dir),
        "GNUPGHOME": str(gnupg_home),
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
    }
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


def state_path_for(home_dir: Path) -> Path:
    return home_dir / ".cc-sentiment" / "state.json"


def make_saved_state(home_dir: Path) -> str:
    return AppState(
        config=SSHConfig(
            contributor_id=ContributorId(USERNAME),
            key_path=home_dir / ".ssh" / "id_ed25519",
        ),
    ).model_dump_json()


def prepare_fake_bin_dir(
    harness: HarnessRunner,
    tmp_path: Path,
    slug: str,
    exclude: tuple[str, ...],
) -> tuple[Path, Path | None]:
    if not exclude:
        return harness.fake_bin_dir, None
    fake_bin_dir = tmp_path / slug / "fake-bin"
    fake_bin_dir.mkdir(parents=True, exist_ok=True)
    for source in harness.fake_bin_dir.iterdir():
        if source.name in exclude:
            continue
        target = fake_bin_dir / source.name
        shutil.copy2(source, target)
        target.chmod(source.stat().st_mode)
    return fake_bin_dir, fake_bin_dir


def run_scenario(
    harness: HarnessRunner,
    tmp_path: Path,
    scenario_name: str,
    scenario: dict[str, object],
    *,
    home_dir: Path | None = None,
    output_slug: str | None = None,
    size: str = "80x24",
    timeout_seconds: float = 30,
) -> HarnessResult:
    slug = HarnessRunner.sanitize(output_slug or scenario_name)
    output_dir = tmp_path / slug / "out"
    output_dir.mkdir(parents=True, exist_ok=True)
    home_dir = home_dir or (tmp_path / slug / "home")
    home_dir.mkdir(parents=True, exist_ok=True)
    HarnessRunner.write_home_files(home_dir, scenario)
    fake_bin_dir, cleanup_dir = prepare_fake_bin_dir(
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
    try:
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
            timeout=timeout_seconds,
        )
    finally:
        harness.mitm.scenario_path.write_text("{}")
        if cleanup_dir is not None:
            shutil.rmtree(cleanup_dir, ignore_errors=True)
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


def normalize_engine_copy(text: str) -> str:
    return "\n".join(
        line
        for line in text.splitlines()
        if "Where scoring happens:" not in line and not re.search(r"^\s*┃\s*dashboard\.", line)
    )


def significant_snapshot_lines(text: str) -> tuple[str, ...]:
    return tuple(
        stripped
        for line in normalize_engine_copy(text).splitlines()
        if (stripped := line.strip(" ┃│╭╮╰╯▔▁")) and any(char.isalnum() for char in stripped)
    )


def write_engine_override(tmp_path: Path, slug: str) -> dict[str, str]:
    pythonpath_dir = tmp_path / slug / "pythonpath"
    pythonpath_dir.mkdir(parents=True, exist_ok=True)
    (pythonpath_dir / "sitecustomize.py").write_text(
        "\n".join(
            (
                "import os",
                "from cc_sentiment.engines import EngineFactory",
                'if os.environ.get("CC_SENTIMENT_ENGINE") == "claude":',
                '    EngineFactory.default = classmethod(lambda cls: "claude")',
            )
        )
    )
    return {
        "CC_SENTIMENT_ENGINE": "claude",
        "PYTHONPATH": str(pythonpath_dir),
    }


def verified_ssh_link_scenario(
    home_files: dict[str, dict[str, object]],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, object]:
    return {
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
            {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
            {"action": "snapshot", "name": "01-done"},
            {
                "action": "capture-file",
                "name": "committed-state",
                "path": "__HOME__/.cc-sentiment/state.json",
            },
            {"action": "press", "keys": ("enter",)},
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
                "responses": ({"status": 200, "json": {}},),
            },
        ),
        "fake": {
            "gh": (
                {"argv": ("auth", "status"), "stdout": "", "returncode": 0},
                {"argv_prefix": ("ssh-key", "add"), "stdout": "", "returncode": 0},
            ),
        },
        "fake_bin_exclude": ("gpg",),
        "home_files": home_files,
        **({"env": env} if env else {}),
    }


def openpgp_pending_scenario(fingerprint: str) -> dict[str, object]:
    return {
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
            {"action": "press", "keys": ("enter",)},
            {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
            {"action": "snapshot", "name": "01-pending"},
            {
                "action": "capture-file",
                "name": "committed-state",
                "path": "__HOME__/.cc-sentiment/state.json",
            },
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
                "responses": ({"status": 404, "text": ""}, {"status": 404, "text": ""}),
            },
            {
                "match": {"method": "POST", "url": "https://keys.openpgp.org/vks/v1/upload"},
                "responses": ({"status": 200, "text": "token"},),
            },
            {
                "match": {"method": "POST", "url": "https://keys.openpgp.org/vks/v1/request-verify"},
                "responses": ({"status": 200, "json": {"status": {"alice@example.com": "pending"}}},),
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
        "home_files": {},
    }


def failed_gh_link_scenario(home_files: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
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
                "name": "committed-state",
                "path": "__HOME__/.cc-sentiment/state.json",
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
    }


def manual_pending_scenario(home_files: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
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
            {"action": "wait", "pattern": "Show me the key; I'll add it myself", "timeout_ms": 10000},
            {"action": "click", "pattern": "Show me the key; I'll add it myself", "timeout_ms": 10000},
            {"action": "press", "keys": ("enter",)},
            {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
            {"action": "snapshot", "name": "01-pending"},
            {
                "action": "capture-file",
                "name": "committed-state",
                "path": "__HOME__/.cc-sentiment/state.json",
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
            ),
        },
        "fake_bin_exclude": ("gpg",),
        "home_files": home_files,
    }


def saved_state_scenario(
    home_dir: Path,
    home_files: dict[str, dict[str, object]],
    responses: tuple[int, ...],
    steps: tuple[dict[str, object], ...],
) -> dict[str, object]:
    return {
        "exit_timeout_ms": 20000,
        "require_app_exit": True,
        "steps": steps,
        "http": (
            {
                "match": {"method": "POST", "path": "/verify"},
                "responses": tuple({"status": status, "json": {}} for status in responses),
            },
        ),
        "fake_bin_exclude": ("gh", "gpg"),
        "home_files": {
            **home_files,
            ".cc-sentiment/state.json": {"content": make_saved_state(home_dir)},
        },
    }


def materialize_paths(scenario: dict[str, object], home_dir: Path) -> dict[str, object]:
    return json.loads(json.dumps(scenario).replace("__HOME__", str(home_dir)))


@dataclass(frozen=True)
class TerminalCase:
    scenario_name: str
    final_step: str
    expected_key_type: str
    expected_contribute: bool
    scenario: dict[str, object]
    home_dir: Path | None = None


def test_terminal_state_honest_state_and_config_commit_invariants(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    ssh_home_files, _ = make_keypair(tmp_path, ".ssh")
    cases = (
        TerminalCase(
            scenario_name="terminal-verified-ssh",
            final_step="01-done",
            expected_key_type="ssh",
            expected_contribute=True,
            scenario=verified_ssh_link_scenario(ssh_home_files),
        ),
        TerminalCase(
            scenario_name="terminal-pending-manual",
            final_step="01-pending",
            expected_key_type="ssh",
            expected_contribute=False,
            scenario=manual_pending_scenario(ssh_home_files),
        ),
        TerminalCase(
            scenario_name="terminal-failed-gh-link",
            final_step="01-failed",
            expected_key_type="ssh",
            expected_contribute=False,
            scenario=failed_gh_link_scenario(ssh_home_files),
        ),
    )

    for case in cases:
        home_dir = case.home_dir or (tmp_path / case.scenario_name / "home")
        result = run_scenario(
            harness,
            tmp_path,
            case.scenario_name,
            materialize_paths(case.scenario, home_dir),
            home_dir=home_dir,
        )

        assert_success(result)
        final_snapshot = result.snapshot(case.final_step)
        saw_verify_200 = any(int(entry["status"]) == 200 for entry in verify_requests(result))
        assert ("Contribute my stats" in final_snapshot) is case.expected_contribute
        assert saw_verify_200 is case.expected_contribute
        capture = result.state["captures"]["committed-state"]
        captured_state_path = Path(capture["path"])
        current_state_path = state_path_for(home_dir)
        assert current_state_path.exists()
        assert capture["sha256"] == sha256(current_state_path)
        parsed = AppState.model_validate_json(captured_state_path.read_text())
        assert parsed.config is not None
        assert parsed.config.key_type == case.expected_key_type
        assert_no_setup_processes(result)


def test_claude_engine_happy_path_matches_omlx_flow(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    home_files, _ = make_keypair(tmp_path, ".ssh")
    omlx = run_scenario(
        harness,
        tmp_path,
        "engine-omlx-happy-path",
        materialize_paths(
            verified_ssh_link_scenario(home_files),
            tmp_path / "engine-omlx-happy-path" / "home",
        ),
            size="180x68",
    )
    claude_env = write_engine_override(tmp_path, "engine-claude-happy-path")
    claude = run_scenario(
        harness,
        tmp_path,
        "engine-claude-happy-path",
        materialize_paths(
            verified_ssh_link_scenario(home_files, env=claude_env),
            tmp_path / "engine-claude-happy-path" / "home",
        ),
            size="180x68",
    )

    assert_success(omlx)
    assert_success(claude)
    assert_steps(omlx, ("01-done",))
    assert_steps(claude, ("01-done",))
    assert_snapshot(claude, "claude-engine-happy-path", "01-done", "180x68")
    assert significant_snapshot_lines(omlx.snapshot("01-done")) == significant_snapshot_lines(claude.snapshot("01-done"))
    assert "local Gemma model" in omlx.snapshot("01-done")
    assert "claude CLI on this Mac" in claude.snapshot("01-done")
    assert [int(entry["status"]) for entry in verify_requests(omlx)] == [200]
    assert [int(entry["status"]) for entry in verify_requests(claude)] == [200]
    assert_no_setup_processes(omlx)
    assert_no_setup_processes(claude)


def test_addon_isolation_and_path_hygiene_between_scenarios(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    original_path = os.environ["PATH"]
    baseline_threads = len(threading.enumerate())
    saved_home_files, _ = make_keypair(tmp_path, ".ssh")

    first_home = tmp_path / "addon-isolation-first" / "home"
    first = run_scenario(
        harness,
        tmp_path,
        "addon-isolation-first",
        saved_state_scenario(
            first_home,
            saved_home_files,
            responses=(401,),
            steps=(
                {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-pending"},
                {"action": "press", "keys": ("escape",)},
            ),
        ),
        home_dir=first_home,
    )

    assert_success(first)
    assert os.environ["PATH"] == original_path
    assert len(threading.enumerate()) == baseline_threads
    first_verify = verify_requests(first)
    assert [int(entry["status"]) for entry in first_verify] == [401]
    assert int(first_verify[0]["count"]) == 1
    assert not (tmp_path / "addon-isolation-first" / "fake-bin").exists()
    assert_no_setup_processes(first)

    second_home = tmp_path / "addon-isolation-second" / "home"
    second = run_scenario(
        harness,
        tmp_path,
        "addon-isolation-second",
        saved_state_scenario(
            second_home,
            saved_home_files,
            responses=(200,),
            steps=(
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-done"},
                {"action": "press", "keys": ("enter",)},
            ),
        ),
        home_dir=second_home,
    )

    assert_success(second)
    assert os.environ["PATH"] == original_path
    assert len(threading.enumerate()) == baseline_threads
    second_verify = verify_requests(second)
    assert [int(entry["status"]) for entry in second_verify] == [200]
    assert int(second_verify[0]["count"]) == 1
    assert not any(int(entry["status"]) == 401 for entry in second_verify)
    assert not (tmp_path / "addon-isolation-second" / "fake-bin").exists()
    assert_no_setup_processes(second)


def test_network_flap_pending_once_then_verified(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    home_dir = tmp_path / "network-flap" / "home"
    saved_home_files, _ = make_keypair(tmp_path, ".ssh")
    result = run_scenario(
        harness,
        tmp_path,
        "network-flap",
        saved_state_scenario(
            home_dir,
            saved_home_files,
            responses=(502, 200),
            steps=(
                {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-pending"},
                {"action": "wait", "pattern": "Contribute my stats", "timeout_ms": 15000},
                {"action": "snapshot", "name": "02-verified"},
                {"action": "press", "keys": ("enter",)},
            ),
        ),
        home_dir=home_dir,
        timeout_seconds=25,
    )

    assert_success(result)
    assert_steps(result, ("01-pending", "02-verified"))
    assert_snapshot(result, "network-flap", "01-pending")
    assert_snapshot(result, "network-flap", "02-verified")
    pending_snapshot = result.snapshot("01-pending")
    verified_snapshot = result.snapshot("02-verified")
    assert "Contribute my stats" not in pending_snapshot
    assert "Contribute my stats" in verified_snapshot
    assert all("We couldn't verify your key" not in result.snapshot(step) for step in result.state["snapshots"])
    assert [int(entry["status"]) for entry in verify_requests(result)] == [502, 200]
    assert_no_setup_processes(result)

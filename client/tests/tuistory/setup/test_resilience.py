from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from cc_sentiment.models import AppState, ContributorId, SSHConfig
from tests.tuistory.harness import HarnessResult, HarnessRunner

USERNAME = "alice"
VERIFY_URL = "https://anetaco--cc-sentiment-api-serve.modal.run/verify"
SNAPSHOT_ROOT = Path(__file__).parent / "__snapshots__"
SPINNER_PATTERN = re.compile("[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]")
KEY_MATERIAL_PATTERN = re.compile("[A-Za-z0-9+/=]{12,}")
ELAPSED_PATTERN = re.compile(r"Waiting for your key to propagate… \d+:\d{2}")
MID_WORD_ELLIPSIS_PATTERN = re.compile(r"\S…\S")
LINE_END_ELLIPSIS_PATTERN = re.compile(r"\S…$")
OVERFLOW_MARKERS = ("▸", "»")


def normalize_snapshot(text: str) -> str:
    return ELAPSED_PATTERN.sub(
        "Waiting for your key to propagate… <ELAPSED>",
        KEY_MATERIAL_PATTERN.sub("<KEY>", SPINNER_PATTERN.sub("⠋", text)),
    )


def snapshot_path(scenario: str, step: str, size: str) -> Path:
    return SNAPSHOT_ROOT / scenario / f"{step}_{size}.txt"


def assert_snapshot(result: HarnessResult, scenario: str, step: str, size: str) -> None:
    expected_path = snapshot_path(scenario, step, size)
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


def verify_requests(result: HarnessResult) -> list[dict[str, object]]:
    return [entry for entry in result.addon_requests() if entry.get("matched") and entry["url"] == VERIFY_URL]


def fake_commands(result: HarnessResult, tool: str) -> list[dict[str, object]]:
    return [entry for entry in result.fake_commands() if entry["tool"] == tool]


def elapsed_for_step(result: HarnessResult, step: str) -> float:
    return float(next(record["elapsed"] for record in result.state["commands"] if record["step"] == step))


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


def state_path_for(home_dir: Path) -> Path:
    return home_dir / ".cc-sentiment" / "state.json"


def sha256(path: Path) -> str:
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


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
    size: str = "80x24",
    home_dir: Path | None = None,
    output_slug: str | None = None,
    timeout_seconds: float = 30,
    env_patch: dict[str, str] | None = None,
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
        timeout=timeout_seconds,
        env={**os.environ, **(env_patch or {})},
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


def assert_no_mid_word_truncation(text: str) -> None:
    assert MID_WORD_ELLIPSIS_PATTERN.search(text) is None
    lines = text.splitlines()
    assert all(
        LINE_END_ELLIPSIS_PATTERN.search(line) is None
        for line in lines[:-1]
    )


def assert_within_width(text: str, width: int) -> None:
    assert all(len(line) <= width for line in text.splitlines())


def assert_no_horizontal_overflow(text: str) -> None:
    assert not [line for line in text.splitlines() if line.endswith(OVERFLOW_MARKERS)]


def pending_seconds(text: str) -> int:
    match = re.search(r"Waiting for your key to propagate… (\d+):(\d{2})", text)
    assert match is not None, text
    return int(match.group(1)) * 60 + int(match.group(2))


def classify_stage(text: str) -> str:
    if "You're set up. Ready to upload." in text or "Contribute my stats" in text:
        return "step-done"
    if "Your key isn't linked yet" in text or "Verifying your key" in text:
        return "step-remote"
    if "Link my key" in text or "Link via GitHub (gh)" in text:
        return "step-upload"
    if "SSH ·" in text or "GPG ·" in text or "Choose the key to use" in text:
        return "step-discovery"
    if "Who are you?" in text or "Auto-detected:" in text:
        return "step-username"
    return "step-loading"


def no_gh_path(tmp_path: Path) -> str:
    tool_dir = tmp_path / "path-no-gh"
    tool_dir.mkdir(parents=True, exist_ok=True)
    for name in ("bun", "git", "python3", "uv"):
        source = shutil.which(name)
        assert source is not None
        target = tool_dir / name
        if target.exists():
            continue
        target.symlink_to(source)
    return f"{tool_dir}:/usr/bin:/bin:/usr/sbin:/sbin"


def width_scenario(home_files: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "exit_timeout_ms": 15000,
        "require_app_exit": True,
        "steps": (
            {"action": "wait", "pattern": "Looking for your GitHub username", "timeout_ms": 10000},
            {"action": "snapshot", "name": "01-loading"},
            {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
            {"action": "snapshot", "name": "02-username"},
            {"action": "click", "pattern": "Next", "timeout_ms": 5000},
            {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
            {"action": "snapshot", "name": "03-discovery"},
            {"action": "click", "pattern": "Next", "timeout_ms": 5000},
            {"action": "wait", "pattern": "Your key isn't linked yet", "timeout_ms": 10000},
            {"action": "snapshot", "name": "04-remote"},
            {"action": "click", "pattern": "Next", "timeout_ms": 5000},
            {"action": "wait", "pattern": "Link via GitHub (gh)", "timeout_ms": 10000},
            {"action": "snapshot", "name": "05-link"},
            {"action": "press", "keys": ("enter",)},
            {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
            {"action": "snapshot", "name": "06-done"},
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
    }


def test_width_resilience(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "width-resilience"
    expected_steps = ("01-loading", "02-username", "03-discovery", "04-remote", "05-link", "06-done")
    home_files, _ = make_keypair(tmp_path, ".ssh")

    for size, width in (("80x24", 80), ("120x40", 120), ("180x68", 180)):
        result = run_scenario(
            harness,
            tmp_path,
            scenario_name,
            width_scenario(home_files),
            size=size,
            output_slug=f"{scenario_name}-{size}",
        )

        assert_success(result)
        assert_steps(result, expected_steps)
        for step in expected_steps:
            snapshot = result.snapshot(step)
            if step != "01-loading":
                assert_snapshot(result, scenario_name, step, size)
            assert_within_width(snapshot, width)
            assert_no_mid_word_truncation(snapshot)
            assert_no_horizontal_overflow(snapshot)
        assert "Next" in result.snapshot("04-remote")
        assert "Link my key" in result.snapshot("05-link")
        assert "Contribute my stats" in result.snapshot("06-done")
        assert_no_setup_processes(result)


def test_rerun_idempotency_verified(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "rerun-idempotency"
    home_dir = tmp_path / "rerun-home"
    home_files, public_text = make_keypair(tmp_path, ".ssh")
    first = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                        "responses": ({"status": 200, "text": public_text, "delay_ms": 50},),
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
        home_dir=home_dir,
    )

    assert_success(first)
    first_state_path = state_path_for(home_dir)
    first_hash = sha256(first_state_path)
    assert matched_requests(first) == [
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("POST", VERIFY_URL, 200),
    ]

    second = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                    {"action": "sleep", "seconds": 0.05},
                {"action": "snapshot", "name": "01-initial"},
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "snapshot", "name": "02-done"},
                {"action": "press", "keys": ("enter",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                        "responses": ({"status": 200, "text": public_text},),
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
        },
        home_dir=home_dir,
        output_slug=f"{scenario_name}-second",
    )

    assert_success(second)
    assert elapsed_for_step(second, "02-done") < 2
    assert_steps(second, ("01-initial", "02-done"))
    assert_snapshot(second, scenario_name, "01-initial", "80x24")
    assert_snapshot(second, scenario_name, "02-done", "80x24")
    assert classify_stage(second.snapshot("01-initial")) in {"step-loading", "step-done"}
    assert "GitHub username" not in second.snapshot("01-initial")
    assert "SSH ·" not in second.snapshot("01-initial")
    assert "SSH ·" not in second.snapshot("02-done")
    assert matched_requests(second) == [("POST", VERIFY_URL, 200)]
    assert len(verify_requests(second)) == 1
    assert sha256(first_state_path) == first_hash
    assert_no_setup_processes(first)
    assert_no_setup_processes(second)


def test_visible_only_no_gh_hides_github_option(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "visible-only-no-gh"
    home_files, _ = make_keypair(tmp_path, ".ssh")
    result = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                    {"action": "wait", "pattern": "GitHub username", "timeout_ms": 10000},
                    {"action": "type", "text": USERNAME},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Your key isn't linked yet", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                    {"action": "wait", "pattern": "Show me the key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-link"},
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
            "fake_bin_exclude": ("gh", "gpg"),
            "home_files": home_files,
        },
        env_patch={"PATH": no_gh_path(tmp_path)},
    )

    assert_success(result)
    assert_steps(result, ("01-link",))
    assert_snapshot(result, scenario_name, "01-link", "80x24")
    link_snapshot = result.snapshot("01-link")
    assert "Link via GitHub (gh)" not in link_snapshot
    assert "needs gh CLI" not in link_snapshot
    assert "needs gh auth login" not in link_snapshot
    assert "(disabled)" not in link_snapshot
    assert fake_commands(result, "gh") == []
    assert_no_setup_processes(result)


def test_remote_elision_skips_remote_history(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "remote-elision"
    home_files, public_text = make_keypair(tmp_path, ".ssh")
    result = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 15000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-discovery"},
                {"action": "press", "keys": ("enter",)},
                {"action": "sleep", "seconds": 0.15},
                {"action": "snapshot", "name": "02-after-enter-015"},
                {"action": "sleep", "seconds": 0.25},
                {"action": "snapshot", "name": "03-after-enter-040"},
                {"action": "wait", "pattern": "You're set up. Ready to upload.", "timeout_ms": 10000},
                {"action": "snapshot", "name": "04-done"},
                {"action": "press", "keys": ("escape",)},
            ),
            "http": (
                {
                    "match": {"method": "GET", "url": f"https://github.com/{USERNAME}.keys"},
                    "responses": ({"status": 200, "text": "", "delay_ms": 600}, {"status": 200, "text": public_text, "delay_ms": 600}),
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
                ),
            },
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
    )

    assert_success(result)
    assert_steps(result, ("01-discovery", "02-after-enter-015", "03-after-enter-040", "04-done"))
    for step in ("01-discovery", "04-done"):
        assert_snapshot(result, scenario_name, step, "80x24")
    stage_history = [classify_stage(result.snapshot(step)) for step in result.state["snapshots"]]
    assert stage_history[0] == "step-discovery"
    assert stage_history[-1] == "step-done"
    assert "step-remote" not in stage_history
    assert all(
        marker not in result.snapshot(step)
        for step in result.state["snapshots"]
        for marker in ("Your key isn't linked yet", "Verifying your key")
    )
    assert "Link my key" not in result.snapshot("04-done")
    assert matched_requests(result) == [
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("GET", f"https://github.com/{USERNAME}.gpg", 200),
        ("GET", f"https://api.github.com/users/{USERNAME}", 200),
        ("GET", f"https://github.com/{USERNAME}.keys", 200),
        ("POST", VERIFY_URL, 200),
    ]
    config = AppState.model_validate_json(state_path_for(result.output_dir.parent / "home").read_text()).config
    assert config is not None
    assert config.key_type == "ssh"
    assert_no_setup_processes(result)


def test_resize_mid_pending_preserves_counter_and_retry_cadence(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "resize-mid-pending"
    home_dir = tmp_path / "resize-mid-pending" / "home"
    home_files, _ = make_keypair(tmp_path, ".ssh")
    home_files[".cc-sentiment/state.json"] = {"content": make_saved_state(home_dir)}
    result = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 20000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                {"action": "sleep", "seconds": 2.1},
                {"action": "snapshot", "name": "01-pending-2s"},
                {"action": "sleep", "seconds": 2.9},
                {"action": "resize", "value": "120x40"},
                {"action": "wait-idle", "timeout_ms": 500},
                {"action": "sleep", "seconds": 6.5},
                {"action": "snapshot", "name": "02-pending-12s"},
                {"action": "press", "keys": ("escape",)},
            ),
            "http": (
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": (
                        {"status": 401, "json": {}},
                        {"status": 401, "json": {}},
                        {"status": 401, "json": {}},
                    ),
                },
            ),
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
        home_dir=home_dir,
        timeout_seconds=25,
    )

    assert_success(result)
    assert_steps(result, ("01-pending-2s", "02-pending-12s"))
    assert_snapshot(result, scenario_name, "01-pending-2s", "80x24")
    assert_snapshot(result, scenario_name, "02-pending-12s", "120x40")
    pending_t1 = result.snapshot("01-pending-2s")
    pending_t2 = result.snapshot("02-pending-12s")
    assert pending_seconds(pending_t1) < pending_seconds(pending_t2)
    assert "Exit, continue later" in pending_t2
    assert "Retry now" in pending_t2
    verify = verify_requests(result)
    assert [int(entry["status"]) for entry in verify] == [401, 401]
    gaps = [float(verify[index]["elapsed"]) - float(verify[index - 1]["elapsed"]) for index in range(1, len(verify))]
    assert gaps
    assert all(8 <= gap <= 12 for gap in gaps)
    assert_no_setup_processes(result)

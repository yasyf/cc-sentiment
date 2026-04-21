from __future__ import annotations

import json
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
KEY_MATERIAL_PATTERN = re.compile("[A-Za-z0-9+/]{12,}")
ELAPSED_PATTERN = re.compile(r"Waiting for your key to propagate… \d+:\d{2}")


def normalize_snapshot(text: str) -> str:
    return ELAPSED_PATTERN.sub(
        "Waiting for your key to propagate… <ELAPSED>",
        KEY_MATERIAL_PATTERN.sub("<KEY>", SPINNER_PATTERN.sub("⠋", text)),
    )


def snapshot_path(scenario: str, step: str) -> Path:
    return SNAPSHOT_ROOT / scenario / f"{step}_80x24.txt"


def assert_snapshot(result: HarnessResult, scenario: str, step: str) -> None:
    expected_path = snapshot_path(scenario, step)
    assert expected_path.exists(), f"Missing golden snapshot: {expected_path}"
    assert normalize_snapshot(result.snapshot(step)) == normalize_snapshot(expected_path.read_text())


def assert_steps(result: HarnessResult, expected_steps: tuple[str, ...]) -> None:
    assert tuple(result.state["snapshots"]) == expected_steps


def matched_requests(result: HarnessResult) -> list[dict[str, object]]:
    return [entry for entry in result.addon_requests() if entry.get("matched")]


def verify_requests(result: HarnessResult) -> list[dict[str, object]]:
    return [entry for entry in matched_requests(result) if entry["url"] == VERIFY_URL]


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


def pending_seconds(text: str) -> int:
    match = re.search(r"Waiting for your key to propagate… (\d+):(\d{2})", text)
    assert match is not None, text
    return int(match.group(1)) * 60 + int(match.group(2))


def capture_hash(result: HarnessResult, name: str) -> str:
    return str(result.state["captures"][name]["sha256"])


def assert_no_input_between(result: HarnessResult, start_step: str, end_step: str) -> None:
    commands = list(result.state["commands"])
    start = next(index for index, record in enumerate(commands) if record["step"] == start_step)
    end = next(index for index, record in enumerate(commands) if record["step"] == end_step)
    assert [
        record
        for record in commands[start + 1:end]
        if len(record["argv"]) >= 3 and record["argv"][2] in {"press", "type", "click"}
    ] == []


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
    timeout_seconds: float = 45,
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


def state_file(home_dir: Path) -> Path:
    return home_dir / ".cc-sentiment" / "state.json"


def make_saved_state(home_dir: Path) -> str:
    return AppState(
        config=SSHConfig(
            contributor_id=ContributorId(USERNAME),
            key_path=home_dir / ".ssh" / "id_ed25519",
        ),
    ).model_dump_json()


def assert_key_preview_matches(upload_snapshot: str, public_text: str) -> None:
    inside_preview = False
    chunks: list[str] = []
    for line in upload_snapshot.splitlines():
        if "╭" in line:
            inside_preview = True
            continue
        if "╰" in line:
            break
        if inside_preview and (match := re.search(r"│\s?(.*?)\s*│", line)):
            chunks.append(match.group(1).strip())
    assert "".join(chunks).replace(" ", "") == public_text.replace(" ", "").replace("\n", "")


def test_manual_escape_pending_to_verified(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "manual-escape-pending-to-verified"
    slug = HarnessRunner.sanitize(scenario_name)
    home_dir = tmp_path / slug / "home"
    home_files, public_text = make_keypair(tmp_path, ".ssh")
    result = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 25000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": f"Auto-detected: {USERNAME}", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "SSH · id_ed25519 · ssh-ed25519", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Your key isn't linked yet", "timeout_ms": 10000},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Link via GitHub (gh)", "timeout_ms": 10000},
                    {"action": "click", "pattern": "Show me the key; I'll add it myself", "timeout_ms": 5000},
                {"action": "wait-idle", "timeout_ms": 500},
                {"action": "snapshot", "name": "01-upload-manual-keypreview"},
                {"action": "press", "keys": ("enter",)},
                {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                {"action": "sleep", "seconds": 11.1},
                {"action": "snapshot", "name": "02-pending"},
                {"action": "capture-file", "name": "state-before-verified", "path": str(state_file(home_dir))},
                {"action": "wait", "pattern": "Contribute my stats", "timeout_ms": 15000},
                {"action": "snapshot", "name": "03-verified-after-poll"},
                {"action": "capture-file", "name": "state-after-verified", "path": str(state_file(home_dir))},
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
                    "responses": (
                        {"status": 401, "json": {}},
                        {"status": 401, "json": {}},
                        {"status": 200, "json": {}},
                    ),
                },
            ),
            "fake": {
                "gh": (
                    {"argv": ("api", "user", "--jq", ".login"), "stdout": f"{USERNAME}\n", "returncode": 0},
                    {"argv": ("auth", "status"), "stdout": "", "returncode": 0},
                ),
            },
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
        home_dir=home_dir,
    )

    assert_success(result)
    assert_steps(result, ("01-upload-manual-keypreview", "02-pending", "03-verified-after-poll"))
    for step in ("01-upload-manual-keypreview", "02-pending", "03-verified-after-poll"):
        assert_snapshot(result, scenario_name, step)
    upload_snapshot = result.snapshot("01-upload-manual-keypreview")
    pending_snapshot = result.snapshot("02-pending")
    verified_snapshot = result.snapshot("03-verified-after-poll")
    assert_key_preview_matches(upload_snapshot, public_text)
    assert "Show me the key; I'll add it myself" in upload_snapshot
    assert "Paste your public key at" in pending_snapshot
    assert "https://github.com/settings/ssh/new" in pending_snapshot
    assert "Contribute my stats" not in pending_snapshot
    assert "Waiting for your key to propagate…" not in verified_snapshot
    assert "Contribute my stats" in verified_snapshot
    verify = verify_requests(result)
    assert [int(entry["status"]) for entry in verify] == [401, 401, 200]
    assert all(
        8 <= float(verify[index]["elapsed"]) - float(verify[index - 1]["elapsed"]) <= 12
        for index in range(1, len(verify))
    )
    assert capture_hash(result, "state-before-verified") == capture_hash(result, "state-after-verified")
    assert_no_input_between(result, "02-pending", "03-verified-after-poll")
    assert_no_setup_processes(result)


def test_pending_auto_retry_cadence(tmp_path: Path, harness: HarnessRunner) -> None:
    scenario_name = "pending-auto-retry-cadence"
    slug = HarnessRunner.sanitize(scenario_name)
    home_dir = tmp_path / slug / "home"
    home_files, _ = make_keypair(tmp_path, ".ssh")
    home_files[".cc-sentiment/state.json"] = {"content": make_saved_state(home_dir)}
    result = run_scenario(
        harness,
        tmp_path,
        scenario_name,
        {
            "exit_timeout_ms": 35000,
            "require_app_exit": True,
            "steps": (
                {"action": "wait", "pattern": "Waiting for your key", "timeout_ms": 10000},
                {"action": "snapshot", "name": "01-pending-0s"},
                {"action": "sleep", "seconds": 11.1},
                {"action": "snapshot", "name": "02-pending-11s"},
                {"action": "sleep", "seconds": 10.0},
                {"action": "snapshot", "name": "03-pending-21s"},
                {"action": "capture-file", "name": "state-before-verified", "path": str(state_file(home_dir))},
                {"action": "wait", "pattern": "Contribute my stats", "timeout_ms": 15000},
                {"action": "snapshot", "name": "04-verified"},
                {"action": "capture-file", "name": "state-after-verified", "path": str(state_file(home_dir))},
                {"action": "press", "keys": ("enter",)},
            ),
            "http": (
                {
                    "match": {"method": "POST", "path": "/verify"},
                    "responses": (
                        {"status": 401, "json": {}},
                        {"status": 401, "json": {}},
                        {"status": 401, "json": {}},
                        {"status": 200, "json": {}},
                    ),
                },
            ),
            "fake_bin_exclude": ("gpg",),
            "home_files": home_files,
        },
        home_dir=home_dir,
    )

    assert_success(result)
    assert_steps(result, ("01-pending-0s", "02-pending-11s", "03-pending-21s", "04-verified"))
    for step in ("01-pending-0s", "02-pending-11s", "03-pending-21s", "04-verified"):
        assert_snapshot(result, scenario_name, step)
    pending_0 = result.snapshot("01-pending-0s")
    pending_11 = result.snapshot("02-pending-11s")
    pending_21 = result.snapshot("03-pending-21s")
    verified_snapshot = result.snapshot("04-verified")
    assert 0 <= pending_seconds(pending_0) <= 2
    assert 10 <= pending_seconds(pending_11) <= 12
    assert 20 <= pending_seconds(pending_21) <= 22
    assert "Contribute my stats" not in pending_21
    assert "Contribute my stats" in verified_snapshot
    verify = verify_requests(result)
    assert [int(entry["status"]) for entry in verify] == [401, 401, 401, 200]
    window = [entry for entry in verify if float(entry["elapsed"]) <= 25]
    assert len(window) >= 3
    gaps = [float(window[index]["elapsed"]) - float(window[index - 1]["elapsed"]) for index in range(1, len(window))]
    assert len(gaps) >= 2
    assert all(8 <= gap <= 12 for gap in gaps)
    assert capture_hash(result, "state-before-verified") == capture_hash(result, "state-after-verified")
    assert_no_input_between(result, "01-pending-0s", "04-verified")
    assert_no_setup_processes(result)

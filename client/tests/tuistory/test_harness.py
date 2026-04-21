from __future__ import annotations

from pathlib import Path

from tests.tuistory.conftest import MitmSession
from tests.tuistory.harness import HarnessRunner


def test_mitm_fixture_starts_and_parses_ephemeral_port(mitm: MitmSession) -> None:
    assert mitm.port > 0
    assert (mitm.confdir / "mitmproxy-ca-cert.pem").exists()


def test_wrapper_smoke_launches_snapshots_and_intercepts_https(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name="smoke-launch-quit",
        scenario={
            "exit_timeout_ms": 15000,
            "probe_url": "https://github.com/alice.keys",
            "steps": (
                {"action": "sleep", "seconds": 2},
                {"action": "snapshot", "name": "loading"},
            ),
            "http": (
                {
                    "name": "github-keys",
                    "match": {
                        "method": "GET",
                        "url": "https://github.com/alice.keys",
                    },
                    "responses": (
                        {"status": 200, "text": ""},
                    ),
                },
            ),
            "fake": {
                "gh": (
                    {
                        "argv": ("api", "user", "--jq", ".login"),
                        "stdout": "alice\n",
                        "returncode": 0,
                    },
                    {
                        "argv": ("auth", "status"),
                        "stderr": "not logged in\n",
                        "returncode": 1,
                    },
                ),
            },
        },
    )
    assert result.returncode == 0, result.completed.stderr or result.completed.stdout
    assert result.app_returncode == 0
    assert "Setting things up" in result.snapshot("loading")
    assert any(
        entry["url"] == "https://github.com/alice.keys" and entry["matched"]
        for entry in result.addon_requests()
    )
    assert any(
        entry["tool"] == "gh" and entry["argv"] == ["api", "user", "--jq", ".login"]
        for entry in result.fake_commands()
    )


def test_wrapper_capture_file_records_hash(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    capture_source = tmp_path / "capture.txt"
    capture_source.write_text("hello")

    result = harness.run(
        tmp_path=tmp_path,
        scenario_name="capture-file",
        scenario={
            "steps": (
                {"action": "capture-file", "name": "greeting", "path": str(capture_source)},
            ),
        },
    )

    assert result.returncode == 0, result.completed.stderr or result.completed.stdout
    assert result.state["captures"]["greeting"]["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert Path(result.state["captures"]["greeting"]["path"]).read_text() == "hello"

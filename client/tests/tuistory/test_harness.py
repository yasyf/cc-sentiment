from __future__ import annotations

import io
from pathlib import Path

import pytest

from tests.tuistory import conftest
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
            # Harness smoke test intentionally inspects a live session, then lets the wrapper close it.
            "allow_forced_close": True,
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
    assert result.state["app_exit"]["forced_close"] is True
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
            # This harness-only capture test validates wrapper plumbing without dismissing the app itself.
            "allow_forced_close": True,
            "steps": (
                {"action": "capture-file", "name": "greeting", "path": str(capture_source)},
            ),
        },
    )

    assert result.returncode == 0, result.completed.stderr or result.completed.stdout
    assert result.state["app_exit"]["forced_close"] is True
    assert result.state["captures"]["greeting"]["sha256"] == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert Path(result.state["captures"]["greeting"]["path"]).read_text() == "hello"


def test_wrapper_forced_close_requires_opt_in(
    tmp_path: Path,
    harness: HarnessRunner,
) -> None:
    result = harness.run(
        tmp_path=tmp_path,
        scenario_name="forced-close-default-fails",
        scenario={
            "exit_timeout_ms": 200,
            "steps": (),
        },
    )

    assert result.returncode == 1
    assert result.state["error"] == {
        "message": "Scenario timed out and required a forced close",
        "type": "ForcedClose",
    }
    assert result.state["app_exit"]["forced_close"] is True
    assert result.app_returncode == 0


def test_mitm_fixture_terminates_spawned_process_when_startup_check_raises(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path_factory: pytest.TempPathFactory,
) -> None:
    class FakeProcess:
        def __init__(self) -> None:
            self.stdout = io.StringIO()
            self.terminated = False
            self.killed = False

        def terminate(self) -> None:
            self.terminated = True

        def wait(self, timeout: float) -> int:
            return 0

        def kill(self) -> None:
            self.killed = True

    process = FakeProcess()
    monkeypatch.setattr(conftest.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(conftest.MitmOutput, "start", lambda self: None)
    monkeypatch.setattr(conftest.MitmOutput, "wait_for_port", lambda self, timeout: (_ for _ in ()).throw(TimeoutError("boom")))

    generator = conftest.mitm.__wrapped__(tmp_path_factory)
    with pytest.raises(TimeoutError, match="boom"):
        next(generator)

    assert process.terminated is True
    assert process.killed is False

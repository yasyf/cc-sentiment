from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.tuistory.scenario import ScenarioFile


@dataclass(frozen=True)
class CommandRecord:
    step: str
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    elapsed: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "argv": list(self.argv),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "elapsed": self.elapsed,
        }


class ScenarioDriver:
    ANSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

    def __init__(
        self,
        session: str,
        output_dir: Path,
        size: str,
        scenario_path: Path,
        bun_path: str,
        cli_path: str,
        daemon_port: int,
        app_exit_path: Path,
        started_at: float,
    ) -> None:
        self.session = session
        self.output_dir = output_dir
        self.size = size
        self.scenario_path = scenario_path
        self.bun_path = bun_path
        self.cli_path = cli_path
        self.daemon_port = daemon_port
        self.app_exit_path = app_exit_path
        self.started_at = started_at
        self.records: list[CommandRecord] = []
        self.snapshots: dict[str, str] = {}
        self.captures: dict[str, dict[str, Any]] = {}

    def run(self) -> int:
        scenario = ScenarioFile.load(self.scenario_path)
        try:
            for step in ScenarioFile.steps(scenario):
                self.run_step(step)
            try:
                app_exit = self.wait_for_app_exit(step_timeout_ms=scenario.get("exit_timeout_ms", 5000))
            except TimeoutError:
                self.run_cli("close-session", "close", "-s", self.session, allow_failure=True)
                if scenario.get("require_app_exit", False):
                    app_exit = self.wait_for_app_exit(step_timeout_ms=5000)
                else:
                    app_exit = self.read_app_exit() or {"returncode": 0, "forced_exit": True}
            else:
                self.run_cli("close-session", "close", "-s", self.session, allow_failure=True)
            self.write_state(app_exit, None)
            return int(app_exit["returncode"] != 0)
        except Exception as error:
            app_exit = self.read_app_exit()
            self.run_cli("close-session", "close", "-s", self.session, allow_failure=True)
            self.write_state(app_exit, {"message": str(error), "type": error.__class__.__name__})
            return 1

    def run_step(self, step: dict[str, Any]) -> None:
        match step["action"]:
            case "wait":
                self.ensure_success(
                    self.run_cli(
                        step.get("name", "wait"),
                        "wait",
                        step["pattern"],
                        "-s",
                        self.session,
                        "--timeout",
                        str(step.get("timeout_ms", 5000)),
                        timeout_seconds=max(10, int(step.get("timeout_ms", 5000)) / 1000 + 5),
                    )
                )
            case "wait-idle":
                self.ensure_success(
                    self.run_cli(
                        step.get("name", "wait-idle"),
                        "wait-idle",
                        "-s",
                        self.session,
                        "--timeout",
                        str(step.get("timeout_ms", 500)),
                        timeout_seconds=max(10, int(step.get("timeout_ms", 500)) / 1000 + 5),
                    )
                )
            case "snapshot":
                record = self.ensure_success(
                    self.run_cli(
                        step["name"],
                        "snapshot",
                        "-s",
                        self.session,
                        "--trim",
                        "--no-cursor",
                    )
                )
                snapshot_path = self.output_dir / f"{step['name']}_{self.size}.txt"
                snapshot_path.write_text(self.strip_ansi(record.stdout))
                self.snapshots[step["name"]] = str(snapshot_path)
            case "press":
                keys = [str(key) for key in step["keys"]]
                self.ensure_success(
                    self.run_cli(
                        step.get("name", "press"),
                        "press",
                        *keys,
                        "-s",
                        self.session,
                    )
                )
            case "type":
                self.ensure_success(
                    self.run_cli(
                        step.get("name", "type"),
                        "type",
                        str(step["text"]),
                        "-s",
                        self.session,
                    )
                )
            case "click":
                self.ensure_success(
                    self.run_cli(
                        step.get("name", "click"),
                        "click",
                        str(step["pattern"]),
                        "-s",
                        self.session,
                        "--timeout",
                        str(step.get("timeout_ms", 5000)),
                        timeout_seconds=max(10, int(step.get("timeout_ms", 5000)) / 1000 + 5),
                    )
                )
            case "resize":
                cols, rows = self.parse_size(step.get("size") or step["value"])
                self.size = f"{cols}x{rows}"
                self.ensure_success(
                    self.run_cli(
                        step.get("name", "resize"),
                        "resize",
                        str(cols),
                        str(rows),
                        "-s",
                        self.session,
                    )
                )
            case "sleep":
                time.sleep(float(step["seconds"]))
            case "capture-file":
                source_path = Path(step["path"])
                payload = source_path.read_bytes()
                capture_path = self.output_dir / f"{step['name']}{source_path.suffix or '.capture'}"
                capture_path.write_bytes(payload)
                self.captures[step["name"]] = {
                    "source_path": str(source_path),
                    "path": str(capture_path),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            case other:
                raise ValueError(f"Unsupported action: {other}")

    def run_cli(
        self,
        step: str,
        *args: str,
        allow_failure: bool = False,
        timeout_seconds: float = 10,
    ) -> CommandRecord:
        completed = subprocess.run(
            [self.bun_path, self.cli_path, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            env={**os.environ, "TUISTORY_PORT": str(self.daemon_port)},
        )
        record = CommandRecord(
            step=step,
            argv=(self.bun_path, self.cli_path, *args),
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed=time.monotonic() - self.started_at,
        )
        self.records.append(record)
        if completed.returncode != 0 and not allow_failure:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "tuistory command failed")
        return record

    @staticmethod
    def ensure_success(record: CommandRecord) -> CommandRecord:
        if record.returncode != 0:
            raise RuntimeError(record.stderr.strip() or record.stdout.strip() or "command failed")
        return record

    @classmethod
    def strip_ansi(cls, text: str) -> str:
        return cls.ANSI_PATTERN.sub("", text).replace("\r", "")

    @staticmethod
    def parse_size(size: str) -> tuple[int, int]:
        delimiter = "x" if "x" in size else "X"
        cols, rows = size.split(delimiter, 1)
        return int(cols), int(rows)

    def wait_for_app_exit(self, step_timeout_ms: int) -> dict[str, Any]:
        deadline = time.monotonic() + (step_timeout_ms / 1000)
        while time.monotonic() < deadline:
            if app_exit := self.read_app_exit():
                return app_exit
            time.sleep(0.1)
        raise TimeoutError(f"Timed out waiting for app exit file: {self.app_exit_path}")

    def read_app_exit(self) -> dict[str, Any] | None:
        if not self.app_exit_path.exists():
            return None
        return json.loads(self.app_exit_path.read_text())

    def write_state(self, app_exit: dict[str, Any] | None, error: dict[str, Any] | None) -> None:
        state_path = self.output_dir / "state.json"
        state_path.write_text(json.dumps({
            "session": self.session,
            "size": self.size,
            "scenario_path": str(self.scenario_path),
            "snapshots": self.snapshots,
            "captures": self.captures,
            "commands": [record.as_dict() for record in self.records],
            "app_exit": app_exit,
            "error": error,
        }))

    @classmethod
    def main(cls) -> int:
        session, output_dir, size, scenario_path, bun_path, cli_path, daemon_port, app_exit_path, started_at = sys.argv[1:10]
        return cls(
            session=session,
            output_dir=Path(output_dir),
            size=size,
            scenario_path=Path(scenario_path),
            bun_path=bun_path,
            cli_path=cli_path,
            daemon_port=int(daemon_port),
            app_exit_path=Path(app_exit_path),
            started_at=float(started_at),
        ).run()


if __name__ == "__main__":
    raise SystemExit(ScenarioDriver.main())

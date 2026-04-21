from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MitmConfig:
    port: int
    confdir: Path
    scenario_path: Path


@dataclass(frozen=True)
class HarnessResult:
    completed: subprocess.CompletedProcess[str]
    output_dir: Path
    state: dict[str, Any]

    @property
    def returncode(self) -> int:
        return self.completed.returncode

    @property
    def app_returncode(self) -> int | None:
        if not (app_exit := self.state.get("app_exit")):
            return None
        return int(app_exit["returncode"])

    def snapshot_path(self, step: str) -> Path:
        return Path(self.state["snapshots"][step])

    def snapshot(self, step: str) -> str:
        return self.snapshot_path(step).read_text()

    def json_lines(self, name: str) -> list[dict[str, Any]]:
        path = self.output_dir / name
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def fake_commands(self) -> list[dict[str, Any]]:
        return self.json_lines("fake-bin.jsonl")

    def addon_requests(self) -> list[dict[str, Any]]:
        return self.json_lines("addon.jsonl")


class HarnessRunner:
    def __init__(self, mitm: MitmConfig, wrapper_path: Path, fake_bin_dir: Path) -> None:
        self.mitm = mitm
        self.wrapper_path = wrapper_path
        self.fake_bin_dir = fake_bin_dir

    @staticmethod
    def sanitize(name: str) -> str:
        return re.sub(r"[^a-z0-9_-]+", "-", name.lower())

    @staticmethod
    def write_home_files(home: Path, scenario: dict[str, Any]) -> None:
        for relative_path, payload in dict(scenario.get("home_files", {})).items():
            target = home / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            match payload:
                case {"content": content, "mode": mode}:
                    target.write_text(str(content))
                    target.chmod(int(mode))
                case {"content": content}:
                    target.write_text(str(content))
                case _:
                    target.write_text(str(payload))

    def run(
        self,
        tmp_path: Path,
        scenario_name: str,
        scenario: dict[str, Any],
        size: str = "80x24",
    ) -> HarnessResult:
        slug = self.sanitize(scenario_name)
        output_dir = tmp_path / slug / "out"
        home_dir = tmp_path / slug / "home"
        output_dir.mkdir(parents=True, exist_ok=True)
        home_dir.mkdir(parents=True, exist_ok=True)
        self.write_home_files(home_dir, scenario)
        scenario_data = {
            **scenario,
            "addon_log_path": str(output_dir / "addon.jsonl"),
        }
        self.mitm.scenario_path.write_text(json.dumps(scenario_data))
        completed = subprocess.run(
            [
                str(self.wrapper_path),
                slug,
                str(output_dir),
                size,
                str(self.mitm.port),
                str(self.mitm.confdir),
                str(self.fake_bin_dir),
                str(home_dir),
                str(self.mitm.scenario_path),
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
            "app_exit": None,
            "error": {"type": "missing-state", "message": "wrapper did not write state.json"},
        }
        self.mitm.scenario_path.write_text("{}")
        return HarnessResult(completed=completed, output_dir=output_dir, state=state)

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

import pytest

from tests.tuistory.harness import HarnessRunner, MitmConfig

CLIENT_ROOT = Path(__file__).resolve().parents[2]
WRAPPER_PATH = CLIENT_ROOT / "tests" / "tuistory" / "bin" / "run_scenario.sh"
FAKE_BIN_DIR = CLIENT_ROOT / "tests" / "tuistory" / "_fixtures" / "fake-bin"
STUBS_PATH = CLIENT_ROOT / "tests" / "tuistory" / "stubs.py"


class MitmOutput:
    PORT_PATTERN = re.compile(r"listening at .*:(\d+)")

    def __init__(self, stream: IO[str] | None, log_path: Path) -> None:
        self.stream = stream
        self.log_path = log_path
        self.lines: list[str] = []
        self.thread = threading.Thread(target=self.read, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def read(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        assert self.stream is not None
        with self.log_path.open("a") as handle:
            for line in self.stream:
                handle.write(line)
                handle.flush()
                self.lines.append(line.rstrip("\n"))

    def wait_for_port(self, timeout: float) -> int:
        deadline = time.monotonic() + timeout
        index = 0
        while time.monotonic() < deadline:
            while index < len(self.lines):
                line = self.lines[index]
                index += 1
                if match := self.PORT_PATTERN.search(line):
                    return int(match.group(1))
            time.sleep(0.05)
        raise TimeoutError("\n".join(self.lines))


@dataclass(frozen=True)
class MitmSession:
    port: int
    confdir: Path
    scenario_path: Path
    process: subprocess.Popen[str]
    output: MitmOutput


@pytest.fixture(scope="session")
def mitm(tmp_path_factory: pytest.TempPathFactory) -> Iterator[MitmSession]:
    root = tmp_path_factory.mktemp("tuistory-mitm")
    confdir = root / "confdir"
    confdir.mkdir(parents=True, exist_ok=True)
    scenario_path = root / "scenario.json"
    scenario_path.write_text("{}")
    process = subprocess.Popen(
        [
            "mitmdump",
            "-p",
            "0",
            "--set",
            f"confdir={confdir}",
            "-s",
            str(STUBS_PATH),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        start_new_session=True,
        cwd=str(CLIENT_ROOT),
        env={**os.environ, "CC_SENTIMENT_SCENARIO": str(scenario_path)},
    )
    output = MitmOutput(process.stdout, root / "mitmdump.log")
    output.start()
    port = output.wait_for_port(10)
    cert_path = confdir / "mitmproxy-ca-cert.pem"
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if cert_path.exists():
            break
        time.sleep(0.05)
    else:
        raise TimeoutError(f"Timed out waiting for mitmproxy CA at {cert_path}")
    session = MitmSession(
        port=port,
        confdir=confdir,
        scenario_path=scenario_path,
        process=process,
        output=output,
    )
    try:
        yield session
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture
def harness(mitm: MitmSession) -> HarnessRunner:
    return HarnessRunner(
        mitm=MitmConfig(port=mitm.port, confdir=mitm.confdir, scenario_path=mitm.scenario_path),
        wrapper_path=WRAPPER_PATH,
        fake_bin_dir=FAKE_BIN_DIR,
    )

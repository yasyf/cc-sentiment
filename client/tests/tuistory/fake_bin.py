from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.tuistory.scenario import ScenarioFile


class FakeCommand:
    @staticmethod
    def log_path() -> Path:
        return Path(os.environ["CC_SENTIMENT_FAKE_BIN_LOG"])

    @classmethod
    def append_log(
        cls,
        tool: str,
        argv: list[str],
        stdin_text: str,
        returncode: int,
        stdout: str,
        stderr: str,
        passthrough: bool,
    ) -> None:
        path = cls.log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps({
                "tool": tool,
                "argv": argv,
                "stdin": stdin_text,
                "cwd": os.getcwd(),
                "returncode": returncode,
                "stdout": stdout,
                "stderr": stderr,
                "passthrough": passthrough,
            }))
            handle.write("\n")

    @staticmethod
    def real_tool(tool: str) -> str | None:
        match tool:
            case "gpg":
                return os.environ.get("CC_SENTIMENT_REAL_GPG")
            case "gh":
                return os.environ.get("CC_SENTIMENT_REAL_GH")
            case _:
                return None

    @classmethod
    def run_passthrough(
        cls,
        tool: str,
        real_tool: str,
        argv: list[str],
        stdin_text: str,
    ) -> int:
        result = subprocess.run(
            [real_tool, *argv],
            input=stdin_text,
            text=True,
            capture_output=True,
            timeout=30,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        cls.append_log(
            tool=tool,
            argv=argv,
            stdin_text=stdin_text,
            returncode=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            passthrough=True,
        )
        return result.returncode

    @classmethod
    def run_fake(
        cls,
        tool: str,
        argv: list[str],
        stdin_text: str,
        rule: dict[str, Any] | None,
    ) -> int:
        result = rule or {}
        stdout = str(result.get("stdout", ""))
        stderr = str(result.get("stderr", ""))
        returncode = int(result.get("returncode", 0 if tool == "gpg" else 1))
        sys.stdout.write(stdout)
        sys.stderr.write(stderr)
        cls.append_log(
            tool=tool,
            argv=argv,
            stdin_text=stdin_text,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            passthrough=False,
        )
        return returncode

    @classmethod
    def main(cls) -> int:
        tool = sys.argv[1]
        argv = sys.argv[2:]
        stdin_text = "" if sys.stdin.isatty() else sys.stdin.read()
        scenario = ScenarioFile.load_from_env()
        rule = ScenarioFile.match_process_rule(
            ScenarioFile.process_rules(scenario, tool),
            argv,
            stdin_text,
        )
        if rule and rule.get("passthrough") and (real_tool := cls.real_tool(tool)):
            return cls.run_passthrough(tool, real_tool, argv, stdin_text)
        if tool == "gpg" and rule is None and (real_tool := cls.real_tool(tool)):
            return cls.run_passthrough(tool, real_tool, argv, stdin_text)
        return cls.run_fake(tool, argv, stdin_text, rule)


if __name__ == "__main__":
    raise SystemExit(FakeCommand.main())

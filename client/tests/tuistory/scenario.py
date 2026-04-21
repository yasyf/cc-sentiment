from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class ScenarioFile:
    @staticmethod
    def path_from_env() -> Path:
        return Path(os.environ["CC_SENTIMENT_SCENARIO"])

    @classmethod
    def load_from_env(cls) -> dict[str, Any]:
        return cls.load(cls.path_from_env())

    @staticmethod
    def load(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        if not (text := path.read_text()).strip():
            return {}
        return json.loads(text)

    @staticmethod
    def steps(data: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        return tuple(data.get("steps", ()))

    @staticmethod
    def http_rules(data: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        return tuple(data.get("http", ()))

    @staticmethod
    def process_rules(data: dict[str, Any], tool: str) -> tuple[dict[str, Any], ...]:
        return tuple(data.get("fake", {}).get(tool, ()))

    @staticmethod
    def addon_log_path(data: dict[str, Any]) -> Path | None:
        return Path(path) if (path := data.get("addon_log_path")) else None

    @staticmethod
    def match_process_rule(
        rules: tuple[dict[str, Any], ...],
        argv: list[str],
        stdin_text: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                rule
                for rule in rules
                if ScenarioFile.process_rule_matches(rule, argv, stdin_text)
            ),
            None,
        )

    @staticmethod
    def process_rule_matches(
        rule: dict[str, Any],
        argv: list[str],
        stdin_text: str,
    ) -> bool:
        if exact := rule.get("argv"):
            return list(exact) == argv and ScenarioFile.stdin_matches(rule, stdin_text)
        if prefix := rule.get("argv_prefix"):
            return argv[: len(prefix)] == list(prefix) and ScenarioFile.stdin_matches(rule, stdin_text)
        if contains := rule.get("contains"):
            return all(part in argv for part in contains) and ScenarioFile.stdin_matches(rule, stdin_text)
        return ScenarioFile.stdin_matches(rule, stdin_text)

    @staticmethod
    def stdin_matches(rule: dict[str, Any], stdin_text: str) -> bool:
        if expected := rule.get("stdin_contains"):
            return expected in stdin_text
        return True

    @staticmethod
    def match_http_rule(
        rules: tuple[dict[str, Any], ...],
        method: str,
        url: str,
        host: str,
        path: str,
    ) -> dict[str, Any] | None:
        return next(
            (
                rule
                for rule in rules
                if ScenarioFile.http_rule_matches(rule, method, url, host, path)
            ),
            None,
        )

    @staticmethod
    def http_rule_matches(
        rule: dict[str, Any],
        method: str,
        url: str,
        host: str,
        path: str,
    ) -> bool:
        match_rule = rule.get("match", {})
        if match_rule.get("method", method).upper() != method.upper():
            return False
        if match_url := match_rule.get("url"):
            return match_url == url
        if match_host := match_rule.get("host"):
            if match_host != host:
                return False
        if match_path := match_rule.get("path"):
            if match_path != path:
                return False
        return True

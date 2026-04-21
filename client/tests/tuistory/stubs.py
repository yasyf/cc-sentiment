from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from mitmproxy import http

sys.path.append(str(Path(__file__).resolve().parents[2]))

from tests.tuistory.scenario import ScenarioFile


class ScenarioStubs:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.counts: dict[str, int] = {}
        self.signature = ""

    def _reset_if_needed(self, scenario: dict[str, Any]) -> None:
        signature = json.dumps(scenario, sort_keys=True)
        if signature == self.signature:
            return
        self.signature = signature
        self.started = time.monotonic()
        self.counts = {}

    def request(self, flow: http.HTTPFlow) -> None:
        scenario = ScenarioFile.load_from_env()
        self._reset_if_needed(scenario)
        rules = ScenarioFile.http_rules(scenario)
        if not (rule := ScenarioFile.match_http_rule(
            rules,
            flow.request.method,
            flow.request.pretty_url,
            flow.request.host,
            flow.request.path,
        )):
            self.write_log(
                ScenarioFile.addon_log_path(scenario),
                {
                    "method": flow.request.method,
                    "url": flow.request.pretty_url,
                    "path": flow.request.path,
                    "matched": False,
                    "elapsed": time.monotonic() - self.started,
                },
            )
            flow.response = http.Response.make(
                599,
                b"No scenario stub matched this request.",
                {"content-type": "text/plain"},
            )
            return
        key = str(rule.get("name") or rule.get("match", {}).get("url") or flow.request.pretty_url)
        count = self.counts.get(key, 0)
        self.counts[key] = count + 1
        elapsed = time.monotonic() - self.started
        response = self.select_response(rule, count, elapsed)
        if delay_ms := response.get("delay_ms"):
            time.sleep(float(delay_ms) / 1000)
        if response.get("disconnect"):
            self.write_log(
                ScenarioFile.addon_log_path(scenario),
                {
                    "method": flow.request.method,
                    "url": flow.request.pretty_url,
                    "path": flow.request.path,
                    "matched": True,
                    "count": count + 1,
                    "elapsed": elapsed,
                    "disconnect": True,
                },
            )
            flow.kill()
            return
        body, headers = self.build_response(response)
        flow.response = http.Response.make(
            int(response.get("status", 200)),
            body,
            headers,
        )
        self.write_log(
            ScenarioFile.addon_log_path(scenario),
            {
                "method": flow.request.method,
                "url": flow.request.pretty_url,
                "path": flow.request.path,
                "matched": True,
                "count": count + 1,
                "elapsed": elapsed,
                "status": int(response.get("status", 200)),
            },
        )

    @staticmethod
    def select_response(rule: dict[str, Any], count: int, elapsed: float) -> dict[str, Any]:
        responses = tuple(rule.get("responses", ())) or (rule,)
        timed = [response for response in responses if (after := response.get("after_seconds")) is not None and elapsed >= float(after)]
        if timed:
            return timed[-1]
        expanded: list[dict[str, Any]] = [
            response
            for response in responses
            for _ in range(int(response.get("repeat", 1)))
        ]
        return expanded[min(count, len(expanded) - 1)]

    @staticmethod
    def build_response(response: dict[str, Any]) -> tuple[bytes, dict[str, str]]:
        headers = {str(key): str(value) for key, value in dict(response.get("headers", {})).items()}
        if "json" in response:
            headers.setdefault("content-type", "application/json")
            return json.dumps(response["json"]).encode(), headers
        if "text" in response:
            return str(response["text"]).encode(), headers
        if "body" in response:
            return str(response["body"]).encode(), headers
        return b"", headers

    @staticmethod
    def write_log(path: Path | None, entry: dict[str, Any]) -> None:
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(json.dumps(entry))
            handle.write("\n")


addons = [ScenarioStubs()]

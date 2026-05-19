from __future__ import annotations

import subprocess

from cc_sentiment.observability import CrashReporter


def make_cpe(label: str) -> subprocess.CalledProcessError:
    return subprocess.CalledProcessError(
        returncode=1, cmd=["claude", "-p", label], output=b"out", stderr=b"err",
    )


class TestIterCalledProcessErrors:
    def test_plain(self) -> None:
        cpe = make_cpe("plain")
        result = list(CrashReporter.iter_called_process_errors(cpe))
        assert result == [cpe]

    def test_no_match(self) -> None:
        result = list(CrashReporter.iter_called_process_errors(ValueError("nope")))
        assert result == []

    def test_cause_chained(self) -> None:
        cpe = make_cpe("cause")
        try:
            try:
                raise cpe
            except subprocess.CalledProcessError as e:
                raise RuntimeError("wrapper") from e
        except RuntimeError as e:
            result = list(CrashReporter.iter_called_process_errors(e))
        assert result == [cpe]

    def test_context_chained(self) -> None:
        cpe = make_cpe("context")
        try:
            try:
                raise cpe
            except subprocess.CalledProcessError:
                raise RuntimeError("wrapper")
        except RuntimeError as e:
            result = list(CrashReporter.iter_called_process_errors(e))
        assert result == [cpe]

    def test_exception_group_wrapped(self) -> None:
        cpe = make_cpe("eg")
        eg = BaseExceptionGroup("wrap", [cpe])
        result = list(CrashReporter.iter_called_process_errors(eg))
        assert result == [cpe]

    def test_nested_group_with_cause(self) -> None:
        inner_cpe = make_cpe("inner")
        outer_cpe = make_cpe("outer")
        try:
            try:
                raise inner_cpe
            except subprocess.CalledProcessError as e:
                raise outer_cpe from e
        except subprocess.CalledProcessError as e:
            eg = BaseExceptionGroup("wrap", [e])
            result = list(CrashReporter.iter_called_process_errors(eg))
        labels = sorted(cpe.cmd[2] for cpe in result)
        assert labels == ["inner", "outer"]


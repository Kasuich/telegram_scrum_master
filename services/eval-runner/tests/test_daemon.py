"""Smoke tests for eval-runner daemon."""

from __future__ import annotations

from eval_runner.daemon import EvalRunnerDaemon


def test_daemon_instantiation() -> None:
    daemon = EvalRunnerDaemon("http://localhost:8001")
    assert daemon._poll_interval == 2.0

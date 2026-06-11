"""Guards that the Agent Debug dashboard stays wired to the emitter contract.

The Trace Explorer panels read structlog lines where the agent_step payload is
nested inside the outer ``message`` field, so every Loki panel must extract
fields in two stages (Line -> message -> inner JSON). The Gantt panel must map
to the exact fields the orchestrator emits (ts / end_ts / label / state).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_DASHBOARD = (
    Path(__file__).resolve().parents[3]
    / "monitoring"
    / "grafana"
    / "dashboards"
    / "05_agent_debug.json"
)


@pytest.fixture(scope="module")
def dashboard() -> dict:
    return json.loads(_DASHBOARD.read_text(encoding="utf-8"))


def _panel(dashboard: dict, panel_id: int) -> dict:
    for panel in dashboard["panels"]:
        if panel.get("id") == panel_id:
            return panel
    raise AssertionError(f"panel {panel_id} not found")


def _extract_sources(panel: dict) -> list[str]:
    return [
        t["options"].get("source")
        for t in panel.get("transformations", [])
        if t.get("id") == "extractFields"
    ]


def test_gantt_maps_to_emitted_fields(dashboard: dict):
    gantt = _panel(dashboard, 30)
    assert gantt["type"] == "marcusolsson-gantt-panel"
    opts = gantt["options"]
    # Field bindings must match orchestrator._log_actions output keys.
    assert opts["startField"] == "ts"
    assert opts["endField"] == "end_ts"
    assert opts["textField"] == "label"
    assert opts["colorByField"] == "state"
    assert opts["groupByField"] == "stage"


def test_gantt_uses_valid_plugin_option_keys(dashboard: dict):
    # marcusolsson-gantt-panel ignores unknown keys; assert we did not regress
    # to the legacy (broken) "groupBy"/"tooltipContent" option names.
    opts = _panel(dashboard, 30)["options"]
    assert "groupBy" not in opts
    assert "tooltipContent" not in opts


def test_loki_trace_panels_extract_two_stages(dashboard: dict):
    # Gantt, Step Details and Trace Finder all read nested JSON: Line -> message.
    for panel_id in (30, 21, 22):
        sources = _extract_sources(_panel(dashboard, panel_id))
        assert sources == ["Line", "message"], (panel_id, sources)


def test_gantt_converts_time_fields(dashboard: dict):
    gantt = _panel(dashboard, 30)
    conversions = [
        c["targetField"]
        for t in gantt["transformations"]
        if t.get("id") == "convertFieldType"
        for c in t["options"]["conversions"]
    ]
    assert "ts" in conversions and "end_ts" in conversions


def test_dashboard_has_message_search_and_status_filters(dashboard: dict):
    names = {v["name"] for v in dashboard["templating"]["list"]}
    assert {"search", "state", "session_id"} <= names
    finder = _panel(dashboard, 22)
    expr = finder["targets"][0]["expr"]
    assert "$search" in expr and "trace_label" in expr


def test_e2e_period_stats_use_range_window(dashboard: dict):
    # Requests / Avg / p50 / p95 / p99 over the dashboard time range ($__range).
    for panel_id in (40, 41, 42, 43, 44):
        panel = _panel(dashboard, panel_id)
        assert panel["type"] == "stat"
        expr = panel["targets"][0]["expr"]
        assert "$__range" in expr
        assert "telegram_bridge_e2e_latency_seconds" in expr
    assert "0.95" in _panel(dashboard, 43)["targets"][0]["expr"]
    assert "0.99" in _panel(dashboard, 44)["targets"][0]["expr"]


def test_panels_do_not_overlap(dashboard: dict):
    occupied: dict[tuple[int, int], int] = {}
    for panel in dashboard["panels"]:
        g = panel["gridPos"]
        assert g["x"] + g["w"] <= 24, panel["id"]
        for yy in range(g["y"], g["y"] + g["h"]):
            for xx in range(g["x"], g["x"] + g["w"]):
                assert (xx, yy) not in occupied, (panel["id"], occupied.get((xx, yy)))
                occupied[(xx, yy)] = panel["id"]

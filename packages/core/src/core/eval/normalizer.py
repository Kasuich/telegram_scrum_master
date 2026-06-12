"""Normalize AgentResult steps into eval operations."""

from __future__ import annotations

from typing import Any

from core.eval.schemas import EvalOperation, NormalizedAgentOutput

_CREATE_TOOLS = frozenset({"tracker_create_issue", "CreateIssue"})
_UPDATE_TOOLS = frozenset({"UpdateIssue", "tracker_patch_issue", "tracker_update_issue"})
_SEARCH_TOOLS = frozenset(
    {"GetIssues", "tracker_find_issues", "tracker_search_issues", "SearchEntities"}
)
_COMMENT_TOOLS = frozenset({"CreateComment", "tracker_comment_issue"})
_TRANSITION_TOOLS = frozenset(
    {"ChangeIssueStatus", "tracker_transition_issue", "tracker_close_issue"}
)
_LINK_TOOLS = frozenset({"link_issues", "BulkUpdate"})


def _step_tool_args(step: dict[str, Any]) -> dict[str, Any]:
    """Extract tool arguments from a step (ReAct uses tool_args, not arguments)."""
    for key in ("tool_args", "arguments", "args"):
        val = step.get(key)
        if isinstance(val, dict):
            return val
    return {}


def _tool_to_operation(tool_name: str, args: dict[str, Any], result: Any) -> EvalOperation | None:
    if tool_name in _CREATE_TOOLS:
        return EvalOperation(
            operation="create_task",
            payload={
                "summary": args.get("summary"),
                "description": args.get("description"),
                "assignee": args.get("assignee"),
                "priority": args.get("priority"),
                "queue": args.get("queue"),
                "deadline": args.get("deadline"),
                "parent": args.get("parent"),
                "issue_type": args.get("issue_type") or args.get("type"),
            },
        )
    if tool_name in _UPDATE_TOOLS:
        key = args.get("issueKey") or args.get("key") or args.get("issue_key")
        return EvalOperation(
            operation="update_task",
            task_key=str(key) if key else None,
            payload={k: v for k, v in args.items() if k not in {"issueKey", "key", "issue_key"}},
        )
    if tool_name in _SEARCH_TOOLS:
        query = str(args.get("query") or args.get("searchQuery") or "")
        used: list[str] = []
        if isinstance(result, dict):
            for issue in result.get("issues") or []:
                if isinstance(issue, dict) and issue.get("key"):
                    used.append(str(issue["key"]))
        return EvalOperation(operation="search_tasks", query=query, result_used=used)
    if tool_name in _COMMENT_TOOLS:
        key = args.get("issueKey") or args.get("key") or args.get("issue_key")
        text = args.get("text") or args.get("comment")
        return EvalOperation(
            operation="comment_task",
            task_key=str(key) if key else None,
            payload={"comment": text},
        )
    if tool_name in _TRANSITION_TOOLS:
        key = args.get("issueKey") or args.get("key") or args.get("issue_key")
        return EvalOperation(
            operation="transition_task",
            task_key=str(key) if key else None,
            payload={"transition": args.get("transition") or args.get("status")},
        )
    if tool_name in _LINK_TOOLS:
        return EvalOperation(operation="link_tasks", payload=dict(args))
    if tool_name == "tracker_create_epic":
        return EvalOperation(
            operation="create_task", payload={"summary": args.get("summary"), "issue_type": "epic"}
        )
    return None


def normalize_agent_output(raw: dict[str, Any]) -> NormalizedAgentOutput:
    steps = raw.get("steps") or []
    operations: list[EvalOperation] = []
    pending_calls: dict[str, dict[str, Any]] = {}

    for step in steps:
        kind = step.get("kind")
        tool = step.get("tool_name") or ""
        if kind == "tool_call":
            pending_calls[tool] = _step_tool_args(step)
        elif kind == "tool_result":
            args = pending_calls.pop(tool, _step_tool_args(step))
            result = step.get("result")
            op = _tool_to_operation(tool, args if isinstance(args, dict) else {}, result)
            if op is not None:
                operations.append(op)

    reply = raw.get("reply") or ""
    clarification = raw.get("clarification")
    final = reply or clarification or ""
    write_ops = {
        o.operation
        for o in operations
        if o.operation not in {"search_tasks", "noop", "ask_clarification"}
    }

    if not write_ops and clarification:
        operations.append(
            EvalOperation(operation="ask_clarification", payload={"text": clarification})
        )
    elif not write_ops and not operations and final:
        operations.append(EvalOperation(operation="noop", payload={"text": final}))

    if raw.get("pending_confirm"):
        pc = raw["pending_confirm"]
        if isinstance(pc, dict):
            op = _tool_to_operation(
                str(pc.get("tool_name", "")),
                pc.get("tool_args") or {},
                None,
            )
            if op is not None:
                operations.append(op)

    return NormalizedAgentOutput(operations=operations, final_answer=final or None)


_TRACE_KINDS = frozenset(
    {"stage", "tool_call", "tool_result", "tool_error", "confirm_wait", "clarification", "final"}
)
_MAX_TRACE_STEPS = 60


def _trim(value: Any, limit: int = 120) -> Any:
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, dict):
        return {k: _trim(v, limit) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return [_trim(v, limit) for v in value[:8]]
    return value


def _result_status(result: Any) -> str:
    if result is None:
        return "empty"
    if isinstance(result, dict):
        if result.get("error"):
            return f"error: {str(result['error'])[:80]}"
        if "count" in result:
            return f"ok (count={result.get('count')})"
        return "ok"
    if isinstance(result, list):
        return f"ok (n={len(result)})"
    return "ok"


def summarize_trajectory(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Compact, judge-friendly view of the agent's reasoning trajectory.

    Surfaces stage transitions, tool calls (with trimmed args), tool results
    (ok/error/empty/count), and errors — enough for a judge to see *how* the
    agent reasoned and where it looped, retried, or stalled, without dumping raw
    payloads. Bounded so a runaway loop can't blow up the judge prompt.
    """
    steps = raw.get("steps") or []
    out: list[dict[str, Any]] = []
    for i, step in enumerate(steps):
        kind = step.get("kind")
        if kind not in _TRACE_KINDS:
            continue
        entry: dict[str, Any] = {"i": i, "kind": kind}
        if kind == "stage":
            entry["stage"] = step.get("stage")
        tool = step.get("tool_name")
        if tool:
            entry["tool"] = tool
        if kind == "tool_call":
            args = step.get("tool_args") or step.get("arguments") or {}
            if isinstance(args, dict):
                entry["args"] = _trim(args)
        elif kind == "tool_result":
            entry["result"] = _result_status(step.get("result"))
        elif kind == "tool_error":
            entry["error"] = str(step.get("error") or "")[:160]
        out.append(entry)
        if len(out) >= _MAX_TRACE_STEPS:
            out.append({"truncated": True, "total_steps": len(steps)})
            break
    return out

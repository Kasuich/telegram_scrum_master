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

"""In-memory fake Yandex Tracker for eval dry-run."""

from __future__ import annotations

import asyncio
import copy
import random
import re
from contextvars import ContextVar, Token
from typing import Any

from core.eval.tracker_profile import ToolLatencyProfile, classify_request, classify_tool
from core.tracker_tool_helpers import issue_summary

_fake_store_var: ContextVar[FakeTrackerStore | None] = ContextVar(
    "eval_fake_tracker_store", default=None
)


class FakeTrackerStore:
    """Isolated in-memory Tracker state for a single eval case."""

    def __init__(
        self,
        *,
        queue: str = "DARKHORSE",
        initial_state: dict[str, Any] | None = None,
        latency_profile: ToolLatencyProfile | None = None,
        seed: str | None = None,
    ) -> None:
        self.queue = queue.upper()
        self._tasks: dict[str, dict[str, Any]] = {}
        self._comments: dict[str, list[dict[str, Any]]] = {}
        self._counter = 0
        # Per-case seeded RNG → identical latency replay across runs. random.Random
        # hashes a str/bytes seed deterministically, so the case id works directly.
        self._latency = latency_profile
        self._rng = random.Random(seed)
        self._latency_samples: dict[str, list[float]] = {}
        if initial_state:
            self.seed(initial_state)

    def seed(self, initial_state: dict[str, Any]) -> None:
        for task in initial_state.get("tasks") or []:
            key = str(task.get("key", "")).upper()
            if not key:
                continue
            self._tasks[key] = self._normalize_task(key, task)
            prefix, _, num = key.partition("-")
            if prefix == self.queue and num.isdigit():
                self._counter = max(self._counter, int(num))

    def _normalize_task(self, key: str, raw: dict[str, Any]) -> dict[str, Any]:
        status = raw.get("status", "open")
        if isinstance(status, str):
            status_obj = {"key": status.lower().replace(" ", "_"), "display": status}
        else:
            status_obj = status
        priority = raw.get("priority", "normal")
        if isinstance(priority, str):
            priority_obj = {"key": priority, "display": priority}
        else:
            priority_obj = priority
        assignee = raw.get("assignee")
        if isinstance(assignee, str):
            assignee_obj = {"id": assignee, "display": assignee}
        else:
            assignee_obj = assignee
        issue_type = raw.get("type") or raw.get("issue_type") or "task"
        if isinstance(issue_type, str):
            type_obj = {"key": issue_type, "display": issue_type}
        else:
            type_obj = issue_type
        return {
            "key": key,
            "queue": {"key": self.queue, "display": self.queue},
            "summary": raw.get("summary", ""),
            "description": raw.get("description", ""),
            "status": status_obj,
            "priority": priority_obj,
            "assignee": assignee_obj,
            "type": type_obj,
            "tags": list(raw.get("tags") or []),
            "deadline": raw.get("deadline"),
            "storyPoints": raw.get("story_points") or raw.get("storyPoints"),
            "parent": raw.get("parent"),
        }

    def _next_key(self) -> str:
        self._counter += 1
        return f"{self.queue}-{self._counter}"

    def dump_state(self) -> dict[str, Any]:
        return {"tasks": [copy.deepcopy(t) for t in self._tasks.values()]}

    # -- Latency simulation --

    async def _simulate(self, op: str) -> None:
        """Sleep a realistic, seeded wall-time for one tool call of kind ``op``."""
        if self._latency is None:
            return
        ms = self._latency.sample_ms(op, self._rng)
        seconds = ms / 1000.0
        self._latency_samples.setdefault(op, []).append(seconds)
        if seconds > 0:
            await asyncio.sleep(seconds)

    def latency_summary(self) -> dict[str, Any]:
        """Per-op + overall simulated latency, for the per-tool latency report."""
        from core.eval.metrics import percentile

        by_op: dict[str, Any] = {}
        all_samples: list[float] = []
        for op, samples in self._latency_samples.items():
            if not samples:
                continue
            all_samples.extend(samples)
            by_op[op] = {
                "count": len(samples),
                "total_sec": round(sum(samples), 3),
                "avg_sec": round(sum(samples) / len(samples), 3),
                "p50_sec": round(percentile(samples, 0.5) or 0.0, 3),
                "p95_sec": round(percentile(samples, 0.95) or 0.0, 3),
            }
        return {
            "enabled": self._latency is not None,
            "total_sec": round(sum(all_samples), 3),
            "calls": len(all_samples),
            "by_op": by_op,
        }

    # -- REST-like API used by TrackerClient routing --

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        method = method.upper()
        body = kwargs.get("json") or {}
        await self._simulate(classify_request(method, path))

        if method == "GET" and path.startswith("/issues/") and "/comments" not in path:
            key = path.split("/issues/", 1)[1].split("/", 1)[0].upper()
            task = self._tasks.get(key)
            if task is None:
                from core.tracker import TrackerError

                raise TrackerError(f"Not found: {key}", status_code=404)
            return copy.deepcopy(task)

        if method == "POST" and path.rstrip("/") == "/issues/_search":
            return await self.search_issues(body.get("query", ""))

        if method == "POST" and path.rstrip("/") == "/issues":
            return await self.create_issue(body)

        if method == "PATCH" and path.startswith("/issues/"):
            key = path.split("/issues/", 1)[1].split("/", 1)[0].upper()
            return await self.patch_issue(key, body)

        if method == "POST" and "/comments" in path:
            key = path.split("/issues/", 1)[1].split("/", 1)[0].upper()
            return await self.comment_issue(key, body.get("text", ""))

        if method == "POST" and "/transitions/" in path:
            key = path.split("/issues/", 1)[1].split("/", 1)[0].upper()
            parts = path.split("/transitions/", 1)[1].split("/", 1)
            transition = parts[0]
            return await self.transition_issue(key, transition, comment=body.get("comment"))

        if method == "GET" and path.startswith("/issues/") and "/comments" in path:
            key = path.split("/issues/", 1)[1].split("/", 1)[0].upper()
            return self._comments.get(key, [])

        return None

    async def create_issue(self, body: dict[str, Any]) -> dict[str, Any]:
        key = self._next_key()
        queue = str(body.get("queue", self.queue)).upper()
        task = {
            "key": key,
            "queue": {"key": queue, "display": queue},
            "summary": body.get("summary", ""),
            "description": body.get("description", ""),
            "status": {"key": "open", "display": "Open"},
            "priority": {
                "key": body.get("priority", "normal"),
                "display": body.get("priority", "normal"),
            },
            "assignee": (
                {"id": body["assignee"], "display": body["assignee"]}
                if body.get("assignee")
                else None
            ),
            "type": {"key": body.get("type", "task"), "display": body.get("type", "task")},
            "tags": list(body.get("tags") or []),
            "deadline": body.get("deadline"),
            "storyPoints": body.get("storyPoints"),
            "parent": body.get("parent"),
        }
        self._tasks[key] = task
        return copy.deepcopy(task)

    async def patch_issue(self, issue_key: str, fields: dict[str, Any]) -> dict[str, Any]:
        key = issue_key.upper()
        task = self._tasks.get(key)
        if task is None:
            from core.tracker import TrackerError

            raise TrackerError(f"Not found: {key}", status_code=404)
        if "summary" in fields:
            task["summary"] = fields["summary"]
        if "description" in fields:
            task["description"] = fields["description"]
        if "priority" in fields:
            task["priority"] = {"key": fields["priority"], "display": fields["priority"]}
        if "assignee" in fields:
            task["assignee"] = {"id": fields["assignee"], "display": fields["assignee"]}
        if "type" in fields:
            task["type"] = {"key": fields["type"], "display": fields["type"]}
        if "tags" in fields:
            task["tags"] = list(fields["tags"])
        if "deadline" in fields:
            task["deadline"] = fields["deadline"]
        if "storyPoints" in fields:
            task["storyPoints"] = fields["storyPoints"]
        if "parent" in fields:
            task["parent"] = fields["parent"]
        return copy.deepcopy(task)

    async def comment_issue(self, issue_key: str, text: str) -> dict[str, Any]:
        key = issue_key.upper()
        if key not in self._tasks:
            from core.tracker import TrackerError

            raise TrackerError(f"Not found: {key}", status_code=404)
        comment = {"id": len(self._comments.get(key, [])) + 1, "text": text}
        self._comments.setdefault(key, []).append(comment)
        return comment

    async def transition_issue(
        self, issue_key: str, transition: str, *, comment: str | None = None
    ) -> dict[str, Any]:
        key = issue_key.upper()
        task = self._tasks.get(key)
        if task is None:
            from core.tracker import TrackerError

            raise TrackerError(f"Not found: {key}", status_code=404)
        task["status"] = {"key": transition, "display": transition}
        if comment:
            await self.comment_issue(key, comment)
        return copy.deepcopy(task)

    async def search_issues(self, query: str) -> list[dict[str, Any]]:
        del query  # simplified: return all non-terminal for queue
        return [
            copy.deepcopy(t)
            for t in self._tasks.values()
            if t.get("queue", {}).get("key") == self.queue
        ]

    def search_issues_normalized(self, query: str) -> dict[str, Any]:
        issues = [issue_summary(t, detailed=False) for t in self._tasks.values()]
        if query:
            tokens = [t.lower() for t in re.findall(r"\w+", query) if len(t) > 2]
            if tokens:
                filtered = []
                for issue in issues:
                    blob = " ".join(
                        str(issue.get(k, "")) for k in ("key", "summary", "description")
                    ).lower()
                    if any(tok in blob for tok in tokens):
                        filtered.append(issue)
                issues = filtered or issues
        return {
            "count": len(issues),
            "query_used": query,
            "issues": issues,
            "not_found": not issues,
        }

    async def mcp_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        name = tool_name.strip()
        args = arguments or {}
        await self._simulate(classify_tool(name))

        if name in {"GetIssue"}:
            key = str(args.get("issueKey") or args.get("key") or "").upper()
            task = self._tasks.get(key)
            if not task:
                return {"error": f"not found: {key}"}
            return copy.deepcopy(task)

        if name in {"GetIssues", "SearchEntities"}:
            query = str(args.get("query") or args.get("searchQuery") or "")
            return self.search_issues_normalized(query)

        if name == "CreateIssue":
            body = {
                "queue": args.get("queue", self.queue),
                "summary": args.get("summary", ""),
                "description": args.get("description"),
                "priority": args.get("priority"),
                "assignee": args.get("assignee"),
                "type": args.get("type") or args.get("issueType"),
                "tags": args.get("tags"),
                "deadline": args.get("deadline"),
                "parent": args.get("parent"),
            }
            return await self.create_issue({k: v for k, v in body.items() if v is not None})

        if name == "UpdateIssue":
            key = str(args.get("issueKey") or args.get("key") or "").upper()
            patch = {
                k: v for k, v in args.items() if k not in {"issueKey", "key"} and v is not None
            }
            return await self.patch_issue(key, patch)

        if name == "CreateComment":
            key = str(args.get("issueKey") or args.get("key") or "").upper()
            return await self.comment_issue(key, str(args.get("text") or args.get("comment") or ""))

        if name == "ChangeIssueStatus":
            key = str(args.get("issueKey") or args.get("key") or "").upper()
            transition = str(args.get("transition") or args.get("status") or "in_progress")
            return await self.transition_issue(
                key, transition, comment=args.get("comment") or args.get("text")
            )

        return {"ok": True, "tool": name, "arguments": args}


def get_fake_tracker_store() -> FakeTrackerStore | None:
    return _fake_store_var.get()


def set_fake_tracker_store(store: FakeTrackerStore | None) -> Token:
    return _fake_store_var.set(store)


def reset_fake_tracker_store(token: Token) -> None:
    _fake_store_var.reset(token)


def seed_fake_tracker_from_metadata(
    metadata: dict[str, Any], *, default_queue: str
) -> FakeTrackerStore:
    initial = metadata.get("initial_state") or {}
    queue = str(initial.get("queue") or default_queue).upper()
    profile: ToolLatencyProfile | None = None
    if metadata.get("simulate_tool_latency"):
        profile = ToolLatencyProfile(
            scale=float(metadata.get("tool_latency_scale") or 1.0),
            simulate_errors=bool(metadata.get("simulate_tracker_errors")),
        )
    # Seed off the case id so each case replays its latencies identically.
    seed = metadata.get("eval_case_id") or metadata.get("eval_run_id")
    return FakeTrackerStore(
        queue=queue,
        initial_state=initial,
        latency_profile=profile,
        seed=str(seed) if seed else None,
    )

"""
Smoke test: create → get → comment → close an issue in real Yandex Tracker.

Run from packages/core/:
    python examples/07_tracker_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, "src")

from pathlib import Path

for line in Path(".env").read_text().splitlines():
    line = line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    k, _, v = line.partition("=")
    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from core.config import get_config  # noqa: E402
from core.tracker import TrackerClient, TrackerError  # noqa: E402


async def _detect_org_type(token: str, org_id: str) -> str:
    """Auto-detect correct org header type."""
    import httpx

    async with httpx.AsyncClient(timeout=10) as client:
        for org_type in ("cloud", "360"):
            hdr = "X-Cloud-Org-ID" if org_type == "cloud" else "X-Org-ID"
            r = await client.get(
                "https://api.tracker.yandex.net/v3/myself",
                headers={"Authorization": f"OAuth {token}", hdr: org_id},
            )
            if r.status_code == 200:
                return org_type
    raise TrackerError("Could not authenticate with either org type")


async def main() -> None:
    cfg = get_config().tracker
    queue = cfg.tracker_queue

    org_type = await _detect_org_type(cfg.tracker_token, cfg.tracker_org_id)
    print(f"Queue: {queue!r}  Org type detected: {org_type!r}")
    print(f"  (add TRACKER_ORG_TYPE={org_type} to .env to skip auto-detect)")

    async with TrackerClient(org_type=org_type) as client:
        # 1. Check queue
        q = await client.get_queue(queue)
        print(f"\n✅ Queue: {q['key']} — {q.get('name')}")

        # 2. Create issue
        issue = await client.create_issue(
            queue,
            summary="[SMOKE] Тестовая задача от PM-агента",
            description="Автоматически создана smoke-тестом. Можно закрыть.",
            priority="minor",
        )
        key = issue["key"]
        print(f"✅ Created: {key} — {issue['summary']}")
        print(f"   URL: https://tracker.yandex.ru/{key}")

        # 3. Get issue
        fetched = await client.get_issue(key)
        print(f"✅ Get: {fetched['key']} status={fetched.get('status', {}).get('display')}")

        # 4. Comment
        comment = await client.comment_issue(key, "Это автоматический комментарий от PM-агента.")
        print(f"✅ Comment id={comment.get('id')}")

        # 5. Search
        results = await client.search_issues("summary: SMOKE", queue=queue)
        print(f"✅ Search: found {len(results)} issue(s) matching 'SMOKE'")

        print(f"\nДля закрытия задачи выполните close: tracker.transition_issue('{key}', 'close')")
        print("Или закройте вручную в интерфейсе Трекера.")


if __name__ == "__main__":
    asyncio.run(main())

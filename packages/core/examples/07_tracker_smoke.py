"""
Smoke test: full PM workflow in real Yandex Tracker.

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
    print(f"Queue: {queue!r}  Org type: {org_type!r}")

    async with TrackerClient(org_type=org_type) as client:
        meta = await client.get_queue_meta(queue)
        print(f"\n✅ Queue meta: {meta['queue_name']} ({meta['queue_key']})")
        print(f"   Local fields: {len(meta.get('local_fields', []))}")

        issue = await client.create_issue(
            queue,
            summary="[SMOKE] PM agent full fields test",
            description="Smoke test. Safe to close.",
            priority="minor",
            deadline="2026-12-31",
            story_points=1,
            tags=["smoke", "pm-agent"],
        )
        key = issue["key"]
        print(f"\n✅ Created: {key}")
        print(f"   URL: https://tracker.yandex.ru/{key}")

        fetched = await client.get_issue(key)
        print(f"✅ Get: status={fetched.get('status', {}).get('display')}")

        transitions = await client.list_transitions(key)
        print(f"✅ Transitions: {[t.get('display') for t in transitions[:5]]}")

        await client.comment_issue(key, "Smoke comment from PM platform.")
        print("✅ Comment added")

        results = await client.search_issues('Summary: "[SMOKE] PM agent"', queue=queue)
        print(f"✅ Search: {len(results)} issue(s)")

        print(f"\nClose manually or: transition_issue('{key}', 'close')")


if __name__ == "__main__":
    asyncio.run(main())

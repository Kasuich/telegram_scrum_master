from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class SpoolItem:
    id: int
    update_id: int
    payload: dict[str, Any]
    status: str
    attempts: int
    received_at: datetime
    next_attempt_at: datetime
    lease_expires_at: datetime | None
    last_error: str | None
    forwarded_at: datetime | None


class GatewaySpool:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inbound_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    update_id INTEGER NOT NULL UNIQUE,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    received_at TEXT NOT NULL,
                    next_attempt_at TEXT NOT NULL,
                    lease_expires_at TEXT,
                    last_error TEXT,
                    forwarded_at TEXT,
                    last_attempt_at TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_inbound_updates_due
                ON inbound_updates(status, next_attempt_at, lease_expires_at, id);
                """
            )
            conn.commit()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(tz=timezone.utc)

    @staticmethod
    def _iso(dt: datetime | None) -> str | None:
        return dt.isoformat() if dt is not None else None

    @staticmethod
    def _parse(value: str | None) -> datetime | None:
        if not value:
            return None
        return datetime.fromisoformat(value)

    @staticmethod
    def _next_attempt(
        now: datetime,
        attempts: int,
        retry_after_seconds: int | None = None,
    ) -> datetime:
        if retry_after_seconds is not None:
            return now + timedelta(seconds=retry_after_seconds)
        delay = min(300, 2 ** min(max(attempts, 1), 8))
        return now + timedelta(seconds=delay)

    def store_update(self, update_id: int, payload: dict[str, Any], received_at: datetime) -> bool:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO inbound_updates(
                    update_id,
                    payload,
                    status,
                    attempts,
                    received_at,
                    next_attempt_at
                ) VALUES (?, ?, 'pending', 0, ?, ?)
                """,
                (update_id, body, self._iso(received_at), self._iso(received_at)),
            )
            conn.commit()
            return cur.rowcount > 0

    def depth(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM inbound_updates
                WHERE status IN ('pending', 'retry', 'processing')
                """
            ).fetchone()
            return int(row["n"]) if row else 0

    def claim_due(self, *, limit: int, lease_seconds: int) -> list[SpoolItem]:
        now = self._now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        due_statuses = ("pending", "retry", "processing")

        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM inbound_updates
                WHERE status IN (?, ?, ?)
                  AND next_attempt_at <= ?
                  AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                ORDER BY id
                LIMIT ?
                """,
                (
                    due_statuses[0],
                    due_statuses[1],
                    due_statuses[2],
                    self._iso(now),
                    self._iso(now),
                    limit,
                ),
            ).fetchall()
            ids = [int(row["id"]) for row in rows]
            if not ids:
                return []

            for row_id in ids:
                conn.execute(
                    """
                    UPDATE inbound_updates
                    SET status = 'processing',
                        attempts = attempts + 1,
                        lease_expires_at = ?,
                        last_attempt_at = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        self._iso(lease_expires_at),
                        self._iso(now),
                        self._iso(now),
                        row_id,
                    ),
                )
            conn.commit()

            claimed: list[SpoolItem] = []
            for row in rows:
                claimed.append(
                    SpoolItem(
                        id=int(row["id"]),
                        update_id=int(row["update_id"]),
                        payload=json.loads(row["payload"]),
                        status="processing",
                        attempts=int(row["attempts"]) + 1,
                        received_at=self._parse(row["received_at"]) or now,
                        next_attempt_at=self._parse(row["next_attempt_at"]) or now,
                        lease_expires_at=lease_expires_at,
                        last_error=row["last_error"],
                        forwarded_at=self._parse(row["forwarded_at"]),
                    )
                )
            return claimed

    def mark_sent(self, item_id: int) -> None:
        now = self._now()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE inbound_updates
                SET status = 'sent',
                    lease_expires_at = NULL,
                    forwarded_at = ?,
                    last_error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (self._iso(now), self._iso(now), item_id),
            )
            conn.commit()

    def mark_retry(
        self,
        item_id: int,
        *,
        attempts: int,
        error: str,
        retry_after_seconds: int | None = None,
        max_attempts: int = 8,
    ) -> str:
        now = self._now()
        status = "retry" if attempts < max_attempts else "dead_letter"
        next_attempt_at = None
        if status == "retry":
            next_attempt_at = self._next_attempt(now, attempts, retry_after_seconds)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE inbound_updates
                SET status = ?,
                    next_attempt_at = COALESCE(?, next_attempt_at),
                    lease_expires_at = NULL,
                    last_error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    self._iso(next_attempt_at),
                    error,
                    self._iso(now),
                    item_id,
                ),
            )
            conn.commit()
        return status

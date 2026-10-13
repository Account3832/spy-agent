"""Small durable task queue (JSONL file) for the local persistent runner.

Gives: idempotency keys, retries with exponential backoff, restart recovery of tasks that
were running when the process died, and a bounded attempt count. No external services.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

from .config import TZ, state_dir
from .store import read_jsonl

MAX_ATTEMPTS = 5
RUNNING_TIMEOUT_S = 600


class TaskQueue:
    def __init__(self, base: Path | None = None):
        self.path = (base or state_dir()) / "queue.jsonl"

    def _all(self) -> dict[str, dict]:
        latest: dict[str, dict] = {}
        for r in read_jsonl(self.path):
            latest[r["id"]] = r  # last record per id wins (append-only log)
        return latest

    def _put(self, rec: dict):
        from .store import append_jsonl
        append_jsonl(self.path, rec)

    def enqueue(self, kind: str, key: str, due: pd.Timestamp | None = None) -> bool:
        tid = hashlib.sha1(f"{kind}|{key}".encode()).hexdigest()[:12]
        if tid in self._all():
            return False  # idempotent: same kind+key is never queued twice
        self._put({"id": tid, "kind": kind, "key": key, "status": "pending", "attempts": 0,
                   "due": str(due or pd.Timestamp.now(tz=TZ)), "updated": str(pd.Timestamp.now(tz=TZ))})
        return True

    def recover(self, now: pd.Timestamp | None = None) -> int:
        """After a crash/restart, tasks stuck in 'running' go back to pending."""
        now, n = now or pd.Timestamp.now(tz=TZ), 0
        for r in self._all().values():
            if r["status"] == "running" and (now - pd.Timestamp(r["updated"])).total_seconds() > RUNNING_TIMEOUT_S:
                self._put({**r, "status": "pending", "updated": str(now)})
                n += 1
        return n

    def next_due(self, now: pd.Timestamp | None = None) -> dict | None:
        now = now or pd.Timestamp.now(tz=TZ)
        due = [r for r in self._all().values() if r["status"] == "pending" and pd.Timestamp(r["due"]) <= now]
        return sorted(due, key=lambda r: r["due"])[0] if due else None

    def run_one(self, handlers: dict, now: pd.Timestamp | None = None) -> dict | None:
        now = now or pd.Timestamp.now(tz=TZ)
        t = self.next_due(now)
        if t is None:
            return None
        self._put({**t, "status": "running", "updated": str(now)})
        try:
            handlers[t["kind"]](t["key"])
            self._put({**t, "status": "done", "updated": str(pd.Timestamp.now(tz=TZ))})
            return {**t, "status": "done"}
        except Exception as e:  # noqa: BLE001
            att = t["attempts"] + 1
            if att >= MAX_ATTEMPTS:
                rec = {**t, "status": "failed", "attempts": att, "error": f"{type(e).__name__}: {e}"}
            else:
                backoff = pd.Timedelta(seconds=min(900, 30 * 2 ** att))
                rec = {**t, "status": "pending", "attempts": att, "due": str(now + backoff), "error": f"{type(e).__name__}: {e}"}
            rec["updated"] = str(now)
            self._put(rec)
            return rec

    def counts(self) -> dict:
        c: dict[str, int] = {}
        for r in self._all().values():
            c[r["status"]] = c.get(r["status"], 0) + 1
        return c

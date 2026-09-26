"""Event log: jsonl on disk, recent events in memory, and live subscribers."""

from __future__ import annotations

import asyncio
import json
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


class EventLog:
    def __init__(self, path: Path | None, timezone: str | None, keep: int = 200):
        self.path = path
        self.timezone = timezone
        self._recent: deque[dict[str, Any]] = deque(maxlen=keep)
        self._subscribers: set[asyncio.Queue] = set()
        self._listeners: list[Callable[[dict[str, Any]], None]] = []
        if path and path.exists():
            for line in path.read_text().splitlines()[-keep:]:
                try:
                    self._recent.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    def emit(self, level: str, msg: str, job: str | None = None, code: str | None = None) -> dict[str, Any]:
        rec = {
            "t": datetime.now(UTC).isoformat(timespec="seconds"),
            "level": level,
            "code": code,
            "msg": msg,
            "job": job,
        }
        self._recent.append(rec)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        for fn in self._listeners:
            fn(rec)
        for q in list(self._subscribers):
            try:
                q.put_nowait(rec)
            except asyncio.QueueFull:
                pass  # a stalled listener loses events rather than blocking the engine
        return rec

    def recent(self, n: int | None = None) -> list[dict[str, Any]]:
        items = list(self._recent)
        return items[-n:] if n else items

    def add_listener(self, fn: Callable[[dict[str, Any]], None]) -> None:
        """Call `fn` synchronously for every new event (the CLI prints them)."""
        self._listeners.append(fn)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)


def format_record(rec: dict[str, Any], timezone: str | None) -> str:
    t = datetime.fromisoformat(rec["t"])
    t = t.astimezone(ZoneInfo(timezone)) if timezone else t.astimezone()
    return f"{t:%Y-%m-%d %H:%M:%S} {rec['level']:<6} {rec['msg']}"

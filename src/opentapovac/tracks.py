"""Keeping what the robot forgets: its track and its clean records.

The robot keeps one track (`getPathData`) and clears it now and then, at
least when it is carried or relocates (2026-09-26: a 25-minute run left 14
points).  `Track.poll` fetches only the points added since the last poll
(`start_pos`), so polling it every time the status is polled is cheap.
A new `path_id`, or fewer points than already fetched, starts a new segment.

`getCleanRecords` lists the robot's runs (time, area, mop washes, error);
`CleanRecords` appends the new ones to a jsonl file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .codes import error_text
from .mapimg import track_points
from .robot import Robot


class Track:
    def __init__(self, path: Path | None, segments: list[dict[str, Any]] | None = None):
        self.path = path
        #: {"path_id", "n": robot-side entries fetched (header included), "points": [[x, y], ...]}
        self.segments: list[dict[str, Any]] = segments or []

    @classmethod
    def load(cls, path: Path) -> Track:
        return cls(path, json.loads(path.read_text())["segments"])

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"segments": self.segments}))

    def points(self) -> list[list[tuple[int, int]]]:
        return [[(x, y) for x, y in s["points"]] for s in self.segments if s["points"]]

    async def poll(self, robot: Robot) -> int:
        """Fetch the points added since the last poll; the number of new points."""
        seg = self.segments[-1] if self.segments else None
        start = seg["n"] if seg else 0
        d = await robot.path_data(start)
        if seg is None or d.get("path_id") != seg["path_id"] or d.get("total_points", 0) < start:
            if start:
                d = await robot.path_data(0)
            seg = {"path_id": d.get("path_id"), "n": 0, "points": []}
            self.segments.append(seg)
        if not d.get("point_counts"):
            return 0
        new = [list(p) for p in track_points(d)]
        seg["points"] += new
        seg["n"] = d["start_pos"] + d["point_counts"]
        self.save()
        return len(new)


class CleanRecords:
    def __init__(self, path: Path):
        self.path = path
        self._last: int | None = None

    def _last_timestamp(self) -> int:
        if self._last is None:
            self._last = 0
            if self.path.exists():
                for line in self.path.read_text().splitlines():
                    try:
                        self._last = max(self._last, json.loads(line)["timestamp"])
                    except (json.JSONDecodeError, KeyError, TypeError):
                        pass
        return self._last

    async def fetch(self, robot: Robot) -> list[dict[str, Any]]:
        """Records newer than any seen before, oldest first; they are appended to the file."""
        reply = await robot.clean_records()
        records = reply.get("record_list", []) if isinstance(reply, dict) else []
        last = self._last_timestamp()
        new = sorted((r for r in records if r.get("timestamp", 0) > last), key=lambda r: r["timestamp"])
        if new:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                for r in new:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
            self._last = new[-1]["timestamp"]
        return new


def describe_record(r: dict[str, Any]) -> str:
    parts = [f"{r.get('clean_time')} min", f"{r.get('clean_area')} m²"]
    washes = r.get("wash_times")
    if washes:
        parts.append(f"{washes} mop wash" + ("es" if washes != 1 else ""))
    if r.get("error"):
        parts.append(f"error {r['error']} ({error_text(r['error'])})")
    return ", ".join(parts)

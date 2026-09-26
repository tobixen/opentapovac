"""Keeping what the robot forgets: its track and its clean records.

The robot keeps one track (`getPathData`) and clears it now and then, at
least when it is carried or relocates (2026-09-26: a 25-minute run left 14
points).  `Track.poll` fetches only the points added since the last poll
(`start_pos`), so polling it every time the status is polled is cheap.
A new `path_id`, or fewer points than already fetched, starts a new segment.
Each poll marks where its points start with the time and whether the robot
was vacuuming or mopping; the robot's own point types don't tell the two apart.

`getCleanRecords` lists the robot's runs (time, area, mop washes, error);
`CleanRecords` appends the new ones to a jsonl file.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Collection
from pathlib import Path
from typing import Any

from .codes import error_text
from .mapimg import track_points
from .robot import Robot

#: what the map can show: cleaning by vacuum or mop, and all else (moving between areas, heading home, ...)
KINDS = frozenset({"vac", "mop", "move"})
#: point types of cleaning; mapimg.TRACK
CLEANING = {0, 5}


def point_type(x: int, y: int) -> int:
    return (x % 4 << 2) + y % 4


class Track:
    def __init__(self, path: Path | None, segments: list[dict[str, Any]] | None = None):
        self.path = path
        #: {"path_id", "n": robot-side entries fetched (header included), "points": [[x, y], ...],
        #:  "marks": [[index of the first point of a poll, Unix time, "vac" | "mop" | None], ...]}
        self.segments: list[dict[str, Any]] = segments or []
        #: the time of points without marks (tracks saved before them)
        self.mtime = 0.0

    @classmethod
    def load(cls, path: Path) -> Track:
        t = cls(path, json.loads(path.read_text())["segments"])
        t.mtime = path.stat().st_mtime
        return t

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"segments": self.segments}))

    def points(self) -> list[list[tuple[int, int]]]:
        return [[(x, y) for x, y in s["points"]] for s in self.segments if s["points"]]

    def lines(self, since: float = 0, show: Collection[str] = KINDS) -> list[list[tuple[int, int, str | None]]]:
        """The points newer than `since` of the kinds in `show`, as polylines of (x, y, kind).

        A point's line comes from the point before it, which is included even
        when it is not shown.  Kind None: cleaning of unknown kind, shown with "vac" or "mop".
        """
        out: list[list[tuple[int, int, str | None]]] = []
        for s in self.segments:
            marks = s.get("marks") or [[0, self.mtime, None]]
            line: list[tuple[int, int, str | None]] = []
            prev = None
            m = 0
            for i, (x, y) in enumerate(s["points"]):
                while m + 1 < len(marks) and marks[m + 1][0] <= i:
                    m += 1
                kind = marks[m][2] if point_type(x, y) in CLEANING else "move"
                pt = (x, y, kind)
                shown = marks[m][1] >= since and (kind in show if kind else bool({"vac", "mop"} & set(show)))
                if shown:
                    if not line and prev:
                        line.append(prev)
                    line.append(pt)
                elif line:
                    out.append(line)
                    line = []
                prev = pt
            if line:
                out.append(line)
        return out

    async def poll(
        self, robot: Robot, kind: Callable[[], Awaitable[str | None]] | None = None, now: float | None = None
    ) -> int:
        """Fetch the points added since the last poll; the number of new points.

        `kind`: asked for "vac" or "mop", what the robot is cleaning with, when there are new points;
        `now`: Unix time.
        """
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
        if new:
            mark = [len(seg["points"]), time.time() if now is None else now, await kind() if kind else None]
            seg.setdefault("marks", []).append(mark)
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

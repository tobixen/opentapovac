"""Room id <-> name <-> alias resolution, and the rooms cache.

Names come from the robot (`getMapData.area_list`); aliases and flags come
from the config, keyed by robot name or id.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class RoomError(ValueError):
    pass


@dataclass
class Room:
    id: int
    name: str | None = None
    aliases: list[str] = field(default_factory=list)
    forbidden: bool = False
    #: the robot can't get in by itself: a human carries it in
    carry_in: bool = False
    #: the robot can't get out by itself: a human carries it out
    carry_out: bool = False
    #: lost here on the way home: these rooms in turn, then home; None: the config's `home_route`
    home_route: list[int | str] | None = None

    @property
    def label(self) -> str:
        """What to show a human: the robot's name, else the first alias, else the id."""
        return self.name or (self.aliases[0] if self.aliases else str(self.id))


class RoomTable:
    def __init__(self, rooms: list[tuple[int, str | None]], room_config: dict[int | str, dict[str, Any]]):
        self._rooms: list[Room] = []
        for rid, name in rooms:
            conf = room_config.get(rid) or room_config.get(str(rid)) or (room_config.get(name) if name else None) or {}
            flags = {k: bool(conf.get(k, False)) for k in ("forbidden", "carry_in", "carry_out")}
            route = conf.get("home_route")
            route = None if route is None else list(route)
            self._rooms.append(Room(rid, name, list(conf.get("aliases", [])), **flags, home_route=route))

    @classmethod
    def from_map(cls, map_data: dict[str, Any], room_config: dict[int | str, dict[str, Any]]) -> RoomTable:
        rooms = []
        for a in map_data.get("area_list", []):
            if a.get("type") == "room":
                n = a.get("name")
                rooms.append((a["id"], base64.b64decode(n).decode() if n else None))
        return cls(rooms, room_config)

    @classmethod
    def load(cls, path: Path, room_config: dict[int | str, dict[str, Any]]) -> RoomTable:
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            data = []
        return cls([(r["id"], r["name"]) for r in data], room_config)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([{"id": r.id, "name": r.name} for r in self._rooms], ensure_ascii=False))

    def __iter__(self) -> Iterator[Room]:
        return iter(self._rooms)

    def __len__(self) -> int:
        return len(self._rooms)

    def resolve(self, token: str | int) -> Room:
        """A room by id, robot name or alias; names are case-insensitive."""
        t = str(token).strip().casefold()
        for r in self._rooms:
            if t == str(r.id) or t == (r.name or "").casefold() or t in (a.casefold() for a in r.aliases):
                return r
        known = ", ".join(self.completions())
        raise RoomError(f"unknown room {token!r} (known: {known or 'none, refresh the rooms'})")

    def label_of(self, room_id: int) -> str:
        for r in self._rooms:
            if r.id == room_id:
                return r.label
        return str(room_id)

    def completions(self) -> list[str]:
        out = []
        for r in self._rooms:
            out.append(r.name or str(r.id))
            out += r.aliases
        return out

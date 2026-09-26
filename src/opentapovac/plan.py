"""A request + the home rules -> an ordered list of runs (docs/design.md §3).

* `order.first` / `order.last` from the config sort the rooms of a run;
  the rest keep the order they were asked for.
* A carry room (`carry_in`, `carry_out`) gets a run of its own, before the
  others: whoever pressed the button is most likely still around.  A
  carry-in room asked for later than first gets a warning.
* Forbidden rooms are refused without `force`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import Config
from .payloads import MODE_LABELS, Settings
from .rooms import Room, RoomError, RoomTable


class PlanError(ValueError):
    """The request can't be turned into runs (unknown or forbidden room, bad setting)."""


@dataclass
class JobRequest:
    rooms: list[str]
    mode: str | None = None
    suction: int | None = None
    water: int | None = None
    passes: int | None = None
    #: one run per room instead of one multi-room run
    sequential: bool = False
    #: allow forbidden rooms and an unlocked map
    force: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> JobRequest:
        known = cls.__dataclass_fields__
        unknown = set(d) - set(known)
        if unknown:
            raise PlanError(f"unknown field(s): {', '.join(sorted(unknown))}")
        if not isinstance(d.get("rooms"), list):
            raise PlanError("rooms must be a list")
        return cls(**d)


@dataclass
class Run:
    """One `runCleanTask`.  A carry run has exactly one room."""

    items: list[tuple[Room, Settings]]
    #: a human carries the robot into the room before the send, and whenever it comes back out
    carry_in: bool = False
    #: a human carries the robot out whenever it heads for the dock
    carry_out: bool = False

    @property
    def rooms(self) -> list[Room]:
        return [r for r, _ in self.items]

    def describe(self) -> str:
        return ", ".join(f"{r.label} ({MODE_LABELS[s.mode]})" for r, s in self.items)


def _resolve_all(tokens: list[Any], table: RoomTable) -> list[Room]:
    """Config references to rooms; ones the robot doesn't have (yet) are skipped."""
    out = []
    for t in tokens:
        try:
            out.append(table.resolve(t))
        except RoomError:
            pass
    return out


def order_rooms(rooms: list[Room], table: RoomTable, config: Config) -> list[Room]:
    first = [r.id for r in _resolve_all(config.order.get("first") or [], table)]
    last = [r.id for r in _resolve_all(config.order.get("last") or [], table)]

    def key(ir: tuple[int, Room]) -> tuple[int, int]:
        i, r = ir
        if r.id in first:
            return (0, first.index(r.id))
        if r.id in last:
            return (2, last.index(r.id))
        return (1, i)

    return [r for _, r in sorted(enumerate(rooms), key=key)]


def plan(req: JobRequest, table: RoomTable, config: Config, warnings: list[str] | None = None) -> list[Run]:
    """The runs for `req`; things the requester should know are appended to `warnings`."""
    if not req.rooms:
        raise PlanError("no rooms given")
    d = config.defaults
    try:
        settings = Settings(
            mode=req.mode or d.mode,
            suction=req.suction if req.suction is not None else d.suction,
            water=req.water if req.water is not None else d.water,
            passes=req.passes if req.passes is not None else d.passes,
        )
        rooms: list[Room] = []
        for token in req.rooms:
            r = table.resolve(token)
            if r not in rooms:
                rooms.append(r)
    except (RoomError, ValueError) as e:
        raise PlanError(str(e)) from e
    bad = [r.label for r in rooms if r.forbidden]
    if bad and not req.force:
        raise PlanError(f"refusing forbidden room(s) {', '.join(bad)} without force")
    if warnings is not None:
        for r in rooms[1:]:
            if r.carry_in:
                warnings.append(
                    f"{r.label} goes first, not where it was asked for: someone carries the robot in "
                    "when it heads there from the dock, after fitting the mops"
                )
    rooms = order_rooms(rooms, table, config)
    carried = [Run([(r, settings)], r.carry_in, r.carry_out) for r in rooms if r.carry_in or r.carry_out]
    items = [(r, settings) for r in rooms if not (r.carry_in or r.carry_out)]
    if not items:
        return carried
    return carried + ([Run([i]) for i in items] if req.sequential else [Run(items)])

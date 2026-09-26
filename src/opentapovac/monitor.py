"""Following a run: a state machine fed with observations, no I/O.

Rules from docs/tapo-clean-spec.md ("the monitor loop"), checked against the
recorded 2026-09-24 evening log (tests/fixtures).  Two deviations from the
spec, both from that log:

* Idle/done is 16, or 5/6 held for `settle` seconds.  "5 seen twice" is not
  enough: after a run the robot showed 5 for ~30 s before washing the mop.
* Standby with err 21 (dock not found) is not the end: the robot retried by
  itself ~90 s later, twice.  It counts as given up after `gave_up_after`.

Carry rooms (docs/design.md §3) are one room per run, so the monitor knows
where the robot is without a position: in a `carry_out` run, heading home
means leaving the room; in a `carry_in` run, leaving the base means heading
for the room.  Either way it raises a question (`ask`) for the engine to put
to a human.  The robot is not paused meanwhile: `setRobotPause` has not been
tried yet.  It stops at the doorstep by itself (err 21 on the way out).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .codes import error_text, status_text

LEFT_DOCK = {1, 4}  # cleaning, going home
STARTED = {1, 4, 15, 17}  # 15/17: mop washing/fitting at the start of a mop pass
CHARGING = {5, 6}
DRYING = 16
#: in or at the base: charging, emptying dust, mop washing/fitting/removing, cutting hair, drying
AT_BASE = {5, 6, 9, 15, 16, 17, 18, 19}
GOING_HOME = 4
CLEANING = 1


@dataclass(frozen=True)
class Observation:
    t: float
    status: int
    errors: tuple[int, ...] = ()
    relocating: bool | None = None
    mapping: bool | None = None
    #: `getCleanStatus.recharge_status` 1: it wants the dock (mop wash, battery, end of run)
    recharging: bool | None = None

    @classmethod
    def from_replies(cls, t: float, vac: dict[str, Any], clean: dict[str, Any] | None = None) -> Observation:
        err = vac.get("err_status") or []
        clean = clean or {}
        rs = clean.get("recharge_status")
        return cls(
            t=t,
            status=vac.get("status"),
            errors=tuple(err) if isinstance(err, list) else (err,),
            relocating=clean.get("is_relocating"),
            mapping=clean.get("is_mapping"),
            recharging=None if rs is None else rs == 1,
        )


@dataclass(frozen=True)
class Event:
    level: str  # info, warn, alert (needs a human), error
    code: str
    msg: str


@dataclass(frozen=True)
class Ask:
    """A question for a human; the engine waits for one of `choices`."""

    code: str
    text: str
    choices: list[str]


@dataclass(frozen=True)
class MonitorParams:
    start_timeout: float = 120
    settle: float = 60
    gave_up_after: float = 300


class IdleWatch:
    """True once the robot is idle: drying (16), or charging/charged held for `settle` s.

    Standby without an error (0, seen with the base unpowered) also counts once
    it has held for `settle` s; `standby` then tells the caller.
    """

    def __init__(self, settle: float):
        self.settle = settle
        self._since: float | None = None
        self.standby = False

    def feed(self, o: Observation) -> bool:
        if o.status == DRYING:
            return True
        self.standby = o.status == 0 and not o.errors
        if o.status in CHARGING or self.standby:
            if self._since is None:
                self._since = o.t
            return o.t - self._since >= self.settle
        self._since = None
        return False


ERROR_LEVELS = {
    3: ("alert", "stuck", "stuck — needs a human"),
    4: ("info", "lifted", "lifted (wheels off the floor)"),
    21: ("warn", "dock_not_found", "dock not found — waiting for it to retry"),
    26: ("warn", "water_empty", "clean water tank in the base is empty"),
}


class Monitor:
    """phase: starting -> running -> done | failed.

    After a `step`, `ask` may hold a question.  The caller clears it once
    answered and calls `resumed()`.
    """

    def __init__(
        self,
        sent_at: float,
        params: MonitorParams,
        room: str | None = None,
        carry_in: bool = False,
        carry_out: bool = False,
    ):
        self.sent_at = sent_at
        self.p = params
        self.room, self.carry_in, self.carry_out = room, carry_in, carry_out
        self.phase = "starting"
        self.message = ""
        self.ask: Ask | None = None
        # carry_out: ask on the next trip home; armed once it is out cleaning, since
        # recharge_status reads 1 while it washes the mop at the start of a run
        self._out_armed = False
        self._at_base = False  # carry_in: ask when it next leaves the base
        self._left_dock = False
        self._status: int | None = None
        self._errors: tuple[int, ...] = ()
        self._relocating = False
        self._mapping = False
        self._standby_since: float | None = None
        self._idle = IdleWatch(params.settle)

    def step(self, o: Observation) -> list[Event]:
        if self.phase in ("done", "failed"):
            return []
        ev: list[Event] = []
        if o.status != self._status:
            ev.append(Event("info", "status", status_text(o.status)))
        for e in o.errors:
            if e not in self._errors:
                level, code, msg = ERROR_LEVELS.get(e, ("warn", "error", error_text(e)))
                ev.append(Event(level, code, msg))
        if self._errors and not o.errors:
            ev.append(Event("info", "error_cleared", "error cleared"))
        if o.relocating and not self._relocating:
            ev.append(Event("warn", "relocating", "relocating — check that it finds the right place"))
        elif self._relocating and o.relocating is False:
            ev.append(Event("info", "relocated", "relocation finished"))
        if o.mapping and not self._mapping and self.phase != "starting":
            ev.append(Event("alert", "mapping", "robot is building a new map — is it lost?"))
        self._status, self._errors = o.status, o.errors
        if o.relocating is not None:
            self._relocating = o.relocating
        if o.mapping is not None:
            self._mapping = o.mapping

        if self.phase == "starting":
            if o.status in STARTED:
                self.phase = "running"
                ev.append(Event("info", "started", "run started"))
            elif o.t - self.sent_at > self.p.start_timeout:
                return ev + self._fail(
                    f"run did not start within {self.p.start_timeout:.0f} s ({status_text(o.status)})"
                )
        if self.phase != "running":
            return ev

        self._left_dock |= o.status in LEFT_DOCK
        if o.status == 0:
            if self._standby_since is None:
                self._standby_since = o.t
            elif o.t - self._standby_since > self.p.gave_up_after:
                why = ", ".join(error_text(e) for e in o.errors) or "no error given"
                return ev + self._fail(f"robot gave up ({why})")
        else:
            self._standby_since = None
        ev += self._carry(o)
        if self._idle.feed(o) and self._left_dock:
            self.phase = "done"
            ev.append(Event("info", "done", "run finished, robot is back on the dock"))
        return ev

    def _carry(self, o: Observation) -> list[Event]:
        if self.carry_out:
            if o.status == GOING_HOME or (o.recharging and o.status not in AT_BASE):
                if self._out_armed:
                    self._out_armed = False
                    return self._ask("carry_out", f"the robot wants to go home: carry it out of {self.room}", ["done"])
            elif o.status == CLEANING:
                self._out_armed = True
        if self.carry_in:
            if o.status in AT_BASE:
                self._at_base = True
            elif o.status == CLEANING and self._at_base:
                self._at_base = False
                return self._ask("carry_in", f"the robot is on its way back: carry it into {self.room}", ["done"])
        return []

    def _ask(self, code: str, text: str, choices: list[str]) -> list[Event]:
        self.ask = Ask(code, text, choices)
        return [Event("alert", code, text + ", then press " + " or ".join(choices))]

    def resumed(self) -> None:
        """A human answered: time spent waiting doesn't count as standby or settling."""
        self._standby_since = None
        self._idle = IdleWatch(self.p.settle)

    def _fail(self, msg: str) -> list[Event]:
        self.phase, self.message = "failed", msg
        return [Event("error", "failed", msg)]

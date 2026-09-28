"""Following a run: a state machine fed with observations, no I/O.

Rules from docs/tapo-clean-spec.md ("the monitor loop"), checked against the
recorded 2026-09-24 evening log (tests/fixtures).  Two deviations from the
spec, both from that log:

* Idle/done is 16, or 5/6 held for `settle` seconds.  "5 seen twice" is not
  enough: after a run the robot showed 5 for ~30 s before washing the mop.
* Standby with err 21 (dock not found) is not the end: the robot retried by
  itself ~90 s later once (2026-09-25).  It counts as given up after
  `gave_up_after`.  On 2026-09-26 it was carried to the dock after ~40 s
  and then ended the run there, mid-way: a run that ends after err 21
  without going out again is reported as maybe unfinished, unless it had
  reached 100 % before losing the dock.

What the robot left undone is in `missed` once the run is done:
"unfinished" (above), or "mop" when a run that should mop was seen
cleaning, but never with the mop on, although it reached 100 % and every
mop read while cleaning was answered (2026-09-28: it vacuumed to 100 %,
washed the mop at the base and ended there).  Below 100 % it was more
likely stopped by a human (the app, its button), so that is only warned
about.  The engine may send what was missed again.

Carry rooms (docs/design.md §3) are one room per run, so the monitor knows
where the robot is without a position: in a `carry_out` run, heading home
means leaving the room; in a `carry_in` run, leaving the base means heading
for the room (the run is sent from the dock, so the mops are on before
anyone carries it).  Either way it raises a question (`ask`) for a human.
The engine pauses the robot meanwhile (`pause_for_carry`): unpaused it
gives up at the doorstep fast and forgets where it has been.  The question
is answered by `answer()`, or by the robot being seen lifted (err 4) and
put down again; the engine then resumes it.  While a question is
open, standby doesn't count as giving up.  After a carry-in, `verify_due`
says when the position is worth checking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .codes import error_text, status_text

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
    # probed once a minute, None otherwise: `getMopState`, `getCleanInfo`, battery
    mop: bool | None = None
    percent: int | None = None
    clean_time: int | None = None
    clean_area: int | None = None
    battery: int | None = None

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
    #: after a carry-in: cleaning, not relocating, this long before the position is checked
    verify_after: float = 60
    #: cleaning with `clean_percent` standing still this long: warn (stuck at a doorstep?)
    stall_after: float = 300


class IdleWatch:
    """True once the robot is idle: drying (16), or charging/charged held for `settle` s.

    Standby without an error (0, seen with the base unpowered) also counts once
    it has held for `settle` s, unless `standby_ok` is off; `standby` then tells
    the caller.  Before a send that is right; during a run it is not, since the
    robot also stands in standby off the dock (at a doorstep, say).
    """

    def __init__(self, settle: float, standby_ok: bool = True):
        self.settle, self.standby_ok = settle, standby_ok
        self._since: float | None = None
        self.standby = False

    def feed(self, o: Observation) -> bool:
        if o.status == DRYING:
            return True
        self.standby = self.standby_ok and o.status == 0 and not o.errors
        if o.status in CHARGING or self.standby:
            if self._since is None:
                self._since = o.t
            return o.t - self._since >= self.settle
        self._since = None
        return False


ERROR_LEVELS = {
    3: ("alert", "stuck", "stuck — needs a human"),
    4: ("info", "lifted", "lifted (wheels off the floor)"),
    21: (
        "alert",
        "dock_not_found",
        "dock not found — carry it to the dock, unless it finds its way (by itself or guided)",
    ),
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
        vacuum_first: bool = False,
        mop_expected: bool = False,
    ):
        self.sent_at = sent_at
        self.p = params
        self.room, self.carry_in, self.carry_out = room, carry_in, carry_out
        #: vacuum then mop: the first cleaning should be with the mop off
        self.vacuum_first = vacuum_first
        #: some room of the run is to be mopped
        self.mop_expected = mop_expected
        #: once done: what the robot left undone, "unfinished" or "mop"
        self.missed: list[str] = []
        self._pass: str | None = None  # vacuum, mop
        self._mopped = False
        self._mop_unread = 0  # polls while cleaning without a mop read
        self._percent: int | None = None
        self._percent_since: float | None = None
        self._stall_told = False
        self.phase = "starting"
        self.message = ""
        self.ask: Ask | None = None
        #: set once after a carry-in; the caller checks the position and clears it
        self.verify_due = False
        self._lifted = False  # err 4 seen while a question is open
        self._carried = False  # carried in, position not checked yet
        self._steady_since: float | None = None  # cleaning, not relocating, since
        # carry_out: ask on the next trip home; armed once it is out cleaning, since
        # recharge_status reads 1 while it washes the mop at the start of a run
        self._out_armed = False
        self._lost_dock = False  # err 21 since it last went out cleaning
        self._lost_at: int | None = None  # clean_percent when it lost the dock
        self._seen_errors: set[int] = set()
        self._at_base = True  # carry_in: ask when it next leaves the base; sent from the dock
        self._left_dock = False
        self._status: int | None = None
        self._errors: tuple[int, ...] = ()
        self._relocating = False
        self._mapping = False
        self._standby_since: float | None = None
        self._idle = IdleWatch(params.settle, standby_ok=False)
        self._base_since: float | None = None  # back at the base without having cleaned

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

        # not "going home": from standby off the dock a run starts by going to the base for the mop
        self._left_dock |= o.status == CLEANING
        self._seen_errors.update(o.errors)
        if 21 in o.errors:
            if not self._lost_dock:
                self._lost_at = self._percent
            self._lost_dock = True
        elif o.status == CLEANING:
            self._lost_dock = False
        if o.status == 0 and self.ask is None:
            if self._standby_since is None:
                self._standby_since = o.t
            elif o.t - self._standby_since > self.p.gave_up_after:
                why = ", ".join(error_text(e) for e in o.errors) or "no error given: is the base powered?"
                return ev + self._fail(f"robot gave up ({why})")
        else:
            self._standby_since = None
        ev += self._carry(o)
        ev += self._progress(o)
        self._check_due(o)
        if not self._left_dock and o.status in CHARGING | {DRYING}:
            if self._base_since is None:
                self._base_since = o.t
            elif o.t - self._base_since >= self.p.start_timeout:
                why = ", ".join(error_text(e) for e in self._seen_errors) or "no error given"
                return ev + self._fail(f"robot went back to the base without cleaning ({why})")
        else:
            self._base_since = None
        if self._idle.feed(o) and self._left_dock:
            self.phase = "done"
            at = f" (the robot says {self._percent} % done)" if self._percent is not None else ""
            ev.append(Event("info", "done", f"run finished, robot is back on the dock{at}"))
            if self._lost_dock and self._lost_at == 100:
                pass  # lost on the way home from a finished run: nothing left undone
            elif self._lost_dock:
                self.missed = ["unfinished"]
                self.message = "the run ended after the robot lost the dock; it may be unfinished"
                ev.append(Event("alert", "maybe_unfinished", self.message))
            elif self.mop_expected and self._pass is not None and not self._mopped:
                if self._percent == 100 and not self._mop_unread:
                    self.missed = ["mop"]
                self.message = "the robot ended the run without a mop pass"
                ev.append(Event("warn", "no_mop_pass", self.message))
        return ev

    def _carry(self, o: Observation) -> list[Event]:
        if self.ask is not None:
            if 4 in o.errors:
                self._lifted = True
            elif self._lifted:
                code = self.ask.code
                self.answer("done")
                return [Event("info", "carried", f"{code.replace('_', ' ')}: seen lifted and put down")]
            return []
        if self.carry_out:
            if o.status == GOING_HOME or (o.recharging and o.status not in AT_BASE):
                if self._out_armed:
                    self._out_armed = False
                    return self._ask(
                        "carry_out",
                        f"the robot wants to go home: carry it out of {self.room} (press its button if it doesn't go on)",
                        ["done"],
                    )
            elif o.status == CLEANING:
                self._out_armed = True
        if self.carry_in:
            if o.status in AT_BASE:
                self._at_base = True
            elif o.status == CLEANING and self._at_base:
                self._at_base = False
                return self._ask(
                    "carry_in",
                    f"the robot is heading for {self.room}: carry it in (press its button if it doesn't go on)",
                    ["done", "skip"],
                )
        return []

    def _progress(self, o: Observation) -> list[Event]:
        """Vacuum and mop passes (mop state while cleaning), and a stall in `clean_percent`."""
        ev: list[Event] = []
        if o.status == CLEANING and o.mop is None and self._pass is not None:
            self._mop_unread += 1
        if o.status == CLEANING and o.mop is not None:
            p = "mop" if o.mop else "vacuum"
            self._mopped |= o.mop
            if p != self._pass:
                if self._pass is None and self.vacuum_first and p == "mop":
                    msg = "vacuum then mop, but it started cleaning with the mop on: the vacuum pass was skipped"
                    ev.append(Event("warn", "skipped_vacuum", msg))
                self._pass = p
                ev.append(Event("info", f"{p}_pass", f"{p} pass"))
        if o.percent is not None:
            if o.status != CLEANING or o.percent != self._percent:
                self._percent_since, self._stall_told = o.t, False
            elif self._percent_since is not None and not self._stall_told:
                if o.t - self._percent_since >= self.p.stall_after:
                    self._stall_told = True
                    mins = (o.t - self._percent_since) / 60
                    msg = f"no progress for {mins:.0f} min ({o.percent} %): stuck at a doorstep?"
                    ev.append(Event("warn", "no_progress", msg))
            self._percent = o.percent
        return ev

    def _ask(self, code: str, text: str, choices: list[str]) -> list[Event]:
        self.ask = Ask(code, text, choices)
        return [Event("alert", code, text + ", then press " + " or ".join(choices))]

    def answer(self, choice: str) -> None:
        """The open question is answered (or seen done); waiting time doesn't count as standby or settling."""
        if self.ask is not None and self.ask.code == "carry_in" and choice == "done":
            self._carried, self._steady_since = True, None
        self.ask, self._lifted = None, False
        self._standby_since = None
        self._idle = IdleWatch(self.p.settle, standby_ok=False)

    def _check_due(self, o: Observation) -> None:
        if not self._carried:
            return
        if o.status != CLEANING or o.relocating:
            self._steady_since = None
        elif self._steady_since is None:
            self._steady_since = o.t
        elif o.t - self._steady_since >= self.p.verify_after:
            self._carried, self.verify_due = False, True

    def _fail(self, msg: str) -> list[Event]:
        self.phase, self.message = "failed", msg
        return [Event("error", "failed", msg)]


#: `gotoPoint` in progress (app `RobotStatus`; seen while going to a point from the app)
GOING_TO_POINT = 11
#: robot position within this of a waypoint (mm): reached
REACH = 400


class HomeGuide:
    """Lost on the way home: waypoint after waypoint (`gotoPoint`), then home.

    Actions, as (what, label or message, point): ("goto", room, (x, y)),
    ("home", "", None) once the last waypoint is reached, ("gave_up", msg,
    None) when one isn't reached in time, ("stopped", why, None) when the
    guide leaves the robot alone.  A waypoint is reached with the robot
    within `REACH` of it, or back in standby (0) after going (11).  The
    time limit counts from the send or the last poll that saw it going.
    Anything else ends the guide: heading home or cleaning by itself (it
    found its way once, ~90 s after err 21, 2026-09-25), remote control or
    a pause (a human), lifted (err 4, a human carrying it), any other
    status.
    """

    def __init__(self, stops: list[tuple[str, tuple[int, int]]], timeout: float):
        self.stops, self.timeout = stops, timeout
        self.done = False
        self._i = -1
        self._since = 0.0  # the send, or the last poll that saw it going
        self._going = False  # status 11 seen since the last goto

    def start(self, t: float) -> list[tuple[str, str, tuple[int, int] | None]]:
        return self._next(t)

    def step(self, o: Observation, pos: tuple[float, float] | None) -> list[tuple[str, str, tuple[int, int] | None]]:
        if self.done:
            return []
        if 4 in o.errors or o.status not in (0, GOING_TO_POINT):
            self.done = True
            why = "lifted" if 4 in o.errors else status_text(o.status)
            return [("stopped", f"the robot is {why}", None)]
        label, point = self.stops[self._i]
        near = pos is not None and (pos[0] - point[0]) ** 2 + (pos[1] - point[1]) ** 2 <= REACH**2
        if near or (self._going and o.status == 0):
            return self._next(o.t)
        if o.status == GOING_TO_POINT:
            self._going, self._since = True, o.t
        elif o.t - self._since >= self.timeout:
            self.done = True
            return [("gave_up", f"did not reach {label} within {self.timeout / 60:.0f} min", None)]
        return []

    def _next(self, t: float) -> list[tuple[str, str, tuple[int, int] | None]]:
        self._i += 1
        self._since, self._going = t, False
        if self._i < len(self.stops):
            label, point = self.stops[self._i]
            return [("goto", label, point)]
        self.done = True
        return [("home", "", None)]

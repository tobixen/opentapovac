"""Runs jobs: the only thing that talks to the robot.

The daemon is "engine + web + HTTP API"; the standalone CLI runs an engine
in-process.  Nothing here imports the web or CLI code or assumes it owns the
event loop, so a Home Assistant integration could wrap it (docs/design.md §10).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import mapimg
from .codes import error_text, status_text
from .config import Config
from .events import EventLog
from .monitor import CLEANING, Ask, IdleWatch, Monitor, MonitorParams, Observation
from .payloads import HOME, STOP, goto_payload, run_payload
from .plan import JobRequest, PlanError, Run, plan
from .robot import Robot, RobotError
from .rooms import Room, RoomError, RoomTable
from .tracks import KINDS, CleanRecords, Track, describe_record

__all__ = ["AnswerError", "Busy", "Engine", "Job", "JobRequest", "PlanError"]

_LOGGER = logging.getLogger(__name__)

#: how far back the map shows tracks, seconds
MAP_MAX_AGE = 12 * 3600.0


class Busy(RuntimeError):
    pass


class JobFailed(Exception):
    pass


class AnswerError(ValueError):
    """An answer to a question that isn't asked, or a choice that isn't offered."""


@dataclass
class Job:
    request: JobRequest
    runs: list[Run]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: str = "queued"  # queued, running, waiting (for a human), done, failed, stopped
    message: str = ""
    step: int = 0
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    finished: str | None = None
    question: Ask | None = None
    warnings: list[str] = field(default_factory=list)
    _answer: asyncio.Future | None = field(default=None, repr=False)

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running", "waiting")

    def describe(self) -> str:
        return " | ".join(run.describe() for run in self.runs)

    def to_dict(self) -> dict[str, Any]:
        q = self.question
        return {
            "id": self.id,
            "state": self.state,
            "message": self.message,
            "step": self.step,
            "steps": len(self.runs),
            "description": self.describe(),
            "rooms": [[r.id for r in run.rooms] for run in self.runs],
            "question": {"code": q.code, "text": q.text, "choices": q.choices} if q else None,
            "warnings": self.warnings,
            "created": self.created,
            "finished": self.finished,
        }


class Engine:
    def __init__(
        self,
        robot: Robot,
        config: Config,
        events: EventLog,
        *,
        rooms: RoomTable | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.robot, self.config, self.events = robot, config, events
        self.rooms = rooms if rooms is not None else RoomTable.load(config.rooms_cache, config.rooms)
        self.clock, self.sleep = clock, sleep
        self.job: Job | None = None
        self.jobs: dict[str, Job] = {}
        self._task: asyncio.Task | None = None
        #: the robot's map, fetched once and on refresh; the tracks are drawn on it per request
        self._map_data: dict[str, Any] | None = None
        self.records = CleanRecords(config.clean_records_file)
        #: the track of the last job or app run, being recorded
        self.track: Track | None = self._last_track()
        self._track_failed = False
        #: the watcher follows the robot outside jobs
        self._watching = False
        self._submit_lock = asyncio.Lock()
        self.params = MonitorParams(
            config.start_timeout, config.settle, config.gave_up_after, config.verify_after, config.stall_after
        )
        #: the map, for naming the room the robot is in; loaded once per job or app run
        self._map: dict[str, Any] | None = None

    # --- planning ---

    def plan(self, req: JobRequest, warnings: list[str] | None = None) -> list[Run]:
        return plan(req, self.rooms, self.config, warnings)

    def _last_track(self) -> Track | None:
        files = sorted(self.config.tracks_dir.glob("*.json"), key=lambda f: f.stat().st_mtime)
        for f in reversed(files):
            try:
                return Track.load(f)
            except (OSError, ValueError, KeyError):
                continue
        return None

    # --- jobs ---

    @property
    def busy(self) -> bool:
        return self.job is not None and self.job.active

    async def submit(self, req: JobRequest) -> Job:
        """Plan and start a job in the background.  Raises PlanError, Busy."""
        async with self._submit_lock:  # the room refresh awaits; two submits must not both pass
            if self.busy:
                raise Busy(f"job {self.job.id} is still {self.job.state}")
            if not len(self.rooms):
                await self.refresh_rooms()
            warnings: list[str] = []
            job = Job(req, self.plan(req, warnings), warnings=warnings)
            self.job = self.jobs[job.id] = job
            self._task = asyncio.create_task(self._run(job))
            return job

    async def wait(self) -> Job | None:
        if self._task is not None:
            await asyncio.shield(self._task)
        return self.job

    async def run(self, req: JobRequest) -> Job:
        """Standalone: submit and block until the job ends."""
        job = await self.submit(req)
        await self.wait()
        return job

    def _emit(self, level: str, msg: str, code: str | None = None, job: Job | None = None) -> None:
        self.events.emit(level, msg, job=job.id if job else None, code=code)

    async def _run(self, job: Job) -> None:
        job.state = "running"
        self._emit("info", f"job {job.id}: {job.describe()}", "job", job)
        for w in job.warnings:
            self._emit("warn", w, "plan_warning", job)
        try:
            await self._preflight(job)
            self.track, self._track_failed = Track(self.config.tracks_dir / f"{job.id}.json"), False
            await self._load_map()
            for i, run in enumerate(job.runs):
                job.step = i + 1
                missed, ended = await self._run_once(job, run)
                redo = self._redo_run(run, missed)
                if redo is not None and ended and await self._may_redo(job, redo, missed):
                    job.message = ""
                    await self._run_once(job, redo, again=True)  # once: what it misses now is only reported
            job.state = "done"
            self._emit("info", f"job {job.id} done", "job_done", job)
        except asyncio.CancelledError:
            job.state = "stopped"
            self._emit("warn", f"job {job.id} stopped", "job_stopped", job)
        except (RobotError, JobFailed) as e:
            job.state, job.message = "failed", str(e)
            self._emit("error", f"job {job.id} failed: {e}", "job_failed", job)
        except Exception as e:  # a bug; the job must still end, or it stays "running" forever
            _LOGGER.debug("job %s", job.id, exc_info=True)
            job.state, job.message = "failed", f"{type(e).__name__}: {e}"
            self._emit("error", f"job {job.id} failed: {job.message}", "job_failed", job)
        finally:
            job.question = job._answer = None
            job.finished = datetime.now(UTC).isoformat(timespec="seconds")
            # the robot may still be busy (mop wash, a stuck run): the watcher follows it from here
            self._watching = True

    async def _run_once(self, job: Job, run: Run, again: bool = False) -> tuple[list[str], bool]:
        """Send a run and follow it to the end.

        What the robot left undone (`Monitor.missed`), and whether its task
        really ended: a new clean record.  A charge mid-run held longer than
        `settle` looks like the end to the monitor, but leaves no record.
        """
        await self._wait_idle(job)
        await self._send(job, run, again)
        missed = await self._follow(job, run)
        await self._poll_track(job)
        return missed, bool(await self._fetch_records(job))

    def _redo_run(self, run: Run, missed: list[str]) -> Run | None:
        """The run again: all of it if unfinished, the rooms to be mopped as mop only if the mop pass was missed."""
        if not self.config.redo_missed or not missed:
            return None
        if "unfinished" in missed:
            return run
        items = [(r, dataclasses.replace(s, mode="mop")) for r, s in run.items if s.mode != "vac"]
        return dataclasses.replace(run, items=items) if items else None

    async def _may_redo(self, job: Job, redo: Run, missed: list[str]) -> bool:
        """Battery and water allowing; an unfinished run only if a human says so.

        The robot ends a run unfinished when a human carried it to the dock,
        too (2026-09-26), so that is asked, not assumed.
        """
        why = None
        try:
            pct = (await self.robot.battery()).get("battery_percentage")
            if pct is not None and pct < 30:
                why = f"battery at {pct} %"
            elif (
                any(s.mode != "vac" for _, s in redo.items) and (await self.robot.base_status()).get("clean_water") == 1
            ):
                why = "the clean water tank in the base is empty"
        except RobotError as e:
            why = f"could not read battery/base: {e}"
        if why:
            self._emit("alert", f"not sending {redo.describe()} again: {why}", "redo_skipped", job)
            return False
        if "unfinished" in missed:
            q = Ask("redo", f"the run may be unfinished: send {redo.describe()} again?", ["again", "no"])
            if await self._ask_and_wait(job, q) != "again":
                return False
        self._emit("warn", f"the robot left something undone: sending {redo.describe()} again", "redo", job)
        return True

    async def _ask_and_wait(self, job: Job, q: Ask) -> str | None:
        """Pose `q` and wait for the answer, at most `human_wait_timeout`; None without one."""
        self._pose(job, q)
        deadline = self.clock() + self.config.human_wait_timeout
        fut = job._answer
        try:
            while fut is not None and not fut.done():
                if self.clock() > deadline:
                    self._emit("warn", f"nobody answered: {q.text}", "ask_timeout", job)
                    return None
                await self.sleep(self.config.poll_interval)
            return fut.result() if fut is not None else None
        finally:
            self._unpose(job)

    # --- what the robot forgets ---

    async def _cleaning_with(self, job: Job | None) -> str | None:
        """What the robot cleans with, "vac" or "mop": the run's modes if they agree, else the robot's mop state.

        Unverified: that `mop_state` is false during the vacuum pass of a
        vac_then_mop run with the mop fitted (TODO.md).
        """
        if job and job.step:
            modes = {s.mode for _, s in job.runs[job.step - 1].items}
            if modes == {"vac"}:
                return "vac"
            if modes <= {"mop", "vac_and_mop"}:
                return "mop"
        try:
            return "mop" if (await self.robot.mop_state()).get("mop_state") else "vac"
        except Exception:  # noqa: BLE001 — as in _poll_track
            return None

    async def _poll_track(self, job: Job | None) -> None:
        if self.track is None:
            return
        try:
            await self.track.poll(self.robot, lambda: self._cleaning_with(job))
        except Exception as e:  # noqa: BLE001 — bookkeeping on a reverse-engineered reply must not end a job
            if not self._track_failed:  # once per track
                self._track_failed = True
                self._emit("warn", f"could not fetch the track: {e}", "track_failed", job)

    async def _fetch_records(self, job: Job | None, report: bool = True) -> list[dict[str, Any]]:
        """The robot's new clean records; it writes one when a task ends."""
        try:
            new = await self.records.fetch(self.robot)
        except Exception as e:  # noqa: BLE001 — as in _poll_track
            _LOGGER.debug("getCleanRecords: %s", e)
            return []
        for r in new if report else []:
            self._emit("info", f"robot's record: {describe_record(r)}", "clean_record", job)
        return new

    async def watch_step(self) -> None:
        """One look at the robot outside jobs: record the track of a run from the app, or of a job's tail."""
        if self.busy:
            return
        vac = await self.robot.vac_status()
        if self.busy:  # a job started while we asked
            return
        status, errors = vac.get("status"), vac.get("err_status") or []
        active = status not in (5, 6, 8, 16) and not (status == 0 and not errors)
        if active and not self._watching:
            self._watching = True
            await self._fetch_records(None, report=False)
            name = datetime.now(UTC).strftime("app-%Y%m%d-%H%M%S.json")
            self.track, self._track_failed = Track(self.config.tracks_dir / name), False
            self._emit("info", f"robot is busy without a job ({status_text(status)}); recording its track", "app_run")
            await self._load_map()
        if active:
            await self._poll_track(None)
            with contextlib.suppress(RobotError):
                self._emit_progress(await self._observe(probe=True), None)
        elif self._watching:
            self._watching = False
            await self._poll_track(None)
            await self._fetch_records(None)

    async def watch(self) -> None:
        """The daemon's watcher; runs until cancelled."""
        while True:
            try:
                await self.watch_step()
            except RobotError as e:
                _LOGGER.debug("watch: %s", e)
            except Exception:  # a bug must not end the watcher
                _LOGGER.exception("watch")
            await self.sleep(self.config.watch_interval)

    # --- humans ---

    def _pose(self, job: Job, q: Ask) -> None:
        """Put `q` to a human.  The job goes on watching the robot; it never goes on without the answer."""
        job.question, job._answer = q, asyncio.get_running_loop().create_future()
        job.state = "waiting"
        self._emit("alert", f"{q.text} — waiting for: {' / '.join(q.choices)}", "ask", job)

    def _unpose(self, job: Job) -> None:
        job.question = job._answer = None
        job.state = "running"

    def answer(self, job_id: str, choice: str) -> None:
        job = self.jobs.get(job_id)
        if job is None:
            raise AnswerError("no such job")
        if job.question is None or job._answer is None or job._answer.done():
            raise AnswerError(f"job {job_id} is not waiting for an answer")
        if choice not in job.question.choices:
            raise AnswerError(f"choose one of: {', '.join(job.question.choices)}")
        fut, job.question, job.state = job._answer, None, "running"
        self._emit("info", f"answered: {choice}", "answered", job)
        fut.set_result(choice)

    async def _verify_position(self, job: Job, room: Room) -> None:
        """After a carry-in: is the robot where it was put?  Wrong = stop, before it cleans by a wrong map."""
        try:
            md = await self.robot.map_data()
            xy = (md.get("real_vac_coor") or [0, 0])[:2]
            near = mapimg.rooms_near(md, xy) if any(xy) else set()
        except (RobotError, KeyError, TypeError, ValueError) as e:
            self._emit("warn", f"could not check the position ({e}); is it in {room.label}?", "position_unknown", job)
            return
        if room.id in near:
            self._emit("info", f"position checked: in {room.label}", "position_ok", job)
            return
        if not near:
            self._emit("warn", f"could not check the position ({xy}); is it in {room.label}?", "position_unknown", job)
            return
        where = ", ".join(sorted(self.rooms.label_of(r) for r in near))
        await self.robot.send("runCleanTask", STOP)
        raise JobFailed(f"the robot thinks it is in {where}, not {room.label} — stopped; check it in the app")

    async def _preflight(self, job: Job) -> None:
        info = await self.robot.map_info()
        current = info.get("current_map_id")
        maps = [m for m in info.get("map_list", []) if m.get("map_id") == current]
        problems = []
        if not (maps and maps[0].get("map_locked")):
            problems.append("the map is not locked")
        if info.get("auto_change_map"):
            problems.append("auto_change_map is on")
        if problems:
            msg = " and ".join(problems) + " — a relocation could overwrite the map"
            if not job.request.force:
                raise JobFailed(msg + " (use force to run anyway)")
            self._emit("warn", msg, "map_unlocked", job)
        try:
            pct = (await self.robot.battery()).get("battery_percentage")
            if pct is not None and pct < 30:
                self._emit("warn", f"battery at {pct} %", "battery_low", job)
            if (await self.robot.base_status()).get("clean_water") == 1:
                self._emit("warn", "clean water tank in the base is empty", "water_empty", job)
        except RobotError as e:
            self._emit("warn", f"could not read battery/base: {e}", "preflight", job)
        await self._fetch_records(job, report=False)  # what came before this job

    async def _observe(self, probe: bool = False, mop_when_cleaning: bool = False) -> Observation:
        """The status; with `probe`, also the mop, the progress and the battery (once a minute).

        `mop_when_cleaning`: the mop on every poll while cleaning, so a short mop pass isn't missed.
        """
        vac = await self.robot.vac_status()
        try:
            clean = await self.robot.clean_status()
        except RobotError:
            clean = None
        o = Observation.from_replies(self.clock(), vac, clean)
        if not probe:
            if mop_when_cleaning and o.status == CLEANING:
                with contextlib.suppress(Exception):
                    o = dataclasses.replace(o, mop=(await self.robot.mop_state()).get("mop_state"))
            return o
        extra: dict[str, Any] = {}
        # each on its own: a failing one must not cost the others, or the poll
        with contextlib.suppress(Exception):
            info = await self.robot.clean_info()
            extra.update(percent=info.get("clean_percent"), clean_time=info.get("clean_time"))
            extra["clean_area"] = info.get("clean_area")
        with contextlib.suppress(Exception):
            extra["mop"] = (await self.robot.mop_state()).get("mop_state")
        with contextlib.suppress(Exception):
            extra["battery"] = (await self.robot.battery()).get("battery_percentage")
        return dataclasses.replace(o, **extra)

    async def _load_map(self) -> None:
        try:
            self._map = await self.robot.map_data()
        except Exception:  # noqa: BLE001 — only used to name rooms in the progress lines
            self._map = None

    def _room_now(self) -> str | None:
        """The room of the last track point, if there is a track and a map."""
        if not (self._map and self.track and self.track.segments and self.track.segments[-1]["points"]):
            return None
        try:
            near = mapimg.rooms_near(self._map, self.track.segments[-1]["points"][-1], 100)
        except Exception:  # noqa: BLE001 — a bad map costs the room name, not the run
            return None
        return " / ".join(sorted(self.rooms.label_of(r) for r in near)) or None

    def _emit_progress(self, o: Observation, job: Job | None) -> None:
        parts = []
        if o.percent is not None:
            parts.append(f"{o.percent} %")
        if o.clean_time is not None:
            parts.append(f"{o.clean_time} min")
        if o.clean_area is not None:
            parts.append(f"{o.clean_area} m²")
        if o.status == CLEANING and o.mop is not None:
            parts.append("mopping" if o.mop else "vacuuming")
        else:
            parts.append(status_text(o.status))
        room = self._room_now()
        if room:
            parts.append(f"in {room}")
        if o.battery is not None:
            parts.append(f"battery {o.battery} %")
        self._emit("info", ", ".join(parts), "progress", job)

    async def _wait_idle(self, job: Job) -> None:
        watch = IdleWatch(self.config.settle)
        deadline = self.clock() + self.config.idle_timeout
        told = False
        while True:
            try:
                o = await self._observe()
            except RobotError as e:
                # a lost poll is not a lost job; the deadline still holds
                if self.clock() > deadline:
                    raise
                self._emit("warn", f"poll failed: {e}", "poll_failed", job)
                await self.sleep(self.config.poll_interval)
                continue
            if watch.feed(o):
                if watch.standby:
                    self._emit("warn", "robot is in standby, not charging — is the base powered?", "standby", job)
                return
            if self.clock() > deadline:
                raise JobFailed(f"robot did not become idle ({status_text(o.status)})")
            if not told:
                self._emit("info", f"waiting for the robot to be idle ({status_text(o.status)})", "waiting", job)
                told = True
            await self.sleep(self.config.poll_interval)

    async def _send(self, job: Job, run: Run, again: bool = False) -> None:
        payload = run_payload([(r.id, s) for r, s in run.items])
        desc = run.describe() if again else ", ".join(r.label for r in run.rooms)
        what = "sending again" if again else "sending"
        self._emit("info", f"step {job.step}/{len(job.runs)}: {what} {desc}", "send", job)
        await self.robot.send("runCleanTask", payload)

    async def _follow(self, job: Job, run: Run) -> list[str]:
        """Follow a run to its end; what the robot left undone (`Monitor.missed`)."""
        room = run.rooms[0]
        vacuum_first = run.items[0][1].mode == "vac_then_mop"
        mop_expected = any(s.mode != "vac" for _, s in run.items)
        m = Monitor(self.clock(), self.params, room.label, run.carry_in, run.carry_out, vacuum_first, mop_expected)
        probed_at = float("-inf")
        posed: Ask | None = None
        posed_at, told = 0.0, False
        lost_since: float | None = None
        lost_told = False
        try:
            while True:
                await self.sleep(self.config.poll_interval)
                if posed is not None and job._answer is not None and job._answer.done():
                    choice = job._answer.result()
                    self._unpose(job)
                    posed = None
                    if choice == "skip":
                        await self.robot.send("runCleanTask", STOP)
                        self._emit("warn", f"skipped {room.label}: stop sent", "skipped", job)
                        return []
                    m.answer(choice)
                probe = self.clock() - probed_at >= self.config.progress_interval
                try:
                    o = await self._observe(probe, mop_when_cleaning=True)
                except RobotError as e:
                    # a lost poll is not a lost run; keep watching, but say so when it lasts
                    self._emit("warn", f"poll failed: {e}", "poll_failed", job)
                    lost_since = self.clock() if lost_since is None else lost_since
                    if not lost_told and self.clock() - lost_since > self.config.idle_timeout:
                        lost_told = True
                        mins = (self.clock() - lost_since) / 60
                        self._emit("alert", f"no contact with the robot for {mins:.0f} min", "no_contact", job)
                    continue
                if lost_told:
                    self._emit("info", "contact with the robot is back", "contact", job)
                lost_since, lost_told = None, False
                await self._poll_track(job)  # first: the progress line names the room from it
                if probe:
                    probed_at = o.t
                    self._emit_progress(o, job)
                for ev in m.step(o):
                    self._emit(ev.level, ev.msg, ev.code, job)
                if m.phase == "done":
                    if m.message:
                        job.message = m.message
                    return m.missed
                if m.phase == "failed":
                    raise JobFailed(m.message)
                if m.ask is not posed:  # a new question, or the monitor saw the old one done
                    if posed is not None:
                        self._unpose(job)
                    if m.ask is not None:
                        self._pose(job, m.ask)
                        posed_at, told = self.clock(), False
                    posed = m.ask
                elif posed is not None and not told and self.clock() - posed_at > self.config.human_wait_timeout:
                    told = True
                    self._emit("alert", f"nobody has answered: {posed.text}", "ask_timeout", job)
                if m.verify_due:
                    m.verify_due = False
                    await self._verify_position(job, room)
        finally:
            if posed is not None:
                self._unpose(job)

    # --- direct commands ---

    async def stop(self) -> None:
        """Stop the current job (if any) and the robot's run."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.job is not None and self.job.active:  # cancelled before it got going
            self.job.state = "stopped"
            self.job.question = self.job._answer = None
            self.job.finished = datetime.now(UTC).isoformat(timespec="seconds")
        await self.robot.send("runCleanTask", STOP)
        self._emit("warn", "stop sent", "stop")

    async def home(self) -> None:
        await self.robot.send("setSwitchCharge", HOME)
        self._emit("info", "sent home (setSwitchCharge, not yet verified on the robot)", "home")

    async def goto(self, point: tuple[int, int] | None = None, room: str | None = None) -> tuple[int, int]:
        """Send the robot to a map point (mm), or to a spot in `room`; the point sent.

        `gotoPoint` is from the app; the robot followed it when sent from the
        app while lost on its way home (2026-09-28), but has not yet been sent
        from here.  Only points on the floor, outside no-go zones, are sent.
        """
        md = await self._robot_map()  # also fills the room table on a fresh install
        if room is not None:
            try:
                r = self.rooms.resolve(room)
            except RoomError as e:
                raise PlanError(str(e)) from e
            spot = mapimg.room_spot(md, r.id)
            if spot is None:
                raise PlanError(f"{r.label} has no floor on the map")
            point, where = spot, f"{r.label} {spot}"
        elif point is None:
            raise PlanError("goto needs a point or a room")
        else:
            where = str(tuple(point))
        if not mapimg.is_floor(md, point):
            raise PlanError(f"{where} is not on the floor (a wall, unknown, a no-go zone or off the map)")
        await self.robot.send("gotoPoint", goto_payload(*point))
        self._emit("info", f"sent to {where} (gotoPoint)", "goto")
        return point

    async def status(self) -> dict[str, Any]:
        vac = await self.robot.vac_status()
        out: dict[str, Any] = {
            "status": vac.get("status"),
            "status_text": status_text(vac.get("status")),
            "errors": [{"code": e, "text": error_text(e)} for e in vac.get("err_status") or []],
            "battery": None,
            "clean_water_empty": None,
            "relocating": None,
        }
        with contextlib.suppress(RobotError):
            out["battery"] = (await self.robot.battery()).get("battery_percentage")
        with contextlib.suppress(RobotError):
            out["clean_water_empty"] = (await self.robot.base_status()).get("clean_water") == 1
        with contextlib.suppress(RobotError):
            out["relocating"] = (await self.robot.clean_status()).get("is_relocating")
        out["job"] = self.job.to_dict() if self.job else None
        return out

    async def refresh_rooms(self) -> RoomTable:
        self._set_rooms(await self.robot.map_data())
        return self.rooms

    def _set_rooms(self, map_data: dict[str, Any]) -> None:
        self.rooms = RoomTable.from_map(map_data, self.config.rooms)
        self.rooms.save(self.config.rooms_cache)

    def recent_tracks(self, max_age: float | None) -> list[Track]:
        """The recorded tracks with points from the last `max_age` seconds (None: all), oldest first."""
        since = time.time() - max_age if max_age else 0
        files = []
        for f in self.config.tracks_dir.glob("*.json"):
            with contextlib.suppress(OSError):
                files.append((f.stat().st_mtime, f))
        out = []
        for mtime, f in sorted(files):
            if mtime < since:
                continue
            try:
                out.append(Track.load(f))
            except (OSError, ValueError, KeyError):
                continue
        return out

    async def _robot_map(self, fresh: bool = False) -> dict[str, Any]:
        """The robot's map, fetched once; `fresh` fetches it anew (the old one stays if that fails)."""
        if self._map_data is None or fresh:
            md = await self.robot.map_data()
            self._set_rooms(md)
            self._map_data = md
        return self._map_data

    async def map_geometry(self) -> dict[str, Any]:
        """How the map's pixels relate to map mm: x = origin_x + col × resolution, row 0 at the bottom."""
        d = await self._robot_map()
        return {
            "origin": d["real_origin_coor"][:2],
            "resolution": d["resolution"],
            "width": d["width"],
            "height": d["height"],
            "scale": mapimg.SCALE,
        }

    async def map_png(
        self, refresh: bool = False, max_age: float | None = MAP_MAX_AGE, show: Collection[str] = KINDS
    ) -> bytes:
        """The map with the tracks recorded in the last `max_age` seconds (None: all), of the kinds in `show`.

        The map itself is fetched once and again on `refresh`.  Before the first
        track is ever recorded, the robot's own track is drawn instead, but only
        with `max_age` None: its age is unknown.
        """
        md = await self._robot_map(fresh=refresh)
        since = time.time() - max_age if max_age else 0
        tracks = [line for t in self.recent_tracks(max_age) for line in t.lines(since, show)]
        if not max_age and not any(self.config.tracks_dir.glob("*.json")):
            with contextlib.suppress(RobotError):
                own = mapimg.track_points(await self.robot.path_data())
                tracks = Track(None, [{"points": own}]).lines(0, show)
        names = {r.id: r.label for r in self.rooms}
        return mapimg.png_bytes(md, names=names, tracks=tracks)

"""Runs jobs: the only thing that talks to the robot.

The daemon is "engine + web + HTTP API"; the standalone CLI runs an engine
in-process.  Nothing here imports the web or CLI code or assumes it owns the
event loop, so a Home Assistant integration could wrap it (docs/design.md §10).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from . import mapimg
from .codes import error_text, status_text
from .config import Config
from .events import EventLog
from .monitor import Ask, IdleWatch, Monitor, MonitorParams, Observation
from .payloads import HOME, STOP, run_payload
from .plan import JobRequest, PlanError, Run, plan
from .robot import Robot, RobotError
from .rooms import Room, RoomTable

__all__ = ["AnswerError", "Busy", "Engine", "Job", "JobRequest", "PlanError"]

_LOGGER = logging.getLogger(__name__)


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
        self._png: bytes | None = None
        self.params = MonitorParams(config.start_timeout, config.settle, config.gave_up_after, config.verify_after)

    # --- planning ---

    def plan(self, req: JobRequest, warnings: list[str] | None = None) -> list[Run]:
        return plan(req, self.rooms, self.config, warnings)

    # --- jobs ---

    @property
    def busy(self) -> bool:
        return self.job is not None and self.job.active

    async def submit(self, req: JobRequest) -> Job:
        """Plan and start a job in the background.  Raises PlanError, Busy."""
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
            for i, run in enumerate(job.runs):
                job.step = i + 1
                await self._wait_idle(job)
                await self._send(job, run)
                await self._follow(job, run)
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
        md = await self.robot.map_data()
        xy = (md.get("real_vac_coor") or [0, 0])[:2]
        near = mapimg.rooms_near(md, xy) if any(xy) else set()
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

    async def _observe(self) -> Observation:
        vac = await self.robot.vac_status()
        try:
            clean = await self.robot.clean_status()
        except RobotError:
            clean = None
        return Observation.from_replies(self.clock(), vac, clean)

    async def _wait_idle(self, job: Job) -> None:
        watch = IdleWatch(self.config.settle)
        deadline = self.clock() + self.config.idle_timeout
        told = False
        while True:
            o = await self._observe()
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

    async def _send(self, job: Job, run: Run) -> None:
        payload = run_payload([(r.id, s) for r, s in run.items])
        desc = ", ".join(r.label for r in run.rooms)
        self._emit("info", f"step {job.step}/{len(job.runs)}: sending {desc}", "send", job)
        await self.robot.send("runCleanTask", payload)

    async def _follow(self, job: Job, run: Run) -> None:
        room = run.rooms[0]
        m = Monitor(self.clock(), self.params, room.label, run.carry_in, run.carry_out)
        posed: Ask | None = None
        posed_at, told = 0.0, False
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
                        return
                    m.answer(choice)
                try:
                    o = await self._observe()
                except RobotError as e:
                    # a lost poll is not a lost run; keep watching
                    self._emit("warn", f"poll failed: {e}", "poll_failed", job)
                    continue
                for ev in m.step(o):
                    self._emit(ev.level, ev.msg, ev.code, job)
                if m.phase == "done":
                    return
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

    async def map_png(self, refresh: bool = False) -> bytes:
        """The last rendered map with the track; `refresh` fetches it anew."""
        if self._png is None or refresh:
            md = await self.robot.map_data()
            self._set_rooms(md)
            try:
                path = await self.robot.path_data()
            except RobotError:
                path = None
            names = {r.id: r.label for r in self.rooms}
            self._png = mapimg.png_bytes(md, path, names=names)
        return self._png

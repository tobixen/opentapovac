"""Runs jobs: the only thing that talks to the robot.

The daemon is "engine + web + HTTP API"; the standalone CLI runs an engine
in-process.  Nothing here imports the web or CLI code or assumes it owns the
event loop, so a Home Assistant integration could wrap it (docs/design.md §10).
"""

from __future__ import annotations

import asyncio
import contextlib
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
from .monitor import IdleWatch, Monitor, MonitorParams, Observation
from .payloads import HOME, MODE_LABELS, STOP, Settings, run_payload
from .robot import Robot, RobotError
from .rooms import Room, RoomError, RoomTable


class PlanError(ValueError):
    """The request can't be turned into runs (unknown or forbidden room, bad setting)."""


class Busy(RuntimeError):
    pass


class JobFailed(Exception):
    pass


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
class Job:
    request: JobRequest
    runs: list[list[tuple[Room, Settings]]]
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    state: str = "queued"  # queued, running, done, failed, stopped
    message: str = ""
    step: int = 0
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    finished: str | None = None

    @property
    def active(self) -> bool:
        return self.state in ("queued", "running")

    def describe(self) -> str:
        parts = []
        for run in self.runs:
            parts.append(", ".join(f"{r.label} ({MODE_LABELS[s.mode]})" for r, s in run))
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "message": self.message,
            "step": self.step,
            "steps": len(self.runs),
            "description": self.describe(),
            "rooms": [[r.id for r, _ in run] for run in self.runs],
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
        self.params = MonitorParams(config.start_timeout, config.settle, config.gave_up_after)

    # --- planning ---

    def plan(self, req: JobRequest) -> list[list[tuple[Room, Settings]]]:
        if not req.rooms:
            raise PlanError("no rooms given")
        d = self.config.defaults
        try:
            settings = Settings(
                mode=req.mode or d.mode,
                suction=req.suction if req.suction is not None else d.suction,
                water=req.water if req.water is not None else d.water,
                passes=req.passes if req.passes is not None else d.passes,
            )
            rooms: list[Room] = []
            for token in req.rooms:
                r = self.rooms.resolve(token)
                if r not in rooms:
                    rooms.append(r)
        except (RoomError, ValueError) as e:
            raise PlanError(str(e)) from e
        bad = [r.label for r in rooms if r.forbidden]
        if bad and not req.force:
            raise PlanError(f"refusing forbidden room(s) {', '.join(bad)} without force")
        items = [(r, settings) for r in rooms]
        return [[i] for i in items] if req.sequential else [items]

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
        job = Job(req, self.plan(req))
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
        try:
            await self._preflight(job)
            for i, run in enumerate(job.runs):
                job.step = i + 1
                await self._wait_idle(job)
                await self._send(job, run)
                await self._follow(job)
            job.state = "done"
            self._emit("info", f"job {job.id} done", "job_done", job)
        except asyncio.CancelledError:
            job.state = "stopped"
            self._emit("warn", f"job {job.id} stopped", "job_stopped", job)
        except (RobotError, JobFailed) as e:
            job.state, job.message = "failed", str(e)
            self._emit("error", f"job {job.id} failed: {e}", "job_failed", job)
        finally:
            job.finished = datetime.now(UTC).isoformat(timespec="seconds")

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

    async def _send(self, job: Job, run: list[tuple[Room, Settings]]) -> None:
        payload = run_payload([(r.id, s) for r, s in run])
        desc = ", ".join(r.label for r, _ in run)
        self._emit("info", f"step {job.step}/{len(job.runs)}: sending {desc}", "send", job)
        await self.robot.send("runCleanTask", payload)

    async def _follow(self, job: Job) -> None:
        m = Monitor(sent_at=self.clock(), params=self.params)
        while True:
            await self.sleep(self.config.poll_interval)
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

    # --- direct commands ---

    async def stop(self) -> None:
        """Stop the current job (if any) and the robot's run."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self.job is not None and self.job.active:  # cancelled before it got going
            self.job.state = "stopped"
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

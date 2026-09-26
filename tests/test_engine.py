import asyncio

import pytest

from opentapovac.engine import Busy, Engine, JobRequest, PlanError
from opentapovac.payloads import STOP
from opentapovac.rooms import RoomTable
from tests.conftest import FakeClock, FakeRobot, make_map


def make_engine(config, events, robot, rooms=True):
    clock = FakeClock()
    table = RoomTable.from_map(make_map(), config.rooms) if rooms else None
    return Engine(robot, config, events, clock=clock, sleep=clock.sleep, rooms=table), clock


A_RUN = [16, 1, 1, 4, 19, 16]


async def test_run_to_done(config, events):
    robot = FakeRobot(A_RUN)
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen", "outer hall"], mode="vac"))
    assert job.state == "done", job.message
    [(method, payload)] = robot.sent
    assert method == "runCleanTask"
    assert [(a["id"], a["clean_type"]) for a in payload["area_list"]] == [(1, 2), (6, 2)]
    assert "done" in [r["code"] for r in events.recent()]


async def test_sequential_is_one_run_per_room(config, events):
    robot = FakeRobot(A_RUN + A_RUN)
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen", "6"], sequential=True))
    assert job.state == "done", job.message
    assert [[a["id"] for a in p["area_list"]] for _, p in robot.sent] == [[1], [6]]


async def test_waits_for_idle_before_sending(config, events):
    robot = FakeRobot([5, 5, 5, 5, 5, 1, 4, 16])
    engine, clock = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message
    assert clock.t >= config.settle


async def test_rejected_send_fails_job(config, events):
    robot = FakeRobot(A_RUN, fail={"runCleanTask": "error -3002"})
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "failed"
    assert "-3002" in job.message


async def test_forbidden_room(config, events):
    engine, _ = make_engine(config, events, FakeRobot(A_RUN))
    with pytest.raises(PlanError, match="stairs"):
        await engine.submit(JobRequest(rooms=["stairs"]))
    job = await engine.run(JobRequest(rooms=["stairs"], force=True))
    assert job.state == "done"


async def test_unknown_room(config, events):
    engine, _ = make_engine(config, events, FakeRobot(A_RUN))
    with pytest.raises(PlanError, match="bathroom"):
        await engine.submit(JobRequest(rooms=["bathroom"]))


async def test_unlocked_map_refused(config, events):
    robot = FakeRobot(A_RUN, map_info={"current_map_id": 42, "map_list": [{"map_id": 42, "map_locked": 0}]})
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "failed"
    assert "not locked" in job.message
    assert robot.sent == []


async def test_busy(config, events):
    robot = FakeRobot([16, 1])  # never finishes
    engine, _ = make_engine(config, events, robot)
    await engine.submit(JobRequest(rooms=["kitchen"]))
    with pytest.raises(Busy):
        await engine.submit(JobRequest(rooms=["kitchen"]))
    await engine.stop()
    assert engine.job.state == "stopped"
    assert robot.sent[-1] == ("runCleanTask", STOP)


async def test_rooms_fetched_when_cache_empty(config, events):
    engine, _ = make_engine(config, events, FakeRobot(A_RUN), rooms=False)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done"
    assert (config.cache_dir / "rooms.json").exists()


async def test_status(config, events):
    engine, _ = make_engine(config, events, FakeRobot([{"status": 7, "err_status": [3]}]))
    st = await engine.status()
    assert st["status_text"] == "paused"
    assert st["errors"] == [{"code": 3, "text": "stuck"}]
    assert st["battery"] == 88
    assert st["job"] is None


async def test_home(config, events):
    robot = FakeRobot()
    engine, _ = make_engine(config, events, robot)
    await engine.home()
    assert robot.sent == [("setSwitchCharge", {"switch_charge": True})]


async def test_map_png(config, events):
    engine, _ = make_engine(config, events, FakeRobot())
    png = await engine.map_png(refresh=True)
    assert png.startswith(b"\x89PNG")
    assert await engine.map_png() is png


async def test_stop_without_job(config, events):
    robot = FakeRobot()
    engine, _ = make_engine(config, events, robot)
    await engine.stop()
    assert robot.sent == [("runCleanTask", STOP)]
    await asyncio.sleep(0)


async def test_standby_counts_as_idle_with_warning(config, events):
    engine, _ = make_engine(config, events, FakeRobot([0, 0, 0, 0, 1, 4, 16]))
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message
    assert "standby" in [r["code"] for r in events.recent()]


async def test_unexpected_error_fails_job(config, events):
    class Broken(FakeRobot):
        async def _raw(self, method, params):
            raise TypeError("boom")

    engine, _ = make_engine(config, events, Broken())
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "failed"
    assert "boom" in job.message

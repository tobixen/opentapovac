import asyncio

import pytest

from opentapovac.engine import AnswerError, Busy, Engine, JobRequest, PlanError
from opentapovac.payloads import STOP
from opentapovac.robot import RobotError
from opentapovac.rooms import RoomTable
from opentapovac.tracks import Track
from tests.conftest import FakeClock, FakeRobot, make_map, record, vac


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


async def until(pred):
    for _ in range(10000):
        if pred():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition never came true")


def carry_config(config, **flags):
    config.rooms[6] = {"aliases": ["outer hall"], **flags}
    return config


IN_ROOM_6 = [750, 250, 0]


async def carry_in_job(engine, robot, rooms=("outer hall",)):
    """Submit, and wait for the carry-in question: the robot sits at "cleaning" on its way to the door."""
    job = await engine.submit(JobRequest(rooms=list(rooms)))
    await until(lambda: job.question is not None)
    return job


async def test_carry_in_asks_once_it_leaves_the_base(config, events):
    robot = FakeRobot([16, 17, 15, 1], map_data=make_map(real_vac_coor=IN_ROOM_6))
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    # sent at once: the mops go on before anyone carries it
    assert [m for m, _ in robot.sent] == ["runCleanTask"]
    assert job.state == "waiting"
    assert "outer hall" in job.question.text
    assert job.to_dict()["question"]["choices"] == ["done", "skip"]
    engine.answer(job.id, "done")
    robot.statuses = [vac(1)] * 5 + [vac(4), vac(16)]
    await engine.wait()
    assert job.state == "done", job.message
    assert job.question is None
    assert {"ask", "answered", "position_ok"} <= {r["code"] for r in events.recent()}


async def test_carry_in_seen_lifted_needs_no_answer(config, events):
    robot = FakeRobot([16, 1], map_data=make_map(real_vac_coor=IN_ROOM_6))
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    robot.statuses = [vac(0, 4), vac(1)] + [vac(1)] * 5 + [vac(4), vac(16)]
    await engine.wait()
    assert job.state == "done", job.message
    assert {"carried", "position_ok"} <= {r["code"] for r in events.recent()}


async def test_carry_in_skip_goes_on_with_the_rest(config, events):
    robot = FakeRobot([16, 1])
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot, rooms=("kitchen", "outer hall"))
    engine.answer(job.id, "skip")
    robot.statuses = [vac(16), vac(1), vac(4), vac(16)]
    await engine.wait()
    assert job.state == "done", job.message
    assert [(m, p.get("clean_on"), [a["id"] for a in p.get("area_list", [])]) for m, p in robot.sent] == [
        ("runCleanTask", True, [6]),
        ("runCleanTask", False, []),
        ("runCleanTask", True, [1]),
    ]
    assert "skipped" in [r["code"] for r in events.recent()]


async def test_position_wrong_stops_the_robot(config, events):
    robot = FakeRobot([16, 1], map_data=make_map(real_vac_coor=[200, 200, 0]))
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    engine.answer(job.id, "done")
    await engine.wait()
    assert job.state == "failed"
    assert "kjøkken" in job.message
    assert robot.sent[-1] == ("runCleanTask", STOP)


async def test_position_unknown_warns(config, events):
    robot = FakeRobot([16, 1])  # real_vac_coor [0, 0, 0], as when docked
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    engine.answer(job.id, "done")
    robot.statuses = [vac(1)] * 5 + [vac(4), vac(16)]
    await engine.wait()
    assert job.state == "done", job.message
    assert "position_unknown" in [r["code"] for r in events.recent()]


async def test_carry_out_waits_for_a_human(config, events):
    robot = FakeRobot([16, 1, 1, 4])
    engine, _ = make_engine(carry_config(config, carry_out=True), events, robot)
    job = await engine.submit(JobRequest(rooms=["outer hall"]))
    await until(lambda: job.question is not None)
    assert job.question.choices == ["done"]
    engine.answer(job.id, "done")
    robot.statuses = [vac(16)]
    await engine.wait()
    assert job.state == "done", job.message


async def test_answer_errors(config, events):
    robot = FakeRobot([16, 1])
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    with pytest.raises(AnswerError, match="no such job"):
        engine.answer("nope", "done")
    job = await carry_in_job(engine, robot)
    with pytest.raises(AnswerError, match="choose one of"):
        engine.answer(job.id, "maybe")
    engine.answer(job.id, "skip")
    robot.statuses = [vac(16)]
    await engine.wait()
    with pytest.raises(AnswerError, match="not waiting"):
        engine.answer(job.id, "done")


async def test_human_wait_timeout_alerts_but_keeps_waiting(config, events):
    config.human_wait_timeout = 100
    robot = FakeRobot([16, 1])
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    await until(lambda: "ask_timeout" in [r["code"] for r in events.recent()])
    await asyncio.sleep(0.01)
    assert job.state == "waiting"
    assert [r["level"] for r in events.recent() if r["code"] == "ask_timeout"] == ["alert"]
    engine.answer(job.id, "skip")
    robot.statuses = [vac(16)]
    await engine.wait()


async def test_stop_while_waiting(config, events):
    robot = FakeRobot([16, 1])
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    await engine.stop()
    assert job.state == "stopped"
    assert job.question is None


async def test_warns_when_carry_in_room_is_not_first(config, events):
    robot = FakeRobot([16, 1])
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot, rooms=("kitchen", "outer hall"))
    assert any("outer hall" in w for w in job.warnings)
    assert job.to_dict()["warnings"] == job.warnings
    assert "plan_warning" in [r["code"] for r in events.recent()]
    await engine.stop()


async def test_order_applied(config, events):
    config.order = {"first": [6], "last": ["kjøkken"]}
    robot = FakeRobot(A_RUN)
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen", "outer hall"]))
    assert job.state == "done", job.message
    assert [a["id"] for a in robot.sent[0][1]["area_list"]] == [6, 1]


async def test_run_records_track_and_clean_record(config, events):
    robot = FakeRobot(A_RUN)
    robot.path = (7, [(1, 0), (100, 100), (104, 100)])
    robot.records = [record(100)]
    robot.next_record = record(200, 18, 11)
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message
    track = Track.load(config.state_dir / "tracks" / f"{job.id}.json")
    assert track.points() == [[(100, 100), (104, 100)]]
    [rec] = [r for r in events.recent() if r["code"] == "clean_record"]
    assert "18 min, 11 m²" in rec["msg"]
    assert (await engine.map_png(refresh=True)).startswith(b"\x89PNG")


async def test_watch_records_app_runs(config, events):
    robot = FakeRobot([16])
    robot.path = (7, [(1, 0), (100, 100)])
    robot.records = [record(100)]
    engine, _ = make_engine(config, events, robot)
    await engine.watch_step()
    assert robot.path_calls == []  # idle: nothing to record
    robot.statuses = [vac(1)]
    await engine.watch_step()
    assert robot.path_calls == [0]
    robot.records.append(record(300, 5, 3))
    robot.statuses = [vac(16)]
    await engine.watch_step()
    assert "5 min, 3 m²" in next(r["msg"] for r in events.recent() if r["code"] == "clean_record")
    assert list((config.state_dir / "tracks").glob("app-*.json"))


async def test_watch_leaves_jobs_alone(config, events):
    robot = FakeRobot([16, 1])  # never finishes
    robot.path = (7, [(1, 0), (100, 100)])
    engine, _ = make_engine(config, events, robot)
    await engine.submit(JobRequest(rooms=["kitchen"]))
    calls = len(robot.path_calls)
    await engine.watch_step()
    assert len(robot.path_calls) == calls
    await engine.stop()


class Flaky(FakeRobot):
    """Fails the named method the first `n` times."""

    def __init__(self, *a, flaky=None, **kw):
        super().__init__(*a, **kw)
        self.flaky = dict(flaky or {})

    async def _raw(self, method, params):
        if self.flaky.get(method, 0) > 0:
            self.flaky[method] -= 1
            raise RobotError(f"{method} timed out")
        return await super()._raw(method, params)


async def test_lost_poll_while_waiting_for_idle_is_not_fatal(config, events):
    robot = Flaky(A_RUN, flaky={"getVacStatus": 1})
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message


async def test_long_loss_of_contact_alerts(config, events):
    config.idle_timeout = 100  # also the "no contact" alert threshold
    robot = Flaky([16, 1, 1, 4, 16], flaky={})
    engine, _ = make_engine(config, events, robot)
    job = await engine.submit(JobRequest(rooms=["kitchen"]))
    await until(lambda: robot.sent)
    robot.flaky["getVacStatus"] = 8  # 8 polls x 20 s
    await engine.wait()
    assert job.state == "done", job.message
    assert [r["level"] for r in events.recent() if r["code"] == "no_contact"] == ["alert"]


async def test_position_check_failure_is_a_warning(config, events):
    robot = Flaky([16, 1])
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    robot.flaky["getMapData"] = 1
    engine.answer(job.id, "done")
    robot.statuses = [vac(1)] * 5 + [vac(4), vac(16)]
    await engine.wait()
    assert job.state == "done", job.message
    assert "position_unknown" in [r["code"] for r in events.recent()]


async def test_broken_track_reply_does_not_end_the_job(config, events):
    class OddPath(FakeRobot):
        async def _raw(self, method, params):
            if method == "getPathData":
                return {"path_id": 1, "point_counts": 3}  # no pos_array
            return await super()._raw(method, params)

    engine, _ = make_engine(config, events, OddPath(A_RUN))
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message
    assert "track_failed" in [r["code"] for r in events.recent()]


async def test_concurrent_submits_start_one_job(config, events):
    class Slow(FakeRobot):
        async def _raw(self, method, params):
            await asyncio.sleep(0)  # a real query yields
            return await super()._raw(method, params)

    engine, _ = make_engine(config, events, Slow([16, 1]), rooms=False)
    results = await asyncio.gather(
        engine.submit(JobRequest(rooms=["kitchen"])),
        engine.submit(JobRequest(rooms=["kitchen"])),
        return_exceptions=True,
    )
    assert sorted(type(r).__name__ for r in results) == ["Busy", "Job"]
    await engine.stop()


async def test_watcher_backs_off_when_a_job_starts_meanwhile(config, events):
    robot = FakeRobot([1])
    robot.path = (7, [(1, 0), (100, 100)])
    engine, _ = make_engine(config, events, robot)
    real = robot.vac_status

    async def slow_vac_status():
        engine.job = type("J", (), {"active": True, "id": "x", "state": "running"})()
        return await real()

    robot.vac_status = slow_vac_status
    await engine.watch_step()
    assert robot.path_calls == []

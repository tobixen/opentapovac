import asyncio
import dataclasses
import json
import os
import time

import pytest

from opentapovac import mapimg
from opentapovac.engine import AnswerError, Busy, Engine, JobRequest, PlanError
from opentapovac.monitor import HomeGuide
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
    assert await engine.map_png() == png


def _track_file(config, name, when, kind="vac", y=100):
    f = config.tracks_dir / f"{name}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    seg = {"path_id": 7, "n": 3, "points": [[100, y], [800, y]], "marks": [[0, when, kind]]}
    f.write_text(json.dumps({"segments": [seg]}))
    os.utime(f, (when, when))


async def test_map_shows_recent_tracks_only(config, events):
    now = time.time()
    _track_file(config, "old", now - 20 * 3600)
    _track_file(config, "new", now - 3600, "mop")
    _track_file(config, "newer", now - 60, y=300)
    engine, _ = make_engine(config, events, FakeRobot())
    assert [t.path.stem for t in engine.recent_tracks(12 * 3600)] == ["new", "newer"]
    assert len(engine.recent_tracks(None)) == 3
    both = await engine.map_png(refresh=True)
    assert await engine.map_png(show={"vac"}) != both
    assert await engine.map_png(show=set()) != both
    assert await engine.map_png() == both
    _track_file(config, "newest", now, y=400)  # no refresh needed to show it
    assert await engine.map_png() != both


async def test_the_robots_own_track_is_filtered_too(config, events):
    robot = FakeRobot()
    engine, _ = make_engine(config, events, robot)
    bare = await engine.map_png(refresh=True)
    robot.path = (7, [(0, 0), (100, 100), (800, 100), (802, 300)])
    everything = await engine.map_png(max_age=None)
    assert everything != bare
    # its age is unknown, so only "all" shows it
    assert await engine.map_png() == bare
    assert await engine.map_png(max_age=None, show=set()) == bare
    assert bare != await engine.map_png(max_age=None, show={"move"}) != everything


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


async def test_home_ends_the_job_first(config, events):
    """Else the monitor sees the run end on the dock and may send what it "missed" again."""
    robot = FakeRobot([16, 1])
    engine, _ = make_engine(carry_config(config, carry_in=True), events, robot)
    job = await carry_in_job(engine, robot)
    await engine.home()
    assert job.state == "stopped"
    assert robot.sent[-1] == ("setSwitchCharge", {"switch_charge": True})


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
    assert track.segments[0]["marks"][0][2] == "vac"  # vac_then_mop, and getMopState says no mop
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


async def test_progress_logged_every_minute(config, events):
    robot = FakeRobot([16, 1, 1, 1, 1, 1, 1, 1, 4, 16])
    robot.clean_info_reply = {"clean_time": 20, "clean_area": 9, "clean_percent": 45}
    robot.mop = True
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message
    lines = [r["msg"] for r in events.recent() if r["code"] == "progress"]
    assert len(lines) == 3  # polls every 20 s for ~180 s, a line a minute
    assert lines[0].startswith("45 %, 20 min, 9 m², mopping")
    assert "battery 88 %" in lines[0]


async def test_progress_logged_by_the_watcher(config, events):
    robot = FakeRobot([1])
    robot.mop = False
    engine, _ = make_engine(config, events, robot)
    await engine.watch_step()
    assert [r["msg"] for r in events.recent() if r["code"] == "progress"][0].startswith("20 %, 5 min, 3 m², vacuuming")


async def test_progress_names_the_room(config, events):
    robot = FakeRobot([16, 1, 1, 1, 1, 4, 16])
    robot.path = (7, [(1, 0), (100, 100)])
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message
    assert " in " in [r["msg"] for r in events.recent() if r["code"] == "progress"][0]


async def test_a_corrupt_map_costs_only_the_room_name(config, events):
    robot = FakeRobot([16, 1, 1, 1, 1, 4, 16])
    robot.path = (7, [(1, 0), (100, 100)])
    robot.map_data_reply = {**robot.map_data_reply, "map_data": "AAAA"}  # lz4 refuses it
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["kitchen"]))
    assert job.state == "done", job.message
    assert [r for r in events.recent() if r["code"] == "progress"]


async def test_progress_leaves_out_what_the_robot_did_not_say(config, events):
    robot = FakeRobot([1])
    robot.clean_info_reply = {"clean_percent": 20}
    robot.mop = False
    engine, _ = make_engine(config, events, robot)
    await engine.watch_step()
    line = [r["msg"] for r in events.recent() if r["code"] == "progress"][0]
    assert line.startswith("20 %, vacuuming"), line
    assert "None" not in line


def _sends(robot):
    return [p for m, p in robot.sent if m == "runCleanTask" and p.get("clean_on")]


# vacuumed, went home, washed the mop and ended: no mop pass (2026-09-28 00:48)
NO_MOP = [16, 1, 1, 4, 15, 16]


def forgetful(statuses, percent=100):
    """A robot that doesn't mop, reaches `percent`, and writes a clean record for each run."""
    robot = FakeRobot(statuses)
    robot.mop = False
    robot.clean_info_reply = {**robot.clean_info_reply, "clean_percent": percent}
    stamps = iter(range(1000, 100000, 1000))
    robot.next_record = lambda: record(next(stamps))
    return robot


async def test_missed_mop_pass_is_sent_again_as_mop(config, events):
    robot = forgetful([*NO_MOP, 16, 15, 1, 1, 4, 16])
    robot.mop = lambda: len(_sends(robot)) > 1  # mops on the second run
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert job.state == "done", job.message
    first, again = _sends(robot)
    assert [a["id"] for a in again["area_list"]] == [6]
    assert again["area_list"][0]["clean_type"] == 1  # mop only, as in test_web's mop job
    assert first["area_list"][0]["clean_type"] != 1
    assert "redo" in [r["code"] for r in events.recent()]
    assert any("again" in r["msg"] for r in events.recent() if r["code"] == "send")


async def test_missed_mop_pass_sent_again_only_once(config, events):
    robot = forgetful([*NO_MOP, *NO_MOP, *NO_MOP])
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert job.state == "done", job.message
    assert len(_sends(robot)) == 2


async def test_redo_missed_off(config, events):
    config.redo_missed = False
    robot = forgetful([*NO_MOP, *NO_MOP])
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert len(_sends(robot)) == 1
    assert "no_mop_pass" in [r["code"] for r in events.recent()]


@pytest.mark.parametrize(("attr", "value", "word"), [("clean_water", 1, "water"), ("battery_pct", 25, "battery")])
async def test_no_redo_with_the_water_tank_empty_or_the_battery_low(config, events, attr, value, word):
    robot = forgetful([*NO_MOP, *NO_MOP])
    engine, _ = make_engine(config, events, robot)
    setattr(robot, attr, value)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert len(_sends(robot)) == 1
    [rec] = [r for r in events.recent() if r["code"] == "redo_skipped"]
    assert word in rec["msg"]


async def test_no_redo_when_the_robot_does_not_answer_the_checks(config, events):
    robot = forgetful([*NO_MOP, *NO_MOP])
    robot.fail = {"getBatteryInfo": "timeout"}
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert len(_sends(robot)) == 1
    assert "redo_skipped" in [r["code"] for r in events.recent()]


async def test_no_redo_when_stopped_by_a_human_mid_pass(config, events):
    """Stopped from the app during the vacuum pass: it docks at 20 %, which is not the robot forgetting."""
    robot = forgetful([16, 1, 1, 0, 4, 16, *NO_MOP], percent=20)
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert len(_sends(robot)) == 1
    assert "no_mop_pass" in [r["code"] for r in events.recent()]


async def test_no_redo_without_a_new_clean_record(config, events):
    """Charging mid-run for longer than `settle` looks done; the robot writes no record then."""
    robot = forgetful([16, 1, 1, 4, *[5] * 8, *NO_MOP])
    robot.next_record = None
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert len(_sends(robot)) == 1


async def test_no_redo_when_mop_reads_failed_while_cleaning(config, events):
    robot = forgetful([*NO_MOP, *NO_MOP])
    reads = iter([False])  # then it fails: a mop pass it may have had
    robot.mop = lambda: next(reads, None)
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    assert len(_sends(robot)) == 1


async def test_only_the_rooms_to_mop_are_mopped_again(config, events):
    robot = forgetful([*NO_MOP, *NO_MOP])
    engine, _ = make_engine(config, events, robot)
    req = JobRequest(rooms=["kitchen", "outer hall"], mode="vac_then_mop")
    [run] = engine.plan(req)
    run.items[0] = (run.items[0][0], dataclasses.replace(run.items[0][1], mode="vac"))
    assert [(r.id, s.mode) for r, s in engine._redo_run(run, ["mop"]).items] == [(run.items[1][0].id, "mop")]


LOST_DOCK = [16, 1, 4, vac(0, 21), vac(0), 4, 19, 16]


async def test_unfinished_run_is_sent_again_when_a_human_says_so(config, events):
    robot = forgetful([*LOST_DOCK, 16, 1, 4, 16], percent=20)  # lost at 20 %, as at 00:48
    engine, _ = make_engine(config, events, robot)
    job = await engine.submit(JobRequest(rooms=["outer hall"], mode="vac"))
    await until(lambda: job.question is not None)
    assert job.question.code == "redo"
    engine.answer(job.id, "again")
    await engine.wait()
    assert job.state == "done", job.message
    first, again = _sends(robot)
    assert first == again
    assert job.message == ""  # the second run finished


async def test_unfinished_run_not_sent_again_without_an_answer(config, events):
    robot = forgetful([*LOST_DOCK, *LOST_DOCK], percent=20)  # lost at 20 %, as at 00:48
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert len(_sends(robot)) == 1
    assert "unfinished" in job.message
    assert job.question is None


async def test_stop_during_the_redo(config, events):
    robot = forgetful([*NO_MOP, 16, 15, 1])
    engine, _ = make_engine(config, events, robot)
    job = await engine.submit(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    await until(lambda: len(_sends(robot)) == 2)
    await engine.stop()
    assert job.state == "stopped"
    assert len(_sends(robot)) == 2


async def test_mop_state_read_on_every_poll_while_cleaning(config, events):
    robot = FakeRobot([16, 1, 1, 1, 1, 4, 16])
    asked = []
    robot.mop = lambda: asked.append(1) or False
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert len(asked) >= 4


async def test_goto_room_with_no_rooms_known_yet(config, events):
    robot = FakeRobot()
    engine, _ = make_engine(config, events, robot, rooms=False)
    x, y = await engine.goto(room="kitchen")
    assert mapimg.rooms_near(robot.map_data_reply, (x, y), 10) == {1}


async def test_failed_map_refresh_keeps_the_map(config, events):
    robot = FakeRobot()
    engine, _ = make_engine(config, events, robot)
    await engine.map_png(refresh=True)
    robot.fail = {"getMapData": "timeout"}
    with pytest.raises(RobotError):
        await engine.map_png(refresh=True)
    assert (await engine.map_png()).startswith(b"\x89PNG")


async def test_goto_point_and_room(config, events):
    robot = FakeRobot()
    engine, _ = make_engine(config, events, robot)
    await engine.goto((300, 200))
    assert robot.sent == [("gotoPoint", {"switch": True, "point": [300, 200]})]
    with pytest.raises(PlanError, match="floor"):
        await engine.goto((4100, 3000))  # off the map
    x, y = await engine.goto(room="kitchen")
    assert robot.sent[-1] == ("gotoPoint", {"switch": True, "point": [x, y]})
    assert mapimg.rooms_near(robot.map_data_reply, (x, y), 10) == {1}
    with pytest.raises(PlanError):
        await engine.goto(room="nowhere")
    assert "goto" in [r["code"] for r in events.recent()]


async def test_home_mid_run_sends_nothing_again(config, events):
    robot = forgetful([16, 1, 1, 1, 1, *NO_MOP])
    engine, _ = make_engine(config, events, robot)
    job = await engine.submit(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    await until(lambda: len(_sends(robot)) == 1)
    await engine.home()
    assert job.state == "stopped"
    assert robot.sent[-1] == ("setSwitchCharge", {"switch_charge": True})
    assert ("runCleanTask", STOP) not in robot.sent
    assert len(_sends(robot)) == 1


async def test_home_command_failing_after_the_job_ended(config, events):
    robot = FakeRobot([16, 1])
    engine, _ = make_engine(config, events, robot)
    robot.fail = {"setSwitchCharge": "-40210"}
    with pytest.raises(RobotError):
        await engine.home()
    assert "home_failed" in [r["code"] for r in events.recent()]


async def test_goto_ends_the_job_first(config, events):
    """Guiding the robot home is a human taking over: no redo when it docks."""
    robot = forgetful([16, 1, 1, 1, 1, *NO_MOP])
    engine, _ = make_engine(config, events, robot)
    job = await engine.submit(JobRequest(rooms=["outer hall"], mode="vac_then_mop"))
    await until(lambda: len(_sends(robot)) == 1)
    await engine.goto(room="kitchen")
    assert job.state == "stopped"
    assert robot.sent[-1][0] == "gotoPoint"


# heading home it loses the dock; the goto is taken (11) and done (0), twice; then home
LOST_ON_THE_WAY_HOME = [16, 1, 4, vac(0, 21), 11, 0, 11, 0, 4, 19, 16]


def _gotos(robot):
    return [tuple(p["point"]) for m, p in robot.sent if m == "gotoPoint"]


async def test_lost_on_the_way_home_is_guided_by_the_home_route(config, events):
    config.home_route = ["outer hall", "kitchen"]
    robot = FakeRobot(LOST_ON_THE_WAY_HOME)
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    hall, kitchen = _gotos(robot)
    assert mapimg.rooms_near(robot.map_data_reply, hall, 10) == {6}
    assert mapimg.rooms_near(robot.map_data_reply, kitchen, 10) == {1}
    methods = [m for m, _ in robot.sent]
    assert methods.index("setSwitchCharge") > methods.index("gotoPoint")
    assert "guide" in [r["code"] for r in events.recent()]


async def test_home_route_overridden_by_the_room_it_is_lost_in(config, events):
    config.home_route = ["outer hall", "kitchen"]
    config.rooms[6] = {"aliases": ["outer hall"], "home_route": ["kitchen"]}
    robot = FakeRobot([16, 1, 4, vac(0, 21), 11, 0, 4, 19, 16])
    robot.path = (7, [(1, 0), (700, 200)])  # in room 6
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    [kitchen] = _gotos(robot)
    assert mapimg.rooms_near(robot.map_data_reply, kitchen, 10) == {1}


async def test_no_home_route_no_guide(config, events):
    config.rooms[6] = {"aliases": ["outer hall"], "home_route": []}
    config.home_route = ["kitchen"]
    robot = FakeRobot([16, 1, 4, vac(0, 21), 4, 19, 16])
    robot.path = (7, [(1, 0), (700, 200)])
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert _gotos(robot) == []
    config.home_route = []
    robot = FakeRobot([16, 1, 4, vac(0, 21), 4, 19, 16])
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert _gotos(robot) == []


async def test_guided_once_per_run(config, events):
    config.home_route = ["kitchen"]
    robot = FakeRobot([16, 1, 4, vac(0, 21), 11, 0, 4, vac(0, 21), vac(0, 21), 4, 19, 16])
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert len(_gotos(robot)) == 1


async def test_watcher_guides_an_app_run_home(config, events):
    config.home_route = ["outer hall", "kitchen"]
    # each look asks twice (the status, then the progress line)
    robot = FakeRobot([s for s in (4, vac(0, 21), 11, 0, 11, 0, 4, 16) for _ in range(2)])
    engine, _ = make_engine(config, events, robot)
    for _ in range(9):
        await engine.watch_step()
    assert len(_gotos(robot)) == 2
    assert robot.sent[-1] == ("setSwitchCharge", {"switch_charge": True})


async def test_stop_ends_the_guide(config, events):
    config.home_route = ["outer hall", "kitchen"]
    robot = FakeRobot([16, 1, 4, vac(0, 21), 11, 0, 11, 0, 0, 0])
    engine, _ = make_engine(config, events, robot)
    await engine.submit(JobRequest(rooms=["outer hall"], mode="vac"))
    await until(lambda: len(_gotos(robot)) == 1)
    await engine.stop()
    for _ in range(6):
        await engine.watch_step()
    assert len(_gotos(robot)) == 1
    assert "setSwitchCharge" not in [m for m, _ in robot.sent]


async def test_guide_leaves_remote_control_alone(config, events):
    config.home_route = ["outer hall", "kitchen"]
    robot = FakeRobot([16, 1, 4, vac(0, 21), 11, 3, 3, 3, 4, 19, 16])  # a human steers it from the app
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert len(_gotos(robot)) == 1
    assert "setSwitchCharge" not in [m for m, _ in robot.sent]


async def test_no_guide_while_homing_by_itself_or_in_a_carry_run(config, events):
    config.home_route = ["kitchen"]
    robot = FakeRobot([16, 1, vac(4, 21), vac(4, 21), 4, 19, 16])  # err 21, but still trying
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert _gotos(robot) == []
    robot = FakeRobot([16, 1, 1, 4, vac(0, 21), vac(0, 21), vac(0, 21)])
    engine, _ = make_engine(carry_config(config, carry_out=True), events, robot)
    job = await engine.submit(JobRequest(rooms=["outer hall"], mode="vac"))
    await until(lambda: job.question is not None)
    for _ in range(50):
        await asyncio.sleep(0)
    assert _gotos(robot) == []
    await engine.stop()


async def test_watcher_guides_again_only_after_the_base(config, events):
    config.home_route = ["kitchen"]
    looks = [4, vac(0, 21), 11, 0, 0, vac(0, 21), vac(0, 21), vac(0, 21)]
    robot = FakeRobot([s for s in looks for _ in range(2)])
    engine, _ = make_engine(config, events, robot)
    for _ in range(len(looks)):
        await engine.watch_step()
    assert len(_gotos(robot)) == 1


async def test_guides_at_most_three_a_day(config, events):
    config.home_route = ["kitchen"]
    lost = [4, vac(0, 21), 11, 0, 5]  # guided, then back on the base
    robot = FakeRobot([s for s in lost * 5 for _ in range(2)])
    engine, _ = make_engine(config, events, robot)
    for _ in range(len(lost) * 5):
        await engine.watch_step()
    assert len(_gotos(robot)) == 3


async def test_forbidden_rooms_left_out_of_the_route(config, events):
    config.home_route = ["stairs", "kitchen"]
    robot = FakeRobot(LOST_ON_THE_WAY_HOME)
    engine, _ = make_engine(config, events, robot)
    await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    [kitchen] = _gotos(robot)
    assert mapimg.rooms_near(robot.map_data_reply, kitchen, 10) == {1}
    assert any("stairs" in r["msg"] for r in events.recent() if r["code"] == "config")


async def test_unknown_route_rooms_warned_when_the_rooms_load(config, events):
    config.home_route = ["bathroom"]
    make_engine(config, events, FakeRobot())
    assert any("bathroom" in r["msg"] for r in events.recent() if r["code"] == "config")


async def test_no_redo_question_after_a_guided_trip_home_from_a_finished_run(config, events):
    config.home_route = ["outer hall", "kitchen"]
    robot = FakeRobot(LOST_ON_THE_WAY_HOME)
    robot.clean_info_reply = {**robot.clean_info_reply, "clean_percent": 100}
    engine, _ = make_engine(config, events, robot)
    job = await engine.run(JobRequest(rooms=["outer hall"], mode="vac"))
    assert job.state == "done"
    assert "ask" not in [r["code"] for r in events.recent()]


def test_watcher_looks_more_often_while_guiding(config, events):
    engine, _ = make_engine(config, events, FakeRobot())
    assert engine.watch_delay() == config.watch_interval
    engine._guide = HomeGuide([("hall", (0, 0))], 180)
    assert engine.watch_delay() == config.poll_interval


async def test_position_found_after_a_relocation(config, events):
    robot = FakeRobot([16, 1, 1, 1, 1, 4, 16])
    looks = iter(range(1000))
    robot.relocating = lambda: 2 <= next(looks) < 4  # the 2nd and 3rd poll of the run
    moved = []
    robot.path = lambda: (7, [(1, 0), (200, 200), *moved])
    engine, _ = make_engine(config, events, robot)

    def relocated():
        codes = [r["code"] for r in events.recent()]
        if "relocated" in codes and not moved:
            moved.append((700, 200))  # it moves on, in room 6
        return codes

    job = await engine.submit(JobRequest(rooms=["outer hall"], mode="vac"))
    await until(lambda: "relocated" in relocated())
    await engine.wait()
    assert job.state == "done", job.message
    [rec] = [r for r in events.recent() if r["code"] == "position"]
    assert rec["msg"].startswith("position found: in outer hall")
    codes = [r["code"] for r in events.recent()]
    assert codes.index("relocated") < codes.index("position")

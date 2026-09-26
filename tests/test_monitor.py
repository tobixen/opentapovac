import json

from opentapovac.monitor import IdleWatch, Monitor, MonitorParams, Observation
from tests.conftest import FIXTURES

P = MonitorParams(start_timeout=120, settle=60, gave_up_after=300)


def obs(t, status, *errors, **kw):
    return Observation(t=t, status=status, errors=tuple(errors), **kw)


def feed(m, observations):
    events = []
    for o in observations:
        events += m.step(o)
    return events


def codes(events):
    return [e.code for e in events]


def evening_runs():
    """The recorded 2026-09-24 evening log, split at each accepted send."""
    runs = []
    for line in (FIXTURES / "status-2026-09-24-evening.log").read_text().splitlines():
        hms, rest = line.split(" ", 1)
        h, m, s = map(int, hms.split(":"))
        t = h * 3600 + m * 60 + s
        if rest.startswith("SEND"):
            if rest.endswith("null}"):
                runs.append((t, []))
            continue
        vac = json.loads(rest.removeprefix("WAIT "))["getVacStatus"]
        if runs:
            runs[-1][1].append(Observation.from_replies(t, vac))
    return runs


def test_evening_log_first_run_stuck_then_done():
    (t0, observations), _ = evening_runs()
    m = Monitor(sent_at=t0, params=P)
    events = feed(m, observations)
    assert m.phase == "done"
    assert "stuck" in codes(events)
    # the brief 5 on the way in is not the end of the run
    assert codes(events).count("done") == 1


def test_evening_log_second_run_dock_not_found_recovers():
    _, (t0, observations) = evening_runs()
    m = Monitor(sent_at=t0, params=P)
    events = feed(m, observations)
    assert "dock_not_found" in codes(events)
    assert m.phase == "done"


def test_gives_up_after_grace():
    m = Monitor(sent_at=0, params=P)
    feed(m, [obs(20, 1), obs(100, 4), obs(200, 0, 21), obs(400, 0, 21)])
    assert m.phase == "running"
    events = m.step(obs(501, 0, 21))
    assert m.phase == "failed"
    assert "dock not found" in m.message
    assert codes(events)[-1] == "failed"


def test_start_timeout():
    m = Monitor(sent_at=0, params=P)
    feed(m, [obs(20, 16), obs(100, 16)])
    assert m.phase == "starting"
    m.step(obs(121, 16))
    assert m.phase == "failed"
    assert "did not start" in m.message


def test_not_done_before_leaving_dock():
    m = Monitor(sent_at=0, params=P)
    feed(m, [obs(10, 16), obs(20, 17), obs(40, 15)])
    assert m.phase == "running"
    feed(m, [obs(60, 16)])
    assert m.phase == "running"
    feed(m, [obs(80, 1), obs(300, 4), obs(400, 19), obs(420, 16)])
    assert m.phase == "done"


def test_done_when_charging_settles():
    m = Monitor(sent_at=0, params=P)
    feed(m, [obs(20, 1), obs(300, 4), obs(400, 5), obs(430, 5)])
    assert m.phase == "running"
    m.step(obs(460, 5))
    assert m.phase == "done"


def test_water_empty_warned_once():
    m = Monitor(sent_at=0, params=P)
    events = feed(m, [obs(20, 1), obs(40, 15, 26), obs(60, 1, 26)])
    assert codes(events).count("water_empty") == 1


def test_relocation_and_mapping():
    m = Monitor(sent_at=0, params=P)
    events = feed(
        m,
        [
            obs(20, 1),
            obs(40, 1, relocating=True),
            obs(60, 1, relocating=True),
            obs(80, 1, relocating=False),
            obs(100, 1, mapping=True),
        ],
    )
    assert codes(events).count("relocating") == 1
    assert "relocated" in codes(events)
    assert [e.level for e in events if e.code == "mapping"] == ["alert"]


def test_status_changes_reported_in_words():
    m = Monitor(sent_at=0, params=P)
    events = feed(m, [obs(20, 1), obs(40, 1), obs(60, 4)])
    assert [e.msg for e in events if e.code == "status"] == ["cleaning", "going home"]


def test_idle_watch():
    w = IdleWatch(settle=60)
    assert w.feed(obs(0, 16))
    w = IdleWatch(settle=60)
    assert not w.feed(obs(0, 5))
    assert not w.feed(obs(30, 19))
    assert not w.feed(obs(40, 6))
    assert not w.feed(obs(80, 6))
    assert w.feed(obs(100, 6))


def test_idle_watch_standby():
    w = IdleWatch(settle=60)
    assert not w.feed(obs(0, 0))
    assert w.feed(obs(60, 0))
    assert w.standby
    w = IdleWatch(settle=60)
    assert not w.feed(obs(0, 0, 21))
    assert not w.feed(obs(100, 0, 21))


def asks(m, observations):
    """Feed observations; the codes of the questions the monitor raises."""
    out = []
    for o in observations:
        m.step(o)
        if m.ask:
            out.append(m.ask.code)
            m.answer("done")
    return out


def test_carry_out_asks_when_heading_home():
    m = Monitor(sent_at=0, params=P, room="living room", carry_out=True)
    assert asks(m, [obs(20, 1), obs(40, 1), obs(60, 4)]) == ["carry_out"]


def test_carry_out_asks_once_per_trip_home():
    m = Monitor(sent_at=0, params=P, room="living room", carry_out=True)
    trip = [obs(20, 1), obs(40, 4), obs(60, 4), obs(80, 0, 21), obs(100, 4), obs(120, 15)]
    assert asks(m, trip) == ["carry_out"]
    # back in by itself after washing the mop, then home again
    assert asks(m, [obs(140, 1), obs(160, 4)]) == ["carry_out"]


def test_carry_out_on_recharge_status():
    m = Monitor(sent_at=0, params=P, room="living room", carry_out=True)
    assert asks(m, [obs(20, 1), obs(40, 1, recharging=True)]) == ["carry_out"]


def test_no_carry_questions_by_default():
    m = Monitor(sent_at=0, params=P)
    assert asks(m, [obs(20, 17), obs(40, 1), obs(60, 4), obs(80, 15), obs(100, 1), obs(120, 4)]) == []


def test_carry_in_asks_each_time_it_leaves_the_base():
    m = Monitor(sent_at=0, params=P, room="bedroom 2", carry_in=True)
    # sent from the dock: vacuum only goes straight to "cleaning"
    assert asks(m, [obs(20, 1), obs(40, 1)]) == ["carry_in"]
    # home to wash the mop, then out again: it can't climb in by itself
    assert asks(m, [obs(60, 4), obs(80, 15), obs(100, 1), obs(120, 1)]) == ["carry_in"]


def test_carry_in_after_fitting_the_mop_at_the_start():
    m = Monitor(sent_at=0, params=P, room="bedroom 2", carry_in=True)
    assert asks(m, [obs(20, 17), obs(40, 15), obs(60, 1)]) == ["carry_in"]


def test_ask_text_names_the_room():
    m = Monitor(sent_at=0, params=P, room="living room", carry_out=True)
    events = feed(m, [obs(20, 1), obs(40, 4)])
    assert [e.level for e in events if e.code == "carry_out"] == ["alert"]
    assert "living room" in m.ask.text
    assert m.ask.choices == ["done"]


def test_no_giving_up_while_a_human_is_asked():
    m = Monitor(sent_at=0, params=P, room="living room", carry_out=True)
    feed(m, [obs(20, 1), obs(40, 4), obs(60, 0, 21), obs(1300, 0, 21)])
    assert m.phase == "running"
    m.answer("done")  # the standby clock starts again
    feed(m, [obs(1320, 0, 21), obs(1500, 0, 21)])
    assert m.phase == "running"
    feed(m, [obs(1700, 0, 21)])
    assert m.phase == "failed"


def test_lifted_and_put_down_answers_the_question():
    m = Monitor(sent_at=0, params=P, room="bedroom 2", carry_in=True)
    feed(m, [obs(20, 17), obs(40, 1)])
    assert m.ask.code == "carry_in"
    feed(m, [obs(60, 0, 4)])
    assert m.ask is not None
    events = feed(m, [obs(80, 1, relocating=True)])
    assert m.ask is None
    assert "carried" in codes(events)


def test_position_check_due_after_the_carry():
    p = MonitorParams(verify_after=60)
    m = Monitor(sent_at=0, params=p, room="bedroom 2", carry_in=True)
    feed(m, [obs(20, 1), obs(40, 1), obs(60, 1), obs(80, 1), obs(100, 1)])
    assert not m.verify_due  # still on its way to the door
    m.answer("done")
    feed(m, [obs(120, 1, relocating=True), obs(140, 1, relocating=False), obs(180, 1)])
    assert not m.verify_due
    feed(m, [obs(200, 1)])
    assert m.verify_due


def test_no_position_check_without_carry_in():
    m = Monitor(sent_at=0, params=MonitorParams(verify_after=60), room="living room", carry_out=True)
    feed(m, [obs(20, 1), obs(100, 1), obs(200, 1)])
    assert not m.verify_due


def test_recharge_status_in_observation():
    o = Observation.from_replies(0, {"status": 1}, {"recharge_status": 1, "is_relocating": False})
    assert o.recharging is True
    assert Observation.from_replies(0, {"status": 1}, {"recharge_status": 0}).recharging is False
    assert Observation.from_replies(0, {"status": 1}).recharging is None


def test_carry_out_not_asked_while_washing_the_mop_before_the_start():
    # 2026-09-26: a vac-and-mop run started with 15 at the base, recharge_status 1
    m = Monitor(sent_at=0, params=P, room="living room", carry_out=True)
    assert asks(m, [obs(20, 15, recharging=True), obs(40, 17, recharging=True)]) == []
    assert asks(m, [obs(60, 1), obs(80, 4)]) == ["carry_out"]


def test_done_after_losing_the_dock_is_flagged():
    # 2026-09-26 evening: err 21 on a trip home mid-run, carried to the dock, run abandoned
    m = Monitor(sent_at=0, params=P)
    events = feed(m, [obs(20, 1), obs(600, 4), obs(840, 0, 21), obs(860, 0), obs(880, 4), obs(900, 19), obs(1000, 16)])
    assert m.phase == "done"
    assert "unfinished" in m.message
    assert [e.level for e in events if e.code == "maybe_unfinished"] == ["alert"]


def test_dock_lost_then_back_out_is_not_flagged():
    m = Monitor(sent_at=0, params=P)
    feed(
        m,
        [
            obs(20, 1),
            obs(600, 4),
            obs(840, 0, 21),
            obs(880, 4),
            obs(900, 15),
            obs(1000, 1),
            obs(1600, 4),
            obs(1700, 16),
        ],
    )
    assert m.phase == "done"
    assert m.message == ""


def test_going_home_at_the_start_is_not_leaving_the_dock():
    # from standby off the dock it first goes to the base to fit the mop (4 -> 17 -> 15 -> 1)
    m = Monitor(sent_at=0, params=P)
    feed(m, [obs(20, 4), obs(40, 17), obs(60, 15), obs(80, 16)])
    assert m.phase == "running"


def test_standby_mid_run_is_not_done():
    # review finding: error-free standby off the dock ended a run as "done" after `settle`
    m = Monitor(sent_at=0, params=P)
    feed(m, [obs(20, 1), obs(40, 0), obs(100, 0), obs(200, 0)])
    assert m.phase == "running"
    feed(m, [obs(400, 0)])
    assert m.phase == "failed"
    assert "base" in m.message  # standby without an error: an unpowered dock is the likely cause


def test_standby_at_the_doorstep_keeps_the_question_open():
    m = Monitor(sent_at=0, params=P, room="bedroom 2", carry_in=True)
    feed(m, [obs(20, 5), obs(40, 1)])
    assert m.ask.code == "carry_in"
    feed(m, [obs(60, 0), obs(120, 0), obs(400, 0), obs(2000, 0)])
    assert m.phase == "running"
    assert m.ask is not None


def test_back_at_the_base_without_cleaning_fails():
    # review finding: 17 -> 15 (err 26) -> 6 stayed "running" for ever
    m = Monitor(sent_at=0, params=P)
    feed(m, [obs(20, 17), obs(40, 15, 26), obs(60, 6), obs(150, 6)])
    assert m.phase == "running"
    feed(m, [obs(200, 6)])
    assert m.phase == "failed"
    assert "without cleaning" in m.message


def test_passes_are_logged():
    m = Monitor(sent_at=0, params=P)
    events = feed(m, [obs(20, 1, mop=False), obs(40, 1, mop=False), obs(60, 15), obs(80, 1, mop=True)])
    assert [e.code for e in events if e.code.endswith("_pass")] == ["vacuum_pass", "mop_pass"]


def test_skipped_vacuum_pass_warned():
    # 2026-09-26 22:18: vacuum then mop, but it went out once and came back for the mop
    m = Monitor(sent_at=0, params=P, vacuum_first=True)
    events = feed(m, [obs(20, 17), obs(40, 15), obs(60, 1, mop=True)])
    assert [e.level for e in events if e.code == "skipped_vacuum"] == ["warn"]
    m = Monitor(sent_at=0, params=P, vacuum_first=True)
    assert "skipped_vacuum" not in codes(feed(m, [obs(20, 1, mop=False), obs(600, 1, mop=True)]))


def test_no_progress_warned_once():
    # 2026-09-26 22:47: at the kitchen doorstep, 45 % for many minutes
    m = Monitor(sent_at=0, params=P)
    events = feed(m, [obs(20, 1, percent=45), obs(80, 1, percent=45), obs(340, 1, percent=45), obs(400, 1, percent=45)])
    assert [e.level for e in events if e.code == "no_progress"] == ["warn"]
    assert "45 %" in next(e.msg for e in events if e.code == "no_progress")
    # progress again: warned anew the next time it stalls
    events = feed(m, [obs(460, 1, percent=46), obs(800, 1, percent=46)])
    assert codes(events).count("no_progress") == 1


def test_no_progress_only_while_cleaning():
    m = Monitor(sent_at=0, params=P)
    events = feed(m, [obs(20, 1, percent=45), obs(80, 15, percent=45), obs(700, 15, percent=45)])
    assert "no_progress" not in codes(events)


def test_end_percentage_reported():
    m = Monitor(sent_at=0, params=P)
    events = feed(m, [obs(20, 1, percent=45), obs(40, 4), obs(60, 16)])
    assert "45 %" in next(e.msg for e in events if e.code == "done")

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
            m.ask = None
            m.resumed()
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
    # carried in by hand before the send: no question on the first "cleaning"
    assert asks(m, [obs(20, 1), obs(40, 1)]) == []
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


def test_resumed_restarts_the_standby_clock():
    m = Monitor(sent_at=0, params=P, room="living room", carry_out=True)
    feed(m, [obs(20, 1), obs(40, 4), obs(60, 0, 21)])
    m.ask = None
    m.resumed()  # the human took 20 minutes
    feed(m, [obs(1300, 0, 21)])
    assert m.phase == "running"


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

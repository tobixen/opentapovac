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

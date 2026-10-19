import time
from datetime import UTC, datetime

import pytest

from opentapovac.robotclock import RobotClock


def ts(*args) -> int:
    return int(datetime(*args, tzinfo=UTC).timestamp())


def clock(at, time_diff=60, dnd=True, start=1320, end=480, timezone="Europe/Oslo") -> RobotClock:
    return RobotClock.from_replies(
        {"timestamp": at, "time_diff": time_diff, "region": "Europe/Oslo"},
        {"do_not_disturb": dnd, "s_min": start, "e_min": end},
        timezone,
    )


def test_2026_09_28_morning_was_inside_the_quiet_hours():
    # read that evening: time_diff 60 on a UTC+2 day, quiet 22:00–08:00 robot time
    c = clock(ts(2026, 9, 28, 5, 37))  # 07:37 in Oslo, 06:37 robot time
    assert c.skew == -60
    assert c.quiet()
    assert c.to_dict() == {
        "time_diff": 60,
        "skew": -60,
        "quiet_hours": "22:00–08:00",
        "quiet_hours_local": "23:00–09:00",
        "quiet_now": True,
        "quiet_starts_in": None,
        "quiet_ends_in": 83,  # until 08:00 robot time
    }


def test_after_the_quiet_hours_end_in_robot_time():
    c = clock(ts(2026, 9, 28, 7, 30))  # 09:30 Oslo, 08:30 robot time
    assert not c.quiet()
    assert c.starts_in() == 810  # at 22:00 robot time
    assert c.ends_in() is None
    assert clock(ts(2026, 9, 28, 6, 30)).quiet()  # 08:30 Oslo, but 07:30 robot time


def test_right_offset_in_winter():
    c = clock(ts(2026, 12, 1, 6, 0))  # 07:00 Oslo, UTC+1
    assert c.skew == 0
    assert c.to_dict()["quiet_hours_local"] == "22:00–08:00"
    assert c.quiet()


def test_quiet_hours_within_one_day():
    c = clock(ts(2026, 12, 1, 12, 0), start=600, end=900)  # 13:00 robot time
    assert c.quiet()
    assert not clock(ts(2026, 12, 1, 15, 0), start=600, end=900).quiet()


def test_quiet_hours_off():
    c = clock(ts(2026, 9, 28, 5, 37), dnd=False)
    assert not c.quiet()
    assert c.to_dict()["quiet_hours"] is None
    assert c.describe_quiet() is None


def test_describe():
    c = clock(ts(2026, 9, 28, 5, 37))
    assert c.describe_quiet() == "22:00–08:00 robot time (23:00–09:00 here)"
    assert c.describe_skew() == "the robot's clock is 60 min behind Europe/Oslo"
    assert clock(ts(2026, 12, 1, 6, 0)).describe_quiet() == "22:00–08:00"
    assert clock(ts(2026, 12, 1, 6, 0)).describe_skew() is None
    assert (
        clock(ts(2026, 12, 1, 6, 0), time_diff=120).describe_skew()
        == "the robot's clock is 60 min ahead of Europe/Oslo"
    )


def test_quiet_hours_on_but_empty_are_off():
    c = clock(ts(2026, 12, 1, 12, 0), start=600, end=600)
    assert not c.quiet()
    assert c.describe_quiet() is None
    assert c.to_dict()["quiet_hours"] is None
    assert c.starts_in() is None


def test_malformed_replies_raise():
    with pytest.raises(KeyError):
        RobotClock.from_replies({"timestamp": 1, "time_diff": 0}, {"do_not_disturb": True}, None)
    with pytest.raises(KeyError):
        RobotClock.from_replies({"time_diff": 0}, {"do_not_disturb": False, "s_min": 0, "e_min": 0}, None)


def test_local_time_zone(monkeypatch):
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    try:
        c = clock(ts(2026, 12, 1, 6, 0), timezone=None)
        assert c.describe_skew() == "the robot's clock is 60 min ahead of the local time"
    finally:
        monkeypatch.undo()
        time.tzset()

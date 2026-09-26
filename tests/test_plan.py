import pytest

from opentapovac.config import Config
from opentapovac.plan import JobRequest, PlanError, plan
from opentapovac.rooms import RoomTable

ROOMS = [
    (1, "kjøkken"),
    (2, "living room"),
    (3, "stairs"),
    (4, "bedroom 1"),
    (5, "hall"),
    (6, None),
    (7, "bedroom 2"),
]


@pytest.fixture
def config():
    return Config.from_dict(
        {
            "rooms": {
                "kjøkken": {"aliases": ["kitchen"], "dock": True},
                6: {"aliases": ["outer hall"]},
                "bedroom 2": {"carry_in": True},
                "living room": {"carry_out": True},
                "stairs": {"forbidden": True},
            },
            "order": {"first": ["bedroom 1", 6], "last": ["kjøkken"]},
        }
    )


@pytest.fixture
def table(config):
    return RoomTable(ROOMS, config.rooms)


def ids(runs):
    return [[r.id for r in run.rooms] for run in runs]


def test_order_first_and_last(config, table):
    runs = plan(JobRequest(rooms=["kitchen", "hall", "bedroom 1", "6"]), table, config)
    assert ids(runs) == [[4, 6, 5, 1]]


def test_order_keeps_request_order_for_the_rest(config, table):
    runs = plan(JobRequest(rooms=["kitchen", "6", "hall", "bedroom 1"]), table, config)
    assert ids(runs) == [[4, 6, 5, 1]]
    runs = plan(JobRequest(rooms=["hall", "kitchen"]), table, config)
    assert ids(runs) == [[5, 1]]


def test_sequential_follows_the_order(config, table):
    runs = plan(JobRequest(rooms=["kitchen", "6"], sequential=True), table, config)
    assert ids(runs) == [[6], [1]]


def test_carry_rooms_get_their_own_runs_first(config, table):
    runs = plan(JobRequest(rooms=["kitchen", "living room", "bedroom 2", "hall"]), table, config)
    assert ids(runs) == [[2], [7], [5, 1]]
    assert [(r.carry_in, r.carry_out) for r in runs] == [(False, True), (True, False), (False, False)]


def test_room_both_carry_in_and_out(table):
    config = Config.from_dict({"rooms": {"bedroom 2": {"carry_in": True, "carry_out": True}}})
    [run] = plan(JobRequest(rooms=["bedroom 2"]), RoomTable(ROOMS, config.rooms), config)
    assert run.carry_in
    assert run.carry_out


def test_unknown_rooms_in_order_are_ignored(table):
    config = Config.from_dict({"order": {"first": ["attic"], "last": [99]}})
    runs = plan(JobRequest(rooms=["hall", "kjøkken"]), RoomTable(ROOMS, config.rooms), config)
    assert ids(runs) == [[5, 1]]


def test_forbidden(config, table):
    with pytest.raises(PlanError, match="stairs"):
        plan(JobRequest(rooms=["stairs"]), table, config)
    assert ids(plan(JobRequest(rooms=["stairs"], force=True), table, config)) == [[3]]


def test_settings_from_defaults_and_request(config, table):
    [run] = plan(JobRequest(rooms=["hall"], mode="mop", passes=2), table, config)
    [(_, s)] = run.items
    assert (s.mode, s.suction, s.water, s.passes) == ("mop", 2, 2, 2)


@pytest.mark.parametrize(("rooms", "match"), [([], "no rooms"), (["bathroom"], "bathroom")])
def test_bad_requests(config, table, rooms, match):
    with pytest.raises(PlanError, match=match):
        plan(JobRequest(rooms=rooms), table, config)


def test_bad_mode(config, table):
    with pytest.raises(PlanError, match="mode"):
        plan(JobRequest(rooms=["hall"], mode="turbo"), table, config)


def test_duplicates_dropped(config, table):
    assert ids(plan(JobRequest(rooms=["hall", "5", "Hall"]), table, config)) == [[5]]

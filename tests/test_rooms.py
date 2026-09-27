import pytest

from opentapovac.rooms import RoomError, RoomTable
from tests.conftest import make_map


@pytest.fixture
def table(config):
    return RoomTable.from_map(make_map(), config.rooms)


def test_names_from_map(table):
    assert [(r.id, r.name) for r in table] == [(1, "kjøkken"), (6, None), (3, "stairs")]


@pytest.mark.parametrize(
    ("token", "rid"), [("kjøkken", 1), ("KJØKKEN", 1), ("Kitchen", 1), ("6", 6), ("outer hall", 6), ("stairs", 3)]
)
def test_resolve(table, token, rid):
    assert table.resolve(token).id == rid


def test_unknown(table):
    with pytest.raises(RoomError, match="bathroom"):
        table.resolve("bathroom")


def test_forbidden(table):
    assert table.resolve("stairs").forbidden
    assert not table.resolve("kitchen").forbidden


def test_label(table):
    assert table.resolve("6").label == "outer hall"
    assert table.resolve("1").label == "kjøkken"


def test_completions(table):
    assert set(table.completions()) == {"kjøkken", "kitchen", "6", "outer hall", "stairs"}


def test_cache_roundtrip(table, config, tmp_path):
    path = tmp_path / "rooms.json"
    table.save(path)
    again = RoomTable.load(path, config.rooms)
    assert [(r.id, r.name, r.aliases) for r in again] == [(r.id, r.name, r.aliases) for r in table]


def test_load_missing_cache(config, tmp_path):
    assert len(RoomTable.load(tmp_path / "nope.json", config.rooms)) == 0


def test_home_route_per_room(config):
    rooms = {**config.rooms, 6: {"aliases": ["outer hall"], "home_route": ["kitchen"]}}
    table = RoomTable.from_map(make_map(), rooms)
    assert table.resolve("outer hall").home_route == ["kitchen"]
    assert table.resolve("kitchen").home_route is None  # the default route

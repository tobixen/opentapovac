from opentapovac.tracks import CleanRecords, Track, describe_record
from tests.conftest import FakeRobot, record

HEADER = (1, 0)


async def test_track_fetches_only_new_points(tmp_path):
    robot = FakeRobot()
    robot.path = (7, [HEADER, (100, 100), (104, 100)])
    t = Track(tmp_path / "t.json")
    assert await t.poll(robot) == 2
    robot.path = (7, [*robot.path[1], (108, 100), (112, 100)])
    assert await t.poll(robot) == 2
    assert await t.poll(robot) == 0
    assert robot.path_calls == [0, 3, 5]
    assert t.segments == [{"path_id": 7, "n": 5, "points": [[100, 100], [104, 100], [108, 100], [112, 100]]}]


async def test_track_new_segment_when_path_id_changes(tmp_path):
    robot = FakeRobot()
    robot.path = (7, [HEADER, (100, 100), (104, 100)])
    t = Track(tmp_path / "t.json")
    await t.poll(robot)
    robot.path = (8, [HEADER, (500, 500)])
    assert await t.poll(robot) == 1
    assert [s["points"] for s in t.segments] == [[[100, 100], [104, 100]], [[500, 500]]]


async def test_track_new_segment_when_cleared(tmp_path):
    robot = FakeRobot()
    robot.path = (7, [HEADER, (100, 100), (104, 100), (108, 100)])
    t = Track(tmp_path / "t.json")
    await t.poll(robot)
    robot.path = (7, [HEADER, (300, 300)])
    assert await t.poll(robot) == 1
    assert [s["points"] for s in t.segments] == [[[100, 100], [104, 100], [108, 100]], [[300, 300]]]


async def test_track_saved_and_loaded(tmp_path):
    robot = FakeRobot()
    robot.path = (7, [HEADER, (100, 100), (104, 100)])
    t = Track(tmp_path / "tracks" / "t.json")
    await t.poll(robot)
    assert Track.load(tmp_path / "tracks" / "t.json").segments == t.segments
    assert t.points() == [[(100, 100), (104, 100)]]


async def test_clean_records(tmp_path):
    robot = FakeRobot()
    robot.records = [record(100), record(50)]
    cr = CleanRecords(tmp_path / "clean-records.jsonl")
    assert [r["timestamp"] for r in await cr.fetch(robot)] == [50, 100]
    assert await cr.fetch(robot) == []
    robot.records.append(record(200))
    again = CleanRecords(tmp_path / "clean-records.jsonl")  # remembers across restarts
    assert [r["timestamp"] for r in await again.fetch(robot)] == [200]
    assert len((tmp_path / "clean-records.jsonl").read_text().splitlines()) == 3


def test_describe_record():
    assert describe_record(record(0, 18, 11)) == "18 min, 11 m², 1 mop wash"
    assert describe_record(record(0, 15, 7, error=3, wash_times=2)) == "15 min, 7 m², 2 mop washes, error 3 (stuck)"

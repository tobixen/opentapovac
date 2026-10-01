import os

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
    assert t.segments[0]["points"] == [[100, 100], [104, 100], [108, 100], [112, 100]]
    assert t.segments[0]["n"] == 5


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


async def test_track_marks_when_and_what(tmp_path):
    robot = FakeRobot()
    robot.path = (7, [HEADER, (100, 100), (104, 100)])
    t = Track(tmp_path / "t.json")
    asked = []

    def cleaning_with(kind):
        async def ask():
            asked.append(kind)
            return kind

        return ask

    await t.poll(robot, cleaning_with("vac"), now=1000)
    robot.path = (7, [*robot.path[1], (108, 100)])
    await t.poll(robot, cleaning_with("mop"), now=1060)
    await t.poll(robot, cleaning_with("mop"), now=1120)  # nothing new: no mark, and nothing asked
    assert t.segments[0]["marks"] == [[0, 1000, "vac"], [2, 1060, "mop"]]
    assert asked == ["vac", "mop"]
    assert Track.load(tmp_path / "t.json").segments == t.segments


def test_track_lines_by_age_and_kind():
    # (109, 100) is a "moving between areas" point, the others cleaning ones
    pts = [[100, 100], [104, 100], [109, 100], [112, 100], [116, 100]]
    t = Track(
        None, [{"path_id": 7, "n": 6, "points": pts, "marks": [[0, 1000, "vac"], [2, 2000, "vac"], [3, 3000, "mop"]]}]
    )
    v, v2, m, p, p2 = (100, 100, "vac"), (104, 100, "vac"), (109, 100, "move"), (112, 100, "mop"), (116, 100, "mop")
    assert t.lines() == [[v, v2, m, p, p2]]
    assert t.lines(since=1500) == [[v2, m, p, p2]]  # the line from the last old point on
    assert t.lines(show={"vac", "mop"}) == [[v, v2], [m, p, p2]]
    assert t.lines(show={"move"}) == [[v2, m]]
    assert t.lines(since=5000) == []


def test_track_lines_of_an_old_file(tmp_path):
    """Tracks saved before the marks: the file's time, cleaning of unknown kind."""
    f = tmp_path / "t.json"
    f.write_text('{"segments": [{"path_id": 7, "n": 3, "points": [[100, 100], [104, 100]]}]}')
    os.utime(f, (5000, 5000))
    t = Track.load(f)
    assert t.lines(show={"mop"}) == [[(100, 100, None), (104, 100, None)]]
    assert t.lines(since=6000) == []
    assert t.lines(show={"move"}) == []


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


def test_track_lines_break_at_the_sub_path_marker():
    """(1, 0) starts a new sub-path, anywhere in the list (2026-09-28: after a resume); no line through it."""
    pts = [[4129, 3028], [1, 0], [4108, 2960], [4084, 2912], [1, 0], [4456, 2512], [4464, 2468]]
    t = Track(None, [{"path_id": 7, "n": 8, "points": pts, "marks": [[0, 1000, "vac"]]}])
    assert t.lines() == [
        [(4108, 2960, "vac"), (4084, 2912, "vac")],
        [(4456, 2512, "vac"), (4464, 2468, "vac")],
    ]


def xy(lines):
    return [[p[:2] for p in line] for line in lines]


def test_track_lines_drop_the_points_before_the_robot_finds_itself():
    """After a sub-path marker the robot may count from (0, 0) until it knows where it is,
    then jump to its place (2026-09-26: 4-5 m, drawn through walls and outside the house)."""
    pts = [[640, 61], [664, 13], [1, 0], [-35, -36], [-71, -68], [4272, 2553], [4292, 2505]]
    t = Track(None, [{"path_id": 7, "n": 8, "points": pts, "marks": [[0, 1000, "vac"]]}])
    assert xy(t.lines()) == [[(640, 61), (664, 13)], [(4272, 2553), (4292, 2505)]]


def test_track_lines_near_the_origin_kept_without_a_jump():
    """A dock at (0, 0): no jump follows, so the points are real."""
    pts = [[1, 0], [-35, -36], [-71, -68], [-111, -104]]
    t = Track(None, [{"path_id": 7, "n": 5, "points": pts, "marks": [[0, 1000, "vac"]]}])
    assert xy(t.lines()) == [[(-35, -36), (-71, -68), (-111, -104)]]


def test_track_lines_break_at_a_jump():
    pts = [[4108, 2960], [4084, 2912], [8452, 452], [8460, 400]]
    t = Track(None, [{"path_id": 7, "n": 5, "points": pts, "marks": [[0, 1000, "vac"]]}])
    assert xy(t.lines()) == [[(4108, 2960), (4084, 2912)], [(8452, 452), (8460, 400)]]

import base64
import io
import struct

import lz4.block
from PIL import Image

from opentapovac.mapimg import ROBOT, load_dump, render
from tests.conftest import make_map


def test_render_size_and_rooms():
    img = render(make_map(), scale=4)
    assert img.size == (80, 40)


def test_docked_robot_not_drawn_at_origin():
    img = render(make_map(), scale=4)
    px = img.load()
    assert all(px[x, y] != ROBOT for x in range(0, 8) for y in range(32, 40))


def test_path_drawn():
    buf = struct.pack(">hh", 1, 0) + b"".join(struct.pack(">hh", x, y) for x, y in [(100, 100), (800, 100), (800, 400)])
    path = {"pos_len": len(buf), "pos_array": base64.b64encode(lz4.block.compress(buf, store_size=False)).decode()}
    plain = render(make_map()).tobytes()
    assert render(make_map(), path).tobytes() != plain


def test_load_dump_skips_warnings(tmp_path):
    f = tmp_path / "dump.json"
    f.write_text('WARNING: foo\nWARNING: bar\n{"getMapData": {"width": 1}}')
    assert load_dump(f) == {"getMapData": {"width": 1}}


def test_png_roundtrip():
    from opentapovac.mapimg import png_bytes

    assert Image.open(io.BytesIO(png_bytes(make_map()))).format == "PNG"


def test_rooms_near():
    from opentapovac.mapimg import rooms_near

    d = make_map()  # room 1 left of x=500 mm, room 6 right of it, 50 mm pixels
    assert rooms_near(d, (200, 200)) == {1}
    assert rooms_near(d, (750, 250)) == {6}
    assert rooms_near(d, (480, 250)) == {1, 6}
    assert rooms_near(d, (5000, 5000)) == set()


def test_track_points_header_only_from_the_start():
    from opentapovac.mapimg import track_points
    from tests.conftest import path_reply

    raw = [(1, 0), (100, 100), (104, 100), (108, 100)]
    assert track_points(path_reply(7, raw)) == [(100, 100), (104, 100), (108, 100)]
    assert track_points(path_reply(7, raw, 2)) == [(104, 100), (108, 100)]


def test_render_with_saved_tracks():
    plain = render(make_map()).tobytes()
    assert render(make_map(), tracks=[[(100, 100), (800, 100)], [(800, 400), (900, 400)]]).tobytes() != plain


def test_render_mopping_in_its_own_colour():
    vac = render(make_map(), tracks=[[(100, 100, "vac"), (800, 100, "vac")]]).tobytes()
    assert render(make_map(), tracks=[[(100, 100, "mop"), (800, 100, "mop")]]).tobytes() != vac
    moving = [[(101, 100, "move"), (801, 100, "move")]]
    assert (
        render(make_map(), tracks=moving).tobytes() == render(make_map(), tracks=[[(101, 100), (801, 100)]]).tobytes()
    )


def test_render_breaks_at_the_sub_path_marker():
    two = render(make_map(), tracks=[[(100, 100), (800, 100)], [(800, 400), (900, 400)]]).tobytes()
    marked = [[(100, 100), (800, 100), (1, 0), (800, 400), (900, 400)]]
    assert render(make_map(), tracks=marked).tobytes() == two


def test_render_breaks_at_a_jump_and_drops_the_lost_points():
    two = render(make_map(60), tracks=[[(100, 100), (200, 100)], [(1800, 300), (1900, 300)]]).tobytes()
    jump = [[(100, 100), (200, 100), (1800, 300), (1900, 300)]]
    assert render(make_map(60), tracks=jump).tobytes() == two
    lost = [[(100, 100), (200, 100), (1, 0), (20, 20), (60, 60), (1800, 300), (1900, 300)]]
    assert render(make_map(60), tracks=lost).tobytes() == two


def test_room_spot_is_on_the_room():
    from opentapovac.mapimg import room_spot, rooms_near

    m = make_map()  # room 1 the left half, room 6 the right half, 50 mm pixels
    for rid in (1, 6):
        assert rooms_near(m, room_spot(m, rid), 10) == {rid}
    assert room_spot(m, 99) is None


def test_is_floor():
    import base64

    import lz4.block

    from opentapovac.mapimg import is_floor, pixels

    m = make_map()  # 1000 × 500 mm, a no-go zone over 0–100 mm
    assert is_floor(m, (300, 200))
    assert not is_floor(m, (50, 50))  # the no-go zone
    assert not is_floor(m, (-100, 0))  # off the map
    assert not is_floor(m, (4100, 3000))
    raw = bytearray(pixels(m))
    raw[4 * 20 + 6] = 0  # a wall at column 6, row 4: (300, 200)
    m["map_data"] = base64.b64encode(lz4.block.compress(bytes(raw), store_size=False)).decode()
    assert not is_floor(m, (300, 200))

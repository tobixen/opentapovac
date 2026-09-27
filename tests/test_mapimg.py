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

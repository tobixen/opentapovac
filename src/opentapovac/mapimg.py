"""Render `getMapData` (+ `getPathData`) to an image: rooms coloured by id, dock/robot/track marked.

Map y grows upwards, image rows grow downwards, so rows are flipped.
"""

from __future__ import annotations

import base64
import colorsys
import io
import json
import math
import struct
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import lz4.block
from PIL import Image, ImageDraw, ImageFont

WALL, UNKNOWN, FLOOR = (40, 40, 40), (255, 255, 255), (200, 200, 200)
DOCK, ROBOT, GOTO, ZONE = (0, 150, 0), (200, 0, 0), (0, 0, 200), (220, 0, 0)
#: track point type -> colour; app oe1/a.java b()/h()
TRACK = {0: (0, 90, 255), 5: (0, 90, 255), 1: (0, 160, 0), 3: (255, 140, 0), 4: (255, 140, 0)}
TRACK_OTHER = (200, 0, 200)
#: cleaning points recorded while mopping (tracks.Track.lines)
TRACK_MOP = (0, 200, 230)


def _font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    # Pillow's own fonts have no ø; DejaVu is on most Linux boxes
    try:
        return ImageFont.truetype("DejaVuSans.ttf", 12)
    except OSError:
        return ImageFont.load_default(size=12)


def load_dump(path: str | Path) -> dict[str, Any]:
    """A saved reply; raw `kasa --json` output starts with WARNING lines, so skip to the first `{`."""
    s = Path(path).read_text()
    return json.loads(s[s.find("{") :])


def room_names(map_data: dict[str, Any]) -> dict[int, str]:
    names = {}
    for a in map_data.get("area_list", []):
        if a.get("type") == "room":
            n = a.get("name")
            names[a["id"]] = base64.b64decode(n).decode() if n else f"room {a['id']}"
    return names


#: entries at the start of the robot's list that are not track points; seen: 377,-8 then 1,0, or 1,0 alone
TRACK_HEADER = {(377, -8), (1, 0)}
#: starts a sub-path, also mid-list (2026-09-28: after a resume); no line is drawn through it
TRACK_BREAK = (1, 0)
#: more than this between two points (mm) is a jump, not a move; seen between moves: up to ~500
MAX_STEP = 1000
#: a sub-path starting this close to (0, 0) may be counted from there, before the robot finds itself
LOST_RADIUS = 100


def joined(a: Sequence[Any], b: Sequence[Any]) -> bool:
    """Whether a line is drawn from point `a` to `b`: no sub-path marker, no jump."""
    return TRACK_BREAK not in (tuple(a[:2]), tuple(b[:2])) and math.dist(a[:2], b[:2]) <= MAX_STEP


def drop_lost(pts: Sequence[Any]) -> list[Any]:
    """`pts`, with the points recorded before the robot found itself on the map made sub-path markers.

    After a marker the robot may count from (0, 0) until it knows where it
    is, then jump to its place (2026-09-26: twice in a run, 4-5 m, through
    walls and outside the house).  A sub-path starting near (0, 0) is lost
    up to its first jump; without a jump it is kept (a dock at (0, 0)).
    """
    out = list(pts)
    start = 0
    for i in range(len(pts) + 1):
        if i < len(pts) and tuple(pts[i][:2]) != TRACK_BREAK:
            continue
        if start < i and math.hypot(*pts[start][:2]) <= LOST_RADIUS:
            for j in range(start + 1, i):
                if math.dist(pts[j - 1][:2], pts[j][:2]) > MAX_STEP:
                    out[start:j] = [TRACK_BREAK] * (j - start)
                    break
        start = i + 1
    return out


def position(pts: Sequence[Any]) -> tuple[int, int] | None:
    """Where the robot is by its track: the last point, or None while its sub-path may still be
    counted from (0, 0) (`drop_lost`: near it, and no jump yet) or right at a sub-path marker."""
    i = len(pts)
    while i and tuple(pts[i - 1][:2]) != TRACK_BREAK:
        i -= 1
    sub = pts[i:]
    if not sub:
        return None
    if math.hypot(*sub[0][:2]) <= LOST_RADIUS and all(joined(a, b) for a, b in zip(sub, sub[1:], strict=False)):
        return None
    return tuple(sub[-1][:2])


def track_points(path_data: dict[str, Any]) -> list[tuple[int, int]]:
    """The points of a `getPathData` reply; one from `start_pos` > 0 has no header."""
    buf = lz4.block.decompress(base64.b64decode(path_data["pos_array"]), uncompressed_size=path_data["pos_len"])
    pts = [struct.unpack_from(">hh", buf, i) for i in range(0, len(buf) - 3, 4)]
    if not path_data.get("start_pos"):
        while pts and pts[0] in TRACK_HEADER:
            pts.pop(0)
    return pts


def pixels(map_data: dict[str, Any]) -> bytes:
    """The pixel grid, row-major from `real_origin_coor`; row r is at y = origin_y + r × resolution."""
    d = map_data
    return lz4.block.decompress(base64.b64decode(d["map_data"]), uncompressed_size=d["pix_len"])


def rooms_near(map_data: dict[str, Any], xy: tuple[float, float], radius: float = 150) -> set[int]:
    """Ids of the rooms with pixels within `radius` mm of the map point `xy` (mm)."""
    d = map_data
    w, h, res = d["width"], d["height"], d["resolution"]
    ids = {a["id"] for a in d.get("area_list", []) if a.get("type") == "room"}
    raw = pixels(d)
    cx, cy = (xy[0] - d["real_origin_coor"][0]) / res, (xy[1] - d["real_origin_coor"][1]) / res
    r = radius / res
    found = set()
    for y in range(max(0, int(cy - r)), min(h, int(cy + r) + 1)):
        for x in range(max(0, int(cx - r)), min(w, int(cx + r) + 1)):
            if (x - cx) ** 2 + (y - cy) ** 2 <= r * r and raw[y * w + x] in ids:
                found.add(raw[y * w + x])
    return found


def room_spot(map_data: dict[str, Any], room_id: int) -> tuple[int, int] | None:
    """A point (mm) on the floor of a room: its pixel nearest the room's centroid; None without pixels."""
    d = map_data
    w, res = d["width"], d["resolution"]
    cells = [(i % w, i // w) for i, v in enumerate(pixels(d)) if v == room_id]
    if not cells:
        return None
    cx, cy = sum(c for c, _ in cells) / len(cells), sum(r for _, r in cells) / len(cells)
    col, row = min(cells, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)
    ox, oy = d["real_origin_coor"][:2]
    return int(ox + col * res), int(oy + row * res)


def _inside(xy: tuple[float, float], poly: list[list[float]]) -> bool:
    x, y = xy
    inside = False
    for (x1, y1), (x2, y2) in zip(poly, poly[1:] + poly[:1], strict=True):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def is_floor(map_data: dict[str, Any], xy: tuple[float, float]) -> bool:
    """The map point `xy` (mm) is on the floor (a room or unassigned floor), outside the no-go zones."""
    d = map_data
    w, h, res = d["width"], d["height"], d["resolution"]
    col, row = round((xy[0] - d["real_origin_coor"][0]) / res), round((xy[1] - d["real_origin_coor"][1]) / res)
    if not (0 <= col < w and 0 <= row < h) or pixels(d)[row * w + col] in (0, 127):
        return False
    zones = (a.get("vertexs", []) for a in d.get("area_list", []) if a.get("type") == "forbid")
    return not any(len(z) >= 3 and _inside(xy, [v[:2] for v in z]) for z in zones)


#: pixels per map pixel in the rendered map
SCALE = 4


def render(
    map_data: dict[str, Any],
    path_data: dict[str, Any] | None = None,
    names: dict[int, str] | None = None,
    scale: int = SCALE,
    tracks: list[list[tuple[int, int]]] | list[list[tuple[int, int, str | None]]] | None = None,
) -> Image.Image:
    """`tracks`: saved track segments, drawn like the one in `path_data`; (x, y, "mop") marks mopping."""
    d = map_data
    w, h, res = d["width"], d["height"], d["resolution"]
    raw = pixels(d)
    names = {**room_names(d), **(names or {})}

    img = Image.new("RGB", (w, h))
    px = img.load()
    for i, v in enumerate(raw):
        if v == 0:
            c = WALL
        elif v == 127:
            c = UNKNOWN
        elif v == 255:
            c = FLOOR
        else:
            r, g, b = colorsys.hsv_to_rgb((v * 0.13) % 1, 0.45, 0.95)
            c = (int(r * 255), int(g * 255), int(b * 255))
        px[i % w, h - 1 - i // w] = c
    img = img.resize((w * scale, h * scale), Image.NEAREST)
    dr = ImageDraw.Draw(img)
    dr.font = _font()
    ox, oy = d["real_origin_coor"][:2]

    def p(xy):  # real mm -> scaled pixel
        return ((xy[0] - ox) / res * scale, (h - 1 - (xy[1] - oy) / res) * scale)

    # room labels at the centroid of their pixels
    acc: dict[int, list[int]] = {}
    for i, v in enumerate(raw):
        if v in names:
            s = acc.setdefault(v, [0, 0, 0])
            s[0] += i % w
            s[1] += i // w
            s[2] += 1
    for rid, (sx, sy, n) in acc.items():
        dr.text((sx / n * scale - 12, (h - 1 - sy / n) * scale - 5), f"{rid}:{names[rid]}", fill=(0, 0, 0))
    for a in d.get("area_list", []):  # no-go zones and virtual walls
        pts = [p(v) for v in a.get("vertexs", [])]
        if a.get("type") in ("forbid", "forbid_mop") and len(pts) >= 3:
            dr.polygon(pts, outline=ZONE, width=3)
            dr.text((pts[0][0] + 4, pts[0][1] + 4), f"{a['id']}:{a['type']}", fill=ZONE)
        elif a.get("type") == "virtual_wall" and len(pts) == 2:
            dr.line(pts, fill=ZONE, width=4)
    segments: list[Any] = list(tracks or [])
    if path_data:
        segments.append(track_points(path_data))
    for pts in map(drop_lost, segments):
        for a, b in zip(pts, pts[1:], strict=False):
            if not joined(a, b):
                continue
            t = (b[0] % 4 << 2) + b[1] % 4
            col = TRACK_MOP if b[2:] == ("mop",) and t in (0, 5) else TRACK.get(t, TRACK_OTHER)
            dr.line((p(a), p(b)), fill=col, width=2)
    for key, col, lab in (
        ("real_charge_coor", DOCK, "dock"),
        ("real_vac_coor", ROBOT, "robot"),
        ("goto_point", GOTO, "goto"),
    ):
        # no goto_point while idle; the robot reads [0, 0, 0] while docked
        if not any(d.get(key, [])[:2]):
            continue
        x, y = p(d[key])
        dr.ellipse((x - 5, y - 5, x + 5, y + 5), fill=col)
        dr.text((x + 7, y - 5), lab, fill=col)
    return img


def png_bytes(map_data: dict[str, Any], path_data: dict[str, Any] | None = None, **kw: Any) -> bytes:
    buf = io.BytesIO()
    render(map_data, path_data, **kw).save(buf, format="PNG")
    return buf.getvalue()

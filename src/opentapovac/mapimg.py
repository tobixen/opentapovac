"""Render `getMapData` (+ `getPathData`) to an image: rooms coloured by id, dock/robot/track marked.

Map y grows upwards, image rows grow downwards, so rows are flipped.
"""

from __future__ import annotations

import base64
import colorsys
import io
import json
import struct
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


def render(
    map_data: dict[str, Any],
    path_data: dict[str, Any] | None = None,
    names: dict[int, str] | None = None,
    scale: int = 4,
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
    for pts in segments:
        for a, b in zip(pts, pts[1:], strict=False):
            if TRACK_BREAK in (tuple(a[:2]), tuple(b[:2])):
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

"""Render a Tapo getMapData dump to PNG: rooms coloured by id, dock/robot marked.

Usage: tapo-render-map.py MAP.json OUT.png [--path PATH.json] [id=name ...]
PATH.json is a getPathData dump; the track is drawn coloured by point type.

Map y grows upwards, image rows grow downwards, so rows are flipped.
"""
import base64, colorsys, json, struct, sys
import lz4.block
from PIL import Image, ImageDraw

def load(fn):  # raw `kasa --json` output starts with WARNING lines
    s = open(fn).read()
    return json.loads(s[s.find("{"):])

args = sys.argv[1:]
path = None
if "--path" in args:
    i = args.index("--path")
    if i + 1 >= len(args): sys.exit(__doc__)
    path = args[i + 1]; del args[i:i + 2]
if len(args) < 2: sys.exit(__doc__)
d = load(args[0])["getMapData"]
w, h, res = d["width"], d["height"], d["resolution"]
raw = lz4.block.decompress(base64.b64decode(d["map_data"]), uncompressed_size=d["pix_len"])
names = {}
for a in d["area_list"]:
    if a.get("type") == "room":
        n = a.get("name")
        names[a["id"]] = base64.b64decode(n).decode() if n else f"room {a['id']}"
names.update({int(k): v for k, v in (a.split("=", 1) for a in args[2:])})  # id=name overrides

img = Image.new("RGB", (w, h))
px = img.load()
for i, v in enumerate(raw):
    x, y = i % w, i // w
    if v == 0: c = (40, 40, 40)
    elif v == 127: c = (255, 255, 255)
    elif v == 255: c = (200, 200, 200)
    else:
        r, g, b = colorsys.hsv_to_rgb((v * 0.13) % 1, 0.45, 0.95)
        c = (int(r * 255), int(g * 255), int(b * 255))
    px[x, h - 1 - y] = c
img = img.resize((w * 4, h * 4), Image.NEAREST)
dr = ImageDraw.Draw(img)
ox, oy = d["real_origin_coor"][:2]
def p(xy):  # real mm -> scaled pixel
    return ((xy[0] - ox) / res * 4, (h - 1 - (xy[1] - oy) / res) * 4)
# room labels at the centroid of their pixels
acc = {}
for i, v in enumerate(raw):
    if v in names:
        s = acc.setdefault(v, [0, 0, 0]); s[0] += i % w; s[1] += i // w; s[2] += 1
for rid, (sx, sy, n) in acc.items():
    dr.text((sx / n * 4 - 12, (h - 1 - sy / n) * 4 - 5), f"{rid}:{names[rid]}", fill=(0, 0, 0))
for a in d["area_list"]:  # no-go zones and virtual walls
    pts = [p(v) for v in a.get("vertexs", [])]
    if a.get("type") in ("forbid", "forbid_mop") and len(pts) >= 3:
        dr.polygon(pts, outline=(220, 0, 0), width=3)
        dr.text((pts[0][0] + 4, pts[0][1] + 4), f"{a['id']}:{a['type']}", fill=(220, 0, 0))
    elif a.get("type") == "virtual_wall" and len(pts) == 2:
        dr.line(pts, fill=(220, 0, 0), width=4)
if path:  # app: oe1/a.java b()/h(); low 2 bits of x and y are the point type
    pd = load(path)["getPathData"]
    buf = lz4.block.decompress(base64.b64decode(pd["pos_array"]), uncompressed_size=pd["pos_len"])
    # the first 8 bytes are not track points (always 377,-8 / 1,0 here)
    pts = [struct.unpack_from(">hh", buf, i) for i in range(8, len(buf) - 3, 4)]
    kinds = {0: (0, 90, 255), 5: (0, 90, 255), 1: (0, 160, 0), 3: (255, 140, 0), 4: (255, 140, 0)}
    for a, b in zip(pts, pts[1:]):
        t = (b[0] % 4 << 2) + b[1] % 4
        dr.line((p(a), p(b)), fill=kinds.get(t, (200, 0, 200)), width=2)
for key, col, lab in (("real_charge_coor", (0, 150, 0), "dock"),
                      ("real_vac_coor", (200, 0, 0), "robot"),
                      ("goto_point", (0, 0, 200), "goto")):
    if not any(d.get(key, [])[:2]): continue  # no goto_point while idle; robot at [0, 0, 0] while docked
    x, y = p(d[key]); dr.ellipse((x - 5, y - 5, x + 5, y + 5), fill=col); dr.text((x + 7, y - 5), lab, fill=col)
img.save(args[1])
print("pixel values:", sorted(set(raw)))

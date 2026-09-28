"""Move the living room / hall border 10 cm into the hall: merge 2+5, split at x=3701, restore names.

    python docs/scripts/move-border-living-room-hall.py MAP-BACKUP.json [--send]

MAP-BACKUP.json: a `getMapData` reply saved just before (the map must not have changed
since); it also supplies the names and floor types to put back.  Dry run unless --send.
The robot must be idle.  Stops, without renaming anything, if the ids come out swapped:
protocol.md, "setAutoAreaData".  Written for this house's map (2026-09-28, field-notes.md).
"""

import asyncio
import json
import sys
from pathlib import Path

from opentapovac import mapimg
from opentapovac.config import load_config, load_credentials
from opentapovac.robot import KasaRobot

LIVING, HALL = 2, 5
X = 3701  # new border, on a pixel edge: origin -1199 + 98 × 50
P1, P2 = [X, -100], [X, 1100]  # across the hall corridor, rows 70–93, overshooting its walls
LIVING_PX, HALL_PX = (94, 80), (99, 80)  # (col, row): surely living room, surely hall
BACKUP = Path(sys.argv[1])
SEND = "--send" in sys.argv


def at(md, cr):
    return mapimg.pixels(md)[cr[1] * md["width"] + cr[0]]


def counts(md):
    raw = mapimg.pixels(md)
    return {LIVING: raw.count(LIVING), HALL: raw.count(HALL)}


async def main():
    old = json.loads(BACKUP.read_text())
    c = load_config()
    user, password = load_credentials(c)
    robot = KasaRobot(c.host, user, password, c.port, c.timeout)
    try:
        vac = await robot.query("getVacStatus")
        if vac.get("status") not in (0, 5, 6) or vac.get("err_status"):
            sys.exit(f"robot not idle: {vac}")
        map_id = old["map_id"]

        async def read():
            return await robot.query("getMapData", {"map_id": map_id, "type": 0})

        md = await read()
        if mapimg.pixels(md) != mapimg.pixels(old):
            sys.exit("the map changed since the backup; take a new one")
        print("before", counts(md), "living px", at(md, LIVING_PX), "hall px", at(md, HALL_PX))
        if not SEND:
            print("dry run: would merge", LIVING, HALL, "then split at", P1, P2)
            return
        print(
            "merge ->",
            await robot.query(
                "setAutoAreaData",
                {
                    "map_id": map_id,
                    "operation": "merge",
                    "extra": {"pixel1": LIVING, "pixel2": HALL},
                    "auto_area_id": 1,
                },
            ),
        )
        md = await read()
        merged = at(md, LIVING_PX)
        print("merged into", merged, counts(md))
        if merged != at(md, HALL_PX):
            sys.exit("merge did not give one room; stopping")
        print(
            "split ->",
            await robot.query(
                "setAutoAreaData",
                {
                    "map_id": map_id,
                    "operation": "split",
                    "extra": {"pixel": merged, "p1": P1, "p2": P2},
                    "auto_area_id": 1,
                },
            ),
        )
        md = await read()
        liv, hall = at(md, LIVING_PX), at(md, HALL_PX)
        print("after split: living side id", liv, "hall side id", hall, counts(md))
        BACKUP.with_name(BACKUP.stem + ".after-split.json").write_text(json.dumps(md))
        if (liv, hall) != (LIVING, HALL):
            sys.exit("ids came out swapped or odd: NOT renaming; fix by hand")
        meta = {a["id"]: a for a in old["area_list"] if a.get("type") == "room"}
        rooms = []
        for a in md["area_list"]:
            if a.get("type") != "room":
                continue
            src = meta.get(a["id"], {})
            rooms.append({**a, **{k: src[k] for k in ("name", "floor_material", "color", "label") if k in src}})
        print(
            "setAreaInfo ->",
            await robot.query("setAreaInfo", {"map_id": map_id, "type_list": ["room"], "area_list": rooms}),
        )
        md = await read()
        print(
            "names now",
            {a["id"]: mapimg.room_names(md).get(a["id"]) for a in md["area_list"] if a.get("type") == "room"},
        )
        print("final", counts(md))
    finally:
        await robot.close()


asyncio.run(main())

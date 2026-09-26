import asyncio
import base64
import struct
from pathlib import Path

import lz4.block
import pytest

from opentapovac.config import Config
from opentapovac.events import EventLog
from opentapovac.robot import Robot, RobotError

FIXTURES = Path(__file__).parent / "fixtures"
PAYLOADS = Path(__file__).parent.parent / "docs" / "payloads"


def b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def make_map(width=20, height=10, rooms=None, **extra) -> dict:
    """A small synthetic getMapData reply: left half room 1, right half room 6."""
    raw = bytearray()
    for _y in range(height):
        raw += bytes([1] * (width // 2) + [6] * (width - width // 2))
    rooms = (
        rooms
        if rooms is not None
        else [
            {"id": 1, "type": "room", "name": b64("kjøkken")},
            {"id": 6, "type": "room"},
            {"id": 3, "type": "room", "name": b64("stairs")},
            {"id": 301, "type": "forbid", "vertexs": [[0, 0], [100, 0], [100, 100], [0, 100]]},
        ]
    )
    d = {
        "map_id": 42,
        "width": width,
        "height": height,
        "resolution": 50,
        "pix_len": len(raw),
        "map_data": base64.b64encode(lz4.block.compress(bytes(raw), store_size=False)).decode(),
        "area_list": rooms,
        "real_origin_coor": [0, 0, 0],
        "real_charge_coor": [100, 100, 0],
        "real_vac_coor": [0, 0, 0],
    }
    d.update(extra)
    return d


def path_reply(path_id, raw, start=0) -> dict:
    """A getPathData reply over the robot-side point list `raw` (header entry included), from `start`."""
    buf = b"".join(struct.pack(">hh", *p) for p in raw[start:])
    packed = lz4.block.compress(buf, store_size=False)
    return {
        "path_id": path_id,
        "start_pos": start,
        "point_counts": len(raw) - start,
        "total_points": len(raw),
        "pos_len": len(buf),
        "pos_lz4len": len(packed),
        "pos_array": base64.b64encode(packed).decode(),
    }


def record(ts, minutes=18, area=11, **kw) -> dict:
    return {"timestamp": ts, "clean_time": minutes, "clean_area": area, "error": 0, "wash_times": 1, **kw}


def vac(status, *errors):
    return {"status": status, "err_status": list(errors)}


class FakeRobot(Robot):
    """Scripted robot: getVacStatus replies are taken from `statuses` in turn."""

    def __init__(self, statuses=(16,), fail=None, map_info=None, map_data=None):
        super().__init__()
        self.statuses = [s if isinstance(s, dict) else vac(s) for s in statuses]
        self.sent = []
        self.fail = fail or {}
        self.map_info_reply = map_info or {
            "current_map_id": 42,
            "auto_change_map": False,
            "map_list": [{"map_id": 42, "map_locked": 1}],
        }
        self.map_data_reply = map_data or make_map()
        #: (path_id, robot-side points incl. the header entry), or None: getPathData fails
        self.path = None
        self.path_calls = []
        self.records = []
        #: appended to `records` when a run is sent
        self.next_record = None
        self.clean_info_reply = {"clean_time": 5, "clean_area": 3, "clean_percent": 20}
        self.mop = False

    async def _raw(self, method, params):
        if method in self.fail:
            raise RobotError(self.fail[method])
        if method == "getVacStatus":
            return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        if method == "getCleanStatus":
            return {"is_relocating": False, "is_mapping": False}
        if method == "getBatteryInfo":
            return {"battery_percentage": 88}
        if method == "getBaseStatus":
            return {"clean_water": 0}
        if method == "getMapInfo":
            return self.map_info_reply
        if method == "getMapData":
            return self.map_data_reply
        if method == "getPathData":
            if self.path is None:
                raise RobotError("no path")
            self.path_calls.append(params["start_pos"])
            return path_reply(*self.path, params["start_pos"])
        if method == "getCleanInfo":
            return dict(self.clean_info_reply)
        if method == "getMopState":
            return {"mop_state": self.mop}
        if method == "getCleanRecords":
            return {"record_list": list(self.records)}
        if method == "runCleanTask" and params.get("clean_on") and self.next_record:
            self.records.append(self.next_record)
        self.sent.append((method, params))
        return None


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += s
        await asyncio.sleep(0)  # let other tasks run, like a real sleep


@pytest.fixture
def config(tmp_path):
    return Config.from_dict(
        {
            "rooms": {
                "kjøkken": {"aliases": ["kitchen"]},
                6: {"aliases": ["outer hall"]},
                "stairs": {"forbidden": True},
            },
            "presets": [{"label": "Kitchen + outer hall", "rooms": ["kjøkken", 6]}],
            "cache_dir": str(tmp_path / "cache"),
            "state_dir": str(tmp_path / "state"),
            "timezone": "Europe/Oslo",
        }
    )


@pytest.fixture
def events(config):
    return EventLog(config.events_file, config.timezone)

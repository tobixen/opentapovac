"""THE table from clean settings to `runCleanTask` payloads.

Checked against the payloads sent to a real robot, `docs/payloads/`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: mode name -> `clean_type` (app `RobotParamValue.CleanType`); 4 is "AI", not offered
MODES = {"vac_and_mop": 0, "mop": 1, "vac": 2, "vac_then_mop": 3}

MODE_LABELS = {"vac": "vacuum", "mop": "mop", "vac_and_mop": "vacuum and mop", "vac_then_mop": "vacuum then mop"}

#: what the app sends to stop a run: `RobotRunCleanTaskParams` defaults with `clean_on` false
STOP = {
    "clean_on": False,
    "support_continue": True,
    "start_type": 1,
    "clean_mode": 0,
    "force_clean": False,
    "is_custom": False,
    "clean_order": True,
    "dust_collection": True,
}

#: `setSwitchCharge`, from the app (`RobotGotoChargeStatus`); not yet sent to a robot
HOME = {"switch_charge": True}


@dataclass(frozen=True)
class Settings:
    mode: str = "vac_then_mop"
    suction: int = 2
    water: int = 2
    passes: int = 1

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown mode {self.mode!r}, expected one of {', '.join(MODES)}")


def area_item(room_id: int, s: Settings) -> dict[str, Any]:
    return {
        "id": room_id,
        "type": "room",
        "clean_number": s.passes,
        "suction": s.suction,
        "cistern": s.water,
        "clean_type": MODES[s.mode],
        "density": 1,
    }


def run_payload(rooms: list[tuple[int, Settings]]) -> dict[str, Any]:
    """One run over `rooms`, cleaned in list order, each with its own settings."""
    return {
        "clean_on": True,
        "support_continue": True,
        "start_type": 1,
        "clean_mode": 3,
        "force_clean": False,
        "is_custom": True,
        "clean_order": True,
        "dust_collection": True,
        "area_list": [area_item(rid, s) for rid, s in rooms],
    }

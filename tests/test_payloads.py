import json

import pytest

from opentapovac.payloads import HOME, MODES, STOP, Settings, run_payload
from tests.conftest import PAYLOADS


def load(name):
    return json.loads((PAYLOADS / f"runCleanTask-{name}.json").read_text())


@pytest.mark.parametrize(
    ("name", "rooms"),
    [
        ("hall-vac-then-mop", [(5, "vac_then_mop")]),
        ("bedroom1-vac-only", [(4, "vac")]),
        ("bedroom1-mop-only", [(4, "mop")]),
        ("bedroom2-vac-and-mop", [(7, "vac_and_mop")]),
        ("bedroom1-kitchen-vac-then-mop", [(4, "vac_then_mop"), (1, "vac_then_mop")]),
        ("bedroom1-kitchen-outerhall", [(4, "mop"), (1, "vac_then_mop"), (6, "vac_then_mop")]),
    ],
)
def test_matches_known_good(name, rooms):
    items = [(rid, Settings(mode=mode)) for rid, mode in rooms]
    assert run_payload(items) == load(name)


def test_settings_map_to_fields():
    item = run_payload([(5, Settings(mode="mop", suction=3, water=1, passes=2))])["area_list"][0]
    assert item["suction"] == 3
    assert item["cistern"] == 1
    assert item["clean_number"] == 2
    assert item["clean_type"] == MODES["mop"]


def test_unknown_mode():
    with pytest.raises(ValueError, match="mode"):
        Settings(mode="dance")


def test_stop_and_home():
    assert STOP["clean_on"] is False
    assert "area_list" not in STOP
    assert HOME == {"switch_charge": True}

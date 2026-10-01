import asyncio
import base64
import json
import time
from unittest import mock

import pytest
from aiohttp.test_utils import TestClient, TestServer, make_mocked_request

from opentapovac import __version__
from opentapovac.client import DaemonClient
from opentapovac.engine import Engine
from opentapovac.rooms import RoomTable
from opentapovac.web.server import _who, make_app
from tests.conftest import FakeClock, FakeRobot, make_map


@pytest.fixture
def make_client(config, events):
    async def make(statuses=(16, 1, 4, 16)):
        clock = FakeClock()
        robot = FakeRobot(statuses)
        engine = Engine(
            robot, config, events, clock=clock, sleep=clock.sleep, rooms=RoomTable.from_map(make_map(), config.rooms)
        )
        client = TestClient(TestServer(make_app(engine)))
        await client.start_server()
        return client, engine, robot

    return make


async def test_index(make_client):
    client, _, _ = await make_client()
    r = await client.get("/")
    assert r.status == 200
    text = await r.text()
    assert "OpenTapoVac" in text
    assert f'<span class="version">{__version__}</span>' in text
    assert (await client.get("/static/app.js")).status == 200
    await client.close()


async def test_rooms_and_presets(make_client):
    client, _, _ = await make_client()
    data = await (await client.get("/rooms")).json()
    assert {"id": 6, "label": "outer hall", "forbidden": False, "carry_in": False, "carry_out": False} in data["rooms"]
    assert data["presets"][0]["label"] == "Kitchen + outer hall"
    assert data["default_mode"] == "vac_then_mop"
    assert "vac" in data["modes"]
    await client.close()


async def test_job_lifecycle(make_client):
    client, engine, robot = await make_client()
    r = await client.post("/jobs", json={"rooms": ["kitchen"], "mode": "mop"})
    assert r.status == 201
    job = await r.json()
    await engine.wait()
    got = await (await client.get(f"/jobs/{job['id']}")).json()
    assert got["state"] == "done"
    assert robot.sent[0][1]["area_list"][0]["clean_type"] == 1
    assert (await client.get("/jobs/nope")).status == 404
    await client.close()


async def test_bad_request_and_busy(make_client):
    client, _, _ = await make_client(statuses=(16, 1))
    assert (await client.post("/jobs", json={"rooms": ["bathroom"]})).status == 400
    assert (await client.post("/jobs", json={"rooms": ["stairs"]})).status == 400
    assert (await client.post("/jobs", json={"rooms": ["kitchen"]})).status == 201
    r = await client.post("/jobs", json={"rooms": ["kitchen"]})
    assert r.status == 201
    assert (await r.json())["state"] == "queued"
    assert (await client.post("/stop", json={})).status == 200
    await client.close()


async def test_status_and_map(make_client):
    client, _, _ = await make_client()
    st = await (await client.get("/status")).json()
    assert st["status_text"] == "drying mop"
    assert (await client.get("/map.png")).content_type == "image/png"
    assert (await client.post("/map/refresh", json={})).status == 200
    assert (await client.get("/map.png?max_age=2.5&show=vac,move")).status == 200
    assert (await client.get("/map.png?max_age=0&show=")).status == 200
    for bad in ("soon", "nan", "inf", "-1"):
        assert (await client.get(f"/map.png?max_age={bad}")).status == 400
    await client.close()


async def test_home(make_client):
    client, _, robot = await make_client()
    assert (await client.post("/home", json={})).status == 200
    assert robot.sent[-1] == ("setSwitchCharge", {"switch_charge": True})
    assert 'id="home"' in await (await client.get("/")).text()
    await client.close()


async def test_pause_and_resume(make_client):
    client, _, robot = await make_client()
    assert (await client.post("/pause", json={})).status == 200
    assert (await client.post("/resume", json={})).status == 200
    assert robot.sent[-2:] == [("setRobotPause", {"pause": True}), ("setRobotPause", {"pause": False})]
    await client.close()


async def test_goto(make_client):
    client, _, robot = await make_client()
    assert (await client.post("/goto", json={"x": 300, "y": 200})).status == 200
    assert robot.sent[-1] == ("gotoPoint", {"switch": True, "point": [300, 200]})
    r = await client.post("/goto", json={"room": "outer hall"})
    assert r.status == 200
    assert (await r.json())["point"] == robot.sent[-1][1]["point"]
    bad_bodies = [{}, {"room": "nowhere"}, {"x": "a", "y": 1}, {"x": 1}, {"x": True, "y": 200}, {"x": 1e300, "y": 200}]
    bad_bodies += [["room"], {"x": 4100, "y": 3000}, {"x": 50, "y": 50}]  # a list; off the map; a no-go zone
    for bad in bad_bodies:
        assert (await client.post("/goto", json=bad)).status == 400, bad
    for raw in ("{", '{"x": Infinity, "y": 1}'):
        r = await client.post("/goto", data=raw, headers={"Content-Type": "application/json"})
        assert r.status == 400, raw
    geo = await (await client.get("/map.json")).json()
    assert set(geo) == {"origin", "resolution", "width", "height", "scale"}
    await client.close()


async def test_map_options_reach_the_engine(make_client, config):
    client, _, _ = await make_client()
    config.tracks_dir.mkdir(parents=True)
    seg = {"path_id": 7, "n": 3, "points": [[100, 100], [800, 100]], "marks": [[0, time.time() - 7200, "vac"]]}
    (config.tracks_dir / "t.json").write_text(json.dumps({"segments": [seg]}))
    plain = await (await client.get("/map.png?show=")).read()
    assert await (await client.get("/map.png?show=vac")).read() != plain
    assert await (await client.get("/map.png?show=mop,move")).read() == plain
    assert await (await client.get("/map.png?max_age=1&show=vac")).read() == plain
    await client.close()
    await client.close()


async def test_robot_error_is_502(make_client):
    client, _, robot = await make_client()
    robot.fail["getVacStatus"] = "timeout"
    r = await client.get("/status")
    assert r.status == 502
    assert "timeout" in (await r.json())["error"]
    await client.close()


async def test_events_stream(make_client, events):
    client, _, _ = await make_client()
    events.emit("info", "before")
    r = await client.get("/events?backlog=5")
    assert r.content_type == "text/event-stream"
    line = await r.content.readline()
    assert json.loads(line.decode().removeprefix("data: "))["msg"] == "before"
    events.emit("warn", "after")
    lines = [await r.content.readline() for _ in range(2)]
    assert json.loads(lines[1].decode().removeprefix("data: "))["msg"] == "after"
    r.close()
    await client.close()


async def test_daemon_client(make_client):
    client, engine, _ = await make_client()
    dc = DaemonClient(str(client.make_url("/")))
    try:
        assert await dc.ping()
        job = await dc.submit({"rooms": ["kitchen"]})
        await engine.wait()
        assert (await dc.job(job["id"]))["state"] == "done"
        assert (await dc.status())["status_text"]
        assert (await dc.rooms())["rooms"]
    finally:
        await dc.close()
        await client.close()


async def test_daemon_client_not_running():
    dc = DaemonClient("http://127.0.0.1:9")
    assert not await dc.ping()
    await dc.close()


async def test_answer(make_client, config):
    config.rooms[6] = {"aliases": ["outer hall"], "carry_in": True}
    client, engine, robot = await make_client(statuses=(16, 1))
    job = await (await client.post("/jobs", json={"rooms": ["outer hall"]})).json()
    for _ in range(1000):
        if engine.job.question:
            break
        await asyncio.sleep(0)
    got = await (await client.get(f"/jobs/{job['id']}")).json()
    assert got["state"] == "waiting"
    assert got["question"]["choices"] == ["done", "skip"]
    assert (await client.post(f"/jobs/{job['id']}/answer", json={"choice": "maybe"})).status == 400
    assert (await client.post("/jobs/nope/answer", json={"choice": "done"})).status == 404
    assert (await client.post(f"/jobs/{job['id']}/answer", json={"choice": "skip"})).status == 200
    robot.statuses = [{"status": 16, "err_status": []}]
    await engine.wait()
    assert engine.job.state == "done"
    await client.close()


async def test_rooms_carry_flags(make_client, config):
    config.rooms[6] = {"aliases": ["outer hall"], "carry_out": True}
    client, _, _ = await make_client()
    data = await (await client.get("/rooms")).json()
    [r6] = [r for r in data["rooms"] if r["id"] == 6]
    assert (r6["carry_in"], r6["carry_out"]) == (False, True)
    await client.close()


async def test_post_must_be_json(make_client):
    # review finding: a no-cors text/plain POST from any web page started jobs
    client, _, robot = await make_client()
    r = await client.post("/jobs", data='{"rooms": ["kitchen"]}', headers={"Content-Type": "text/plain"})
    assert r.status == 415
    assert (await client.post("/stop")).status == 415
    assert robot.sent == []
    await client.close()


async def test_cross_origin_post_refused(make_client):
    client, _, robot = await make_client()
    r = await client.post("/stop", json={}, headers={"Origin": "https://evil.example"})
    assert r.status == 403
    assert robot.sent == []
    own = f"http://{client.host}:{client.port}"
    assert (await client.post("/stop", json={}, headers={"Origin": own})).status == 200
    await client.close()


async def test_unknown_host_refused(make_client, config):
    # DNS rebinding: a page on evil.example resolving to 127.0.0.1
    client, _, _ = await make_client()
    assert (await client.get("/status", headers={"Host": "evil.example:8765"})).status == 403
    config.allowed_hosts = ["robot.example.org"]
    assert (await client.get("/status", headers={"Host": "robot.example.org"})).status == 200
    await client.close()


async def test_commands_logged_with_who_sent_them(make_client):
    """2026-10-01: stop pressed ~57 times in 10 s, the robot sent home and back; the log must say from where."""
    client, engine, _ = await make_client()
    auth = {"Authorization": "Basic " + base64.b64encode(b"family:secret").decode()}
    hdrs = {**auth, "X-Real-IP": "192.168.1.23", "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 7)"}
    await client.post("/stop", json={}, headers=hdrs)
    await client.post("/home", json={}, headers=hdrs)
    r = await client.post("/jobs", json={"rooms": ["kitchen"], "mode": "mop"}, headers=hdrs)
    job = await r.json()
    await engine.wait()
    await client.post("/pause", json={}, headers=hdrs)
    await client.post("/resume", json={}, headers=hdrs)
    await client.post("/goto", json={"room": "kitchen"}, headers=hdrs)
    recs = engine.events.recent()
    for code in ("stop", "home", "job", "pause", "resume", "goto"):
        by = next(e["by"] for e in recs if e["code"] == code)
        assert by == "family, 192.168.1.23, Mozilla/5.0 (Linux; Android 14; Pixel 7)"
    assert "secret" not in json.dumps(recs)
    assert (await (await client.get(f"/jobs/{job['id']}")).json())["by"].startswith("family, ")
    await client.close()


def _request(peer, headers):
    transport = mock.Mock()
    transport.get_extra_info.side_effect = lambda key, default=None: (peer, 1234) if key == "peername" else default
    return make_mocked_request("POST", "/stop", headers=headers, transport=transport)


def test_who_trusts_x_real_ip_only_from_the_local_proxy():
    hdrs = {"X-Real-IP": "192.168.1.23", "User-Agent": "curl/8"}
    assert _who(_request("127.0.0.1", hdrs)) == "192.168.1.23, curl/8"
    assert _who(_request("10.0.0.5", hdrs)) == "10.0.0.5, curl/8"


def test_who_with_a_broken_authorization_header():
    assert _who(_request("10.0.0.5", {"Authorization": "Basic %%%"})) == "10.0.0.5"
    assert _who(_request("10.0.0.5", {"Authorization": "Bearer abc"})) == "10.0.0.5"

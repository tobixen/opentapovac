import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from opentapovac.client import DaemonClient
from opentapovac.engine import Engine
from opentapovac.rooms import RoomTable
from opentapovac.web.server import make_app
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
    assert "OpenTapoVac" in await r.text()
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
    assert (await client.post("/jobs", json={"rooms": ["kitchen"]})).status == 409
    assert (await client.post("/stop")).status == 200
    await client.close()


async def test_status_and_map(make_client):
    client, _, _ = await make_client()
    st = await (await client.get("/status")).json()
    assert st["status_text"] == "drying mop"
    assert (await client.get("/map.png")).content_type == "image/png"
    assert (await client.post("/map/refresh")).status == 200
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

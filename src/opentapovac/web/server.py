"""The daemon's HTTP API and the web page (docs/design.md §5).

Binds to localhost by default; put a reverse proxy with auth in front of it
for anything else.  No accounts here.
"""

from __future__ import annotations

import asyncio
import json
import math
from importlib.resources import files
from urllib.parse import urlsplit

from aiohttp import web

from .. import __version__
from ..engine import MAP_MAX_AGE, AnswerError, Busy, Engine, JobRequest, PlanError
from ..payloads import MODE_LABELS
from ..robot import RobotError
from ..tracks import KINDS

ENGINE: web.AppKey[Engine] = web.AppKey("engine", Engine)
ALLOWED_HOSTS: web.AppKey[set[str]] = web.AppKey("allowed_hosts", set)
STATIC = files(__package__) / "static"
KEEPALIVE = 15


def _error(status: int, msg: str) -> web.Response:
    return web.json_response({"error": msg}, status=status)


@web.middleware
async def errors(request: web.Request, handler):
    try:
        return await handler(request)
    except (PlanError, AnswerError) as e:
        return _error(400, str(e))
    except Busy as e:
        return _error(409, str(e))
    except RobotError as e:
        return _error(502, f"robot: {e}")


def _hostname(host: str) -> str:
    """The host name without the port: example.org:8765 -> example.org, [::1]:8765 -> ::1."""
    return urlsplit(f"//{host}").hostname or ""


@web.middleware
async def same_site(request: web.Request, handler):
    """No accounts here, so keep other web pages out.

    The Host must be one we serve (against DNS rebinding), and a POST must be
    JSON (a cross-site form or no-cors fetch can't send that without a CORS
    preflight, which is never answered) from our own Origin, if it names one.
    """
    if _hostname(request.host) not in request.app[ALLOWED_HOSTS]:
        return _error(403, f"unknown host {request.host!r} (see allowed_hosts in the config)")
    if request.method == "POST":
        if request.content_type != "application/json":
            return _error(415, "POST bodies must be application/json")
        origin = request.headers.get("Origin")
        if origin and urlsplit(origin).netloc != request.host:
            return _error(403, f"cross-origin request from {origin}")
    return await handler(request)


routes = web.RouteTableDef()


@routes.get("/")
async def index(request: web.Request) -> web.Response:
    return web.Response(text=(STATIC / "index.html").read_text(), content_type="text/html")


@routes.get("/static/{name}")
async def static(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    f = STATIC / name
    if "/" in name or name.startswith(".") or not f.is_file():
        raise web.HTTPNotFound()
    ctype = {"js": "text/javascript", "css": "text/css"}.get(name.rsplit(".", 1)[-1], "application/octet-stream")
    return web.Response(text=f.read_text(), content_type=ctype)


@routes.get("/ping")
async def ping(request: web.Request) -> web.Response:
    return web.json_response({"ok": True, "version": __version__})


@routes.get("/rooms")
async def rooms(request: web.Request) -> web.Response:
    engine = request.app[ENGINE]
    if not len(engine.rooms):
        await engine.refresh_rooms()
    return web.json_response(_rooms(engine))


@routes.post("/rooms/refresh")
async def rooms_refresh(request: web.Request) -> web.Response:
    engine = request.app[ENGINE]
    await engine.refresh_rooms()
    return web.json_response(_rooms(engine))


def _rooms(engine: Engine) -> dict:
    c = engine.config
    return {
        "rooms": [
            {"id": r.id, "label": r.label, "forbidden": r.forbidden, "carry_in": r.carry_in, "carry_out": r.carry_out}
            for r in engine.rooms
        ],
        "presets": c.presets,
        "modes": MODE_LABELS,
        "default_mode": c.defaults.mode,
        "timezone": c.timezone,
    }


@routes.post("/jobs")
async def post_job(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _error(400, "body must be JSON")
    if not isinstance(body, dict):
        return _error(400, "body must be a JSON object")
    job = await request.app[ENGINE].submit(JobRequest.from_dict(body))
    return web.json_response(job.to_dict(), status=201)


@routes.get("/jobs/{id}")
async def get_job(request: web.Request) -> web.Response:
    job = request.app[ENGINE].jobs.get(request.match_info["id"])
    if job is None:
        return _error(404, "no such job")
    return web.json_response(job.to_dict())


@routes.post("/jobs/{id}/answer")
async def answer(request: web.Request) -> web.Response:
    engine = request.app[ENGINE]
    job_id = request.match_info["id"]
    if job_id not in engine.jobs:
        return _error(404, "no such job")
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return _error(400, "body must be JSON")
    if not isinstance(body, dict) or not isinstance(body.get("choice"), str):
        return _error(400, 'body must be {"choice": "..."}')
    engine.answer(job_id, body["choice"])
    return web.json_response(engine.jobs[job_id].to_dict())


@routes.post("/stop")
async def stop(request: web.Request) -> web.Response:
    await request.app[ENGINE].stop()
    return web.json_response({"ok": True})


@routes.post("/home")
async def home(request: web.Request) -> web.Response:
    await request.app[ENGINE].home()
    return web.json_response({"ok": True})


@routes.get("/status")
async def status(request: web.Request) -> web.Response:
    return web.json_response(await request.app[ENGINE].status())


@routes.get("/events")
async def events(request: web.Request) -> web.StreamResponse:
    """Server-sent events: the last `backlog` events, then live ones."""
    log = request.app[ENGINE].events
    backlog = int(request.query.get("backlog", "0"))
    resp = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"})
    await resp.prepare(request)
    q = log.subscribe()
    try:
        for rec in log.recent(backlog) if backlog else []:
            await resp.write(f"data: {json.dumps(rec)}\n\n".encode())
        while True:
            try:
                rec = await asyncio.wait_for(q.get(), KEEPALIVE)
                await resp.write(f"data: {json.dumps(rec)}\n\n".encode())
            except TimeoutError:
                await resp.write(b": keepalive\n\n")
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        log.unsubscribe(q)
    return resp


@routes.get("/map.png")
async def map_png(request: web.Request) -> web.Response:
    """`max_age`: hours of tracks to show (0: all, default 12); `show`: of the kinds vac,mop,move (default all)."""
    q = request.query
    try:
        hours = float(q["max_age"]) if "max_age" in q else MAP_MAX_AGE / 3600
    except ValueError:
        hours = -1
    if not 0 <= hours < math.inf:
        return _error(400, f"max_age: not a number of hours: {q['max_age']!r}")
    show = set(q["show"].split(",")) & KINDS if "show" in q else KINDS
    png = await request.app[ENGINE].map_png(max_age=hours * 3600 or None, show=show)
    return web.Response(body=png, content_type="image/png")


@routes.post("/map/refresh")
async def map_refresh(request: web.Request) -> web.Response:
    await request.app[ENGINE].map_png(refresh=True)
    return web.json_response({"ok": True})


class _AllowedHosts(set):
    """localhost, the listen address, and `allowed_hosts` from the config, read on every request."""

    def __init__(self, engine: Engine):
        super().__init__()
        self.engine = engine

    def __contains__(self, host: object) -> bool:
        c = self.engine.config
        listen = _hostname(c.listen.rpartition(":")[0] or "127.0.0.1")
        return host in {"localhost", "127.0.0.1", "::1", listen, *c.allowed_hosts}


def make_app(engine: Engine) -> web.Application:
    app = web.Application(middlewares=[same_site, errors])
    app[ENGINE] = engine
    app[ALLOWED_HOSTS] = _AllowedHosts(engine)
    app.add_routes(routes)
    return app


async def serve(engine: Engine, listen: str) -> None:
    """Run the daemon until cancelled."""
    host, _, port = listen.rpartition(":")
    runner = web.AppRunner(make_app(engine))
    await runner.setup()
    site = web.TCPSite(runner, host.strip("[]") or "127.0.0.1", int(port))
    await site.start()
    engine.events.emit("info", f"daemon listening on {listen}", code="daemon")
    watcher = asyncio.create_task(engine.watch())
    try:
        await asyncio.Event().wait()
    finally:
        watcher.cancel()
        await runner.cleanup()

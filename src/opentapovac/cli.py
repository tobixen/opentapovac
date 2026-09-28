"""opentapovac: clean rooms with a Tapo robot vacuum, from the command line.

PYTHON_ARGCOMPLETE_OK

Commands go to the daemon (`opentapovac serve`) when it answers, and run
standalone otherwise.  Standalone `clean` blocks until the robot is back on
the dock: nothing else watches it, so the computer must stay on the robot's
network until then.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import fcntl
import logging
import os
import re
import sys
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import argcomplete

from . import __version__, mapimg
from .client import DaemonClient, DaemonError
from .config import Config, load_config, load_credentials
from .engine import MAP_MAX_AGE, AnswerError, Busy, Engine, JobRequest, PlanError
from .events import EventLog, format_record
from .payloads import MODES
from .robot import KasaRobot, RobotError
from .rooms import RoomTable

MODE_FLAGS = {m: "--" + m.replace("_", "-") for m in MODES}
EXIT = {"done": 0, "failed": 1, "stopped": 3}
JOB_END = ("job_done", "job_failed", "job_stopped")


class CliError(Exception):
    pass


def room_completer(prefix: str, parsed_args: argparse.Namespace, **_: Any) -> list[str]:
    """Room names from the cache; completion never talks to the robot."""
    config = load_config(getattr(parsed_args, "config", None))
    names = RoomTable.load(config.rooms_cache, config.rooms).completions()
    given = {r.casefold() for r in getattr(parsed_args, "rooms", None) or []}
    return [n for n in names if n.casefold().startswith(prefix.casefold()) and n.casefold() not in given]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="opentapovac", description=__doc__.split("\n\n")[0])
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--config", help="config file (default ~/.config/opentapovac/config.yaml)")
    p.add_argument("--host", help="robot address, overrides the config")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--daemon", metavar="URL", help="daemon URL, overrides the config")
    g.add_argument("--no-daemon", action="store_true", help="talk to the robot directly")
    p.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("clean", help="clean rooms", description="Clean rooms, in the order given, in one run.")
    c.add_argument("rooms", nargs="*", metavar="ROOM", help="robot room name, alias or id").completer = room_completer
    modes = c.add_mutually_exclusive_group()
    for m, flag in MODE_FLAGS.items():
        modes.add_argument(flag, dest="mode", action="store_const", const=m)
    c.add_argument("--suction", type=int)
    c.add_argument("--water", type=int, help="water level (cistern)")
    c.add_argument("--passes", type=int)
    c.add_argument("--sequential", action="store_true", help="one run per room instead of one multi-room run")
    c.add_argument("--force", action="store_true", help="allow forbidden rooms and an unlocked map")
    c.add_argument("--wait", action="store_true", help="with a daemon: follow the job until it ends")

    a = sub.add_parser(
        "answer",
        help="answer the robot's question (carry it, ...)",
        description="Answer the current job's question; without CHOICE, show it.",
    )
    a.add_argument("choice", nargs="?", help="one of the offered choices (a unique prefix will do)")
    sub.add_parser("status", help="robot status")
    sub.add_parser("stop", help="stop the current run")
    sub.add_parser("home", help="send the robot to the dock (setSwitchCharge)")
    sub.add_parser("pause", help="pause the robot's run (setRobotPause)")
    sub.add_parser("resume", help="resume a paused run")
    g = sub.add_parser("goto", help="send the robot to a room, or to a point in map mm (gotoPoint)")
    g.add_argument("target", nargs="+", metavar="ROOM | X Y")
    r = sub.add_parser("rooms", help="list rooms")
    r.add_argument("--refresh", action="store_true", help="read the rooms from the robot")
    lg = sub.add_parser("log", help="recent events")
    lg.add_argument("-n", type=int, default=30)
    lg.add_argument("-f", "--follow", action="store_true", help="follow the daemon's events")
    m = sub.add_parser("map", help="save the map with the recent tracks as PNG")
    m.add_argument("out")
    m.add_argument(
        "--max-age", type=float, default=MAP_MAX_AGE / 3600, metavar="HOURS", help="tracks from the last HOURS (0: all)"
    )
    m.add_argument("--refresh", action="store_true", help="fetch a fresh map (always, when standalone)")
    rm = sub.add_parser("render-map", help="render saved getMapData/getPathData dumps", description=mapimg.__doc__)
    rm.add_argument("dump", help="getMapData reply (raw `kasa --json` output is fine)")
    rm.add_argument("out")
    rm.add_argument("--path", help="getPathData reply, drawn as the track")
    rm.add_argument("names", nargs="*", metavar="ID=NAME", help="room name overrides")
    s = sub.add_parser("serve", help="run the daemon")
    s.add_argument("--listen", help="HOST:PORT (default from config, 127.0.0.1:8765)")
    return p


def request_from_args(args: argparse.Namespace) -> JobRequest:
    return JobRequest(
        rooms=args.rooms,
        mode=args.mode,
        suction=args.suction,
        water=args.water,
        passes=args.passes,
        sequential=args.sequential,
        force=args.force,
    )


def echo(rec: dict[str, Any], config: Config) -> None:
    print(format_record(rec, config.timezone), flush=True)


@contextlib.contextmanager
def engine_lock(config: Config) -> Iterator[None]:
    """One engine per robot: the daemon and standalone runs take the same lock."""
    config.lock_file.parent.mkdir(parents=True, exist_ok=True)
    with config.lock_file.open("w") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CliError(
                f"another opentapovac engine is running (lock {config.lock_file}); "
                f"is the daemon at {config.daemon_url} not answering?"
            ) from None
        yield


@contextlib.asynccontextmanager
async def local_engine(config: Config, verbose_events: bool = True):
    if not config.host:
        raise CliError("no robot host: set robot.host in the config or use --host")
    try:
        user, password = load_credentials(config)
    except FileNotFoundError:
        raise CliError(f"no credentials: {config.credentials} or $KASA_USERNAME/$KASA_PASSWORD") from None
    with engine_lock(config):
        events = EventLog(config.events_file, config.timezone)
        if verbose_events:
            events.add_listener(lambda rec: echo(rec, config))
        robot = KasaRobot(config.host, user, password, config.port, config.timeout)
        try:
            yield Engine(robot, config, events)
        finally:
            await robot.close()


def parse_choice(line: str, choices: list[str]) -> str | None:
    """The choice `line` names: exactly, or by a unique prefix, ignoring case."""
    t = line.strip().casefold()
    if not t:
        return None
    if t in choices:
        return t
    hits = [c for c in choices if c.casefold().startswith(t)]
    return hits[0] if len(hits) == 1 else None


#: bytes read from stdin past the last line handed out
_stdin_rest = bytearray()


async def read_line(prompt: str) -> str | None:
    """A line from stdin without blocking the event loop; None on EOF or without a usable stdin.

    Reads the file descriptor itself: `sys.stdin.readline()` would buffer any
    further lines where the event loop can't see them.
    """
    print(prompt, end="", flush=True)
    loop = asyncio.get_running_loop()
    try:
        fd = sys.stdin.fileno()
    except (ValueError, OSError, AttributeError):
        return None
    while b"\n" not in _stdin_rest:
        fut: asyncio.Future[bytes] = loop.create_future()
        try:
            loop.add_reader(fd, lambda f=fut: f.done() or f.set_result(os.read(fd, 4096)))
        except (ValueError, OSError, NotImplementedError):
            return None
        try:
            chunk = await fut
        finally:
            loop.remove_reader(fd)
        if not chunk:
            return None
        _stdin_rest.extend(chunk)
    line, _, rest = bytes(_stdin_rest).partition(b"\n")
    _stdin_rest[:] = rest
    return line.decode(errors="replace")


class TerminalAsker:
    """Puts a job's questions to the terminal, fed with its events.

    An answer given elsewhere (the web page) cancels the prompt.
    """

    def __init__(
        self,
        fetch: Callable[[str], Awaitable[dict[str, Any] | None]],
        send: Callable[[str, str], Awaitable[Any]],
    ):
        self.fetch, self.send = fetch, send
        self._task: asyncio.Task | None = None

    def feed(self, rec: dict[str, Any]) -> None:
        code = rec.get("code")
        if code == "ask" and rec.get("job"):
            self.cancel()
            self._task = asyncio.get_running_loop().create_task(self._ask(rec["job"]))
        elif code in ("answered", "carried", "skipped") or code in JOB_END:
            self.cancel()

    def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def _ask(self, job_id: str) -> None:
        q = await self.fetch(job_id)
        if not q:
            return
        while True:
            line = await read_line(f"{q['text']} [{'/'.join(q['choices'])}]: ")
            if line is None:
                print("\n(no terminal to answer on: use `opentapovac answer` or the web page)", file=sys.stderr)
                return
            choice = parse_choice(line, q["choices"])
            if choice:
                break
        self._task = None  # the "answered" event must not cancel the task sending it
        try:
            await self.send(job_id, choice)
        except (AnswerError, DaemonError) as e:
            print(f"opentapovac: {e}", file=sys.stderr)


def print_status(st: dict[str, Any]) -> None:
    line = st["status_text"]
    if st["errors"]:
        line += " — " + ", ".join(e["text"] for e in st["errors"])
    print(line)
    if st.get("battery") is not None:
        print(f"battery: {st['battery']} %")
    if st.get("clean_water_empty"):
        print("clean water tank in the base is empty")
    if st.get("relocating"):
        print("relocating")
    if st.get("job"):
        print_job(st["job"])


def print_job(j: dict[str, Any]) -> None:
    line = f"job {j['id']}: {j['description']} — {j['state']}"
    if j["steps"] > 1:
        line += f" (step {j['step']}/{j['steps']})"
    if j["message"]:
        line += f": {j['message']}"
    print(line)
    for w in j.get("warnings") or []:
        print(f"warning: {w}")
    if j.get("question"):
        q = j["question"]
        print(f"question: {q['text']} [{'/'.join(q['choices'])}] — `opentapovac answer CHOICE`")


def print_rooms(rooms: list[dict[str, Any]]) -> None:
    for r in rooms:
        flags = [f for f in ("forbidden", "carry_in", "carry_out") if r.get(f)]
        print(f"{r['id']:>3}  {r['label']}" + (f"  ({', '.join(flags)})" if flags else ""))


def goto_target(words: list[str]) -> tuple[tuple[int, int] | None, str | None]:
    """`X Y` (two integers, map mm) or a room name."""
    if len(words) == 2 and all(re.fullmatch(r"-?\d+", w) for w in words):
        return (int(words[0]), int(words[1])), None
    return None, " ".join(words)


async def via_daemon(args: argparse.Namespace, config: Config, dc: DaemonClient) -> int:
    cmd = args.command
    if cmd == "clean":
        job = await dc.submit(asdict(request_from_args(args)))
        print_job(job)
        if not args.wait:
            return 0

        async def question(job_id: str) -> dict[str, Any] | None:
            return (await dc.job(job_id))["question"]

        asker = TerminalAsker(question, dc.answer)
        try:
            # with a backlog: the job may have moved on (or asked) before we got here
            async for rec in dc.events(backlog=200):
                if rec.get("job") == job["id"]:
                    echo(rec, config)
                    asker.feed(rec)
                    if rec.get("code") in JOB_END:
                        break
        finally:
            asker.cancel()
        job = await dc.job(job["id"])
        return EXIT.get(job["state"], 1)
    if cmd == "answer":
        return await answer(args, dc)
    if cmd == "status":
        print_status(await dc.status())
    elif cmd == "stop":
        await dc.stop()
    elif cmd == "home":
        await dc.home()
    elif cmd in ("pause", "resume"):
        await dc.pause() if cmd == "pause" else await dc.resume()
    elif cmd == "goto":
        point, room = goto_target(args.target)
        print("sent to", await dc.goto(point, room))
    elif cmd == "rooms":
        print_rooms((await dc.rooms(refresh=args.refresh))["rooms"])
    elif cmd == "map":
        Path(args.out).write_bytes(await dc.map_png(refresh=args.refresh, max_age=args.max_age))
    elif cmd == "log" and args.follow:
        async for rec in dc.events(backlog=args.n):
            echo(rec, config)
    return 0


async def answer(args: argparse.Namespace, dc: DaemonClient) -> int:
    job = (await dc.status())["job"]
    q = job and job["question"]
    if not q:
        raise CliError("no question to answer")
    if args.choice is None:
        print(f"{q['text']} [{'/'.join(q['choices'])}]")
        return 0
    choice = parse_choice(args.choice, q["choices"])
    if choice is None:
        raise CliError(f"choose one of: {', '.join(q['choices'])}")
    print_job(await dc.answer(job["id"], choice))
    return 0


async def standalone(args: argparse.Namespace, config: Config) -> int:
    cmd = args.command
    if cmd in ("log", "answer") and (cmd == "answer" or args.follow):
        raise CliError(
            f"{cmd} needs the daemon ({config.daemon_url} does not answer); a standalone clean asks on its own terminal"
        )
    async with local_engine(config, verbose_events=cmd == "clean") as engine:
        if cmd == "clean":

            async def question(job_id: str) -> dict[str, Any] | None:
                return engine.jobs[job_id].to_dict()["question"]

            async def send(job_id: str, choice: str) -> None:
                engine.answer(job_id, choice)

            asker = TerminalAsker(question, send)
            engine.events.add_listener(asker.feed)
            try:
                job = await engine.run(request_from_args(args))
            finally:
                asker.cancel()
            print_job(job.to_dict())
            return EXIT.get(job.state, 1)
        if cmd == "status":
            print_status(await engine.status())
        elif cmd == "stop":
            await engine.stop()
        elif cmd == "home":
            await engine.home()
        elif cmd in ("pause", "resume"):
            await engine.pause() if cmd == "pause" else await engine.resume()
        elif cmd == "goto":
            point, room = goto_target(args.target)
            print("sent to", list(await engine.goto(point, room)))
        elif cmd == "rooms":
            if args.refresh or not len(engine.rooms):
                await engine.refresh_rooms()
            print_rooms([asdict(r) | {"label": r.label} for r in engine.rooms])
        elif cmd == "map":
            Path(args.out).write_bytes(await engine.map_png(refresh=True, max_age=args.max_age * 3600 or None))
        elif cmd == "serve":
            from .web.server import serve

            await serve(engine, args.listen or config.listen)
    return 0


def render_map(args: argparse.Namespace) -> int:
    d = mapimg.load_dump(args.dump)
    d = d.get("getMapData", d)
    path = None
    if args.path:
        path = mapimg.load_dump(args.path)
        path = path.get("getPathData", path)
    names = {int(k): v for k, v in (a.split("=", 1) for a in args.names)}
    mapimg.render(d, path, names).save(args.out)
    return 0


async def amain(args: argparse.Namespace, config: Config) -> int:
    if args.command == "serve":
        # the daemon is its own engine; refuse to start a second one
        dc = DaemonClient(config.daemon_url)
        try:
            if await dc.ping():
                raise CliError(f"a daemon already answers at {config.daemon_url}")
        finally:
            await dc.close()
        return await standalone(args, config)
    if not args.no_daemon:
        dc = DaemonClient(config.daemon_url)
        try:
            if await dc.ping():
                return await via_daemon(args, config, dc)
        finally:
            await dc.close()
    return await standalone(args, config)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argcomplete.autocomplete(parser)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.ERROR)
    if args.command == "render-map":
        return render_map(args)
    config = load_config(args.config)
    if args.host:
        config.host = args.host
    if args.daemon:
        config.daemon_url = args.daemon
    if args.command == "log" and not args.follow:
        for rec in EventLog(config.events_file, config.timezone).recent(args.n):
            echo(rec, config)
        return 0
    try:
        return asyncio.run(amain(args, config))
    except KeyboardInterrupt:
        if args.command == "clean":
            print("interrupted; the robot carries on — `opentapovac stop` stops it", file=sys.stderr)
        return 130
    except (CliError, PlanError, Busy, RobotError, DaemonError) as e:
        print(f"opentapovac: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

"""Configuration: `~/.config/opentapovac/config.yaml`, see docs/design.md §7."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG = Path("~/.config/opentapovac/config.yaml")
DEFAULT_CREDENTIALS = Path("~/.config/tapo/credentials.yaml")


@dataclass
class Defaults:
    """Per-room clean settings used when a request leaves them out."""

    mode: str = "vac_then_mop"
    suction: int = 2
    water: int = 2
    passes: int = 1


@dataclass
class Config:
    host: str | None = None
    port: int = 4433
    credentials: Path = DEFAULT_CREDENTIALS.expanduser()
    timeout: int = 30
    timezone: str | None = None
    defaults: Defaults = field(default_factory=Defaults)
    #: keyed by robot room name or id, values like {"aliases": [...], "forbidden": True};
    #: flags: forbidden, carry_in, carry_out
    rooms: dict[int | str, dict[str, Any]] = field(default_factory=dict)
    #: {"first": [rooms], "last": [rooms]}: the order of rooms within a run
    order: dict[str, list[int | str]] = field(default_factory=dict)
    presets: list[dict[str, Any]] = field(default_factory=list)
    listen: str = "127.0.0.1:8765"
    #: host names the web page is reached by, besides localhost and `listen` (a reverse proxy's name)
    allowed_hosts: list[str] = field(default_factory=list)
    daemon_url: str | None = None
    cache_dir: Path = Path("~/.cache/opentapovac").expanduser()
    state_dir: Path = Path("~/.local/state/opentapovac").expanduser()
    #: seconds between polls while a job runs
    poll_interval: float = 20
    #: 5/6 (charging/charged) must hold this long before the robot counts as idle
    settle: float = 60
    #: a run must show signs of life within this long after the send
    start_timeout: float = 120
    #: standby (status 0) this long during a run = the robot gave up
    gave_up_after: float = 300
    #: how long to wait for the robot to become idle before sending a run
    idle_timeout: float = 900
    #: after this long without an answer, a question to a human raises an alert (and keeps waiting)
    human_wait_timeout: float = 900
    #: after a carry-in, the robot must be cleaning, not relocating, this long before its position is checked
    verify_after: float = 60
    #: the daemon checks the robot this often when no job runs (runs from the app, the end of a job)
    watch_interval: float = 60
    #: a progress line (percent, pass, room, battery) this often while the robot works
    progress_interval: float = 60
    #: cleaning without progress this long: warn
    stall_after: float = 300
    #: send a run again, once, when the robot left it unfinished or skipped its mop pass
    redo_missed: bool = True
    #: lost on its way home (dock not found): go to these rooms in turn, then home;
    #: `waypoints: {home_route: [...]}` in the file, `rooms: {X: {home_route: [...]}}` per room
    home_route: list[int | str] = field(default_factory=list)
    #: a waypoint of the home route not reached within this long: a human is asked
    waypoint_timeout: float = 180

    def __post_init__(self) -> None:
        if self.daemon_url is None:
            host, _, port = self.listen.rpartition(":")
            if host in ("", "0.0.0.0", "::", "[::]"):
                host = "127.0.0.1"
            self.daemon_url = f"http://{host}:{port}"

    @property
    def events_file(self) -> Path:
        return self.state_dir / "events.jsonl"

    @property
    def tracks_dir(self) -> Path:
        return self.state_dir / "tracks"

    @property
    def clean_records_file(self) -> Path:
        return self.state_dir / "clean-records.jsonl"

    @property
    def rooms_cache(self) -> Path:
        return self.cache_dir / "rooms.json"

    @property
    def lock_file(self) -> Path:
        return self.state_dir / "engine.lock"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Config:
        d = dict(d or {})
        kw: dict[str, Any] = {}
        robot = d.pop("robot", None) or {}
        for key in ("host", "port", "timeout"):
            if key in robot:
                kw[key] = robot[key]
        if "credentials" in robot:
            kw["credentials"] = Path(robot["credentials"]).expanduser()
        if "defaults" in d:
            kw["defaults"] = Defaults(**d.pop("defaults"))
        for key in ("cache_dir", "state_dir"):
            if key in d:
                kw[key] = Path(d.pop(key)).expanduser()
        monitor = d.pop("monitor", None) or {}
        kw.update(monitor)
        waypoints = d.pop("waypoints", None) or {}
        if not isinstance(waypoints, dict):
            raise ValueError("waypoints must be a mapping, like {home_route: [hall, kjøkken]}")
        if "home_route" in waypoints:
            kw["home_route"] = _route(waypoints["home_route"], "waypoints.home_route")
        for key, conf in (d.get("rooms") or {}).items():
            if isinstance(conf, dict) and conf.get("home_route") is not None:
                _route(conf["home_route"], f"rooms.{key}.home_route")
        # notify (milestone 6) is accepted and ignored for now
        kw.update({k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        for key in DURATIONS:
            if key in kw:
                kw[key] = parse_duration(kw[key])
        return cls(**kw)


def _route(v: Any, where: str) -> list[int | str]:
    """A list of room names or ids; a single name would otherwise become its letters."""
    if not isinstance(v, list) or not all(isinstance(r, int | str) and not isinstance(r, bool) for r in v):
        raise ValueError(f"{where} must be a list of rooms (names or ids), got {v!r}")
    return list(v)


DURATIONS = ("poll_interval", "settle", "start_timeout", "gave_up_after", "idle_timeout")
DURATIONS += ("human_wait_timeout", "verify_after", "watch_interval", "progress_interval", "stall_after")
DURATIONS += ("waypoint_timeout",)
_UNITS = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_duration(v: float | str) -> float:
    """Seconds, from a number or a string like "90s", "15m", "1h"."""
    if isinstance(v, int | float):
        return v
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*", str(v))
    if not m:
        raise ValueError(f"bad duration {v!r} (expected e.g. 90, 90s, 15m, 1h)")
    return float(m[1]) * _UNITS[m[2]]


def load_config(path: str | Path | None = None) -> Config:
    """Read the config file; a missing file gives the defaults."""
    path = Path(path or os.environ.get("OPENTAPOVAC_CONFIG") or DEFAULT_CONFIG).expanduser()
    if not path.exists():
        return Config()
    return Config.from_dict(yaml.safe_load(path.read_text()) or {})


def load_credentials(config: Config) -> tuple[str, str]:
    """Tapo account (user, password): $KASA_USERNAME/$KASA_PASSWORD, else the credentials file."""
    user, password = os.environ.get("KASA_USERNAME"), os.environ.get("KASA_PASSWORD")
    if user and password:
        return user, password
    d = yaml.safe_load(config.credentials.read_text())
    return d["user"], d["pass"]

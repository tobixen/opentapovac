"""Configuration: `~/.config/opentapovac/config.yaml`, see docs/design.md §7."""

from __future__ import annotations

import os
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
    #: keyed by robot room name or id, values like {"aliases": [...], "forbidden": True}
    rooms: dict[int | str, dict[str, Any]] = field(default_factory=dict)
    presets: list[dict[str, Any]] = field(default_factory=list)
    listen: str = "127.0.0.1:8765"
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
        # home rules for later milestones (order, waypoints, ...) are accepted and ignored for now
        kw.update({k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        return cls(**kw)


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

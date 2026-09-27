"""HTTP client for the daemon; the CLI uses it when the daemon answers."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import aiohttp


class DaemonError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


class DaemonClient:
    def __init__(self, url: str, timeout: float = 120):
        self.url = url.rstrip("/")
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout))

    async def close(self) -> None:
        await self._session.close()

    async def ping(self) -> bool:
        try:
            async with self._session.get(self.url + "/ping", timeout=aiohttp.ClientTimeout(total=2)) as r:
                return r.status == 200 and (await r.json()).get("ok") is True
        except (aiohttp.ClientError, TimeoutError, json.JSONDecodeError):
            return False

    async def _call(self, method: str, path: str, body: Any = None) -> Any:
        if method == "POST" and body is None:
            body = {}  # the daemon takes JSON POSTs only
        async with self._session.request(method, self.url + path, json=body) as r:
            data = await r.json()
            if r.status >= 400:
                raise DaemonError(r.status, data.get("error", str(data)))
            return data

    async def submit(self, request: dict[str, Any]) -> dict[str, Any]:
        return await self._call("POST", "/jobs", request)

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/jobs/{job_id}")

    async def answer(self, job_id: str, choice: str) -> dict[str, Any]:
        return await self._call("POST", f"/jobs/{job_id}/answer", {"choice": choice})

    async def status(self) -> dict[str, Any]:
        return await self._call("GET", "/status")

    async def stop(self) -> None:
        await self._call("POST", "/stop")

    async def home(self) -> None:
        await self._call("POST", "/home")

    async def goto(self, point: tuple[int, int] | None = None, room: str | None = None) -> list[int]:
        body = {"room": room} if room is not None else {"x": point[0], "y": point[1]} if point else {}
        return (await self._call("POST", "/goto", body))["point"]

    async def rooms(self, refresh: bool = False) -> dict[str, Any]:
        return await self._call("POST", "/rooms/refresh") if refresh else await self._call("GET", "/rooms")

    async def map_png(self, refresh: bool = False, max_age: float | None = None) -> bytes:
        """`max_age`: hours of tracks to show (0: all; None: the daemon's default)."""
        if refresh:
            await self._call("POST", "/map/refresh")
        params = {} if max_age is None else {"max_age": max_age}
        async with self._session.get(self.url + "/map.png", params=params) as r:
            if r.status >= 400:
                raise DaemonError(r.status, (await r.json()).get("error", ""))
            return await r.read()

    async def events(self, backlog: int = 0) -> AsyncIterator[dict[str, Any]]:
        """Follow the daemon's event stream (no total timeout)."""
        async with self._session.get(
            self.url + "/events", params={"backlog": backlog}, timeout=aiohttp.ClientTimeout(total=None)
        ) as r:
            async for line in r.content:
                if line.startswith(b"data: "):
                    yield json.loads(line[6:])

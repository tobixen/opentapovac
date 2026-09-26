"""Thin async wrapper over python-kasa: connect, query, error-checked send.

All robot I/O goes through `Robot.query`, which serialises calls: two
queries at the same moment can fail on this robot (docs/protocol.md).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

_LOGGER = logging.getLogger(__name__)


class RobotError(Exception):
    pass


class Robot:
    """Base class; subclasses implement `_raw`.  Tests use a scripted one."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    async def _raw(self, method: str, params: dict[str, Any] | None) -> Any:
        raise NotImplementedError

    async def close(self) -> None:
        pass

    async def query(self, method: str, params: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            return await self._raw(method, params)

    async def send(self, method: str, params: dict[str, Any]) -> None:
        """A write: anything but a `null` result is a failure."""
        result = await self.query(method, params)
        if result is not None:
            raise RobotError(f"{method} was not accepted: {result!r}")

    async def vac_status(self) -> dict[str, Any]:
        return await self.query("getVacStatus")

    async def clean_status(self) -> dict[str, Any]:
        return await self.query("getCleanStatus")

    async def battery(self) -> dict[str, Any]:
        return await self.query("getBatteryInfo")

    async def base_status(self) -> dict[str, Any]:
        return await self.query("getBaseStatus")

    async def map_info(self) -> dict[str, Any]:
        return await self.query("getMapInfo")

    async def map_data(self, map_id: int | None = None) -> dict[str, Any]:
        if map_id is None:
            map_id = (await self.map_info())["current_map_id"]
        return await self.query("getMapData", {"map_id": map_id, "type": 0})

    async def path_data(self, start_pos: int = 0) -> dict[str, Any]:
        """The track since `start_pos` (the robot's own index, header entry included)."""
        return await self.query("getPathData", {"start_pos": start_pos})

    async def clean_info(self) -> dict[str, Any]:
        """The current run: `clean_time` (min), `clean_area` (m²), `clean_percent`."""
        return await self.query("getCleanInfo")

    async def mop_state(self) -> dict[str, Any]:
        """`mop_state`: true while the mop is on."""
        return await self.query("getMopState")

    async def clean_records(self) -> dict[str, Any]:
        return await self.query("getCleanRecords")


class KasaRobot(Robot):
    """The real robot, over TPAP/HTTPS with python-kasa (the PR branch, see docs/design.md)."""

    def __init__(self, host: str, username: str, password: str, port: int = 4433, timeout: int = 30):
        super().__init__()
        self.host, self.port, self.timeout = host, port, timeout
        self._username, self._password = username, password
        self._dev: Any = None

    async def _connect(self) -> Any:
        from kasa import Credentials, Device
        from kasa.deviceconfig import DeviceConfig, DeviceConnectionParameters, DeviceEncryptionType, DeviceFamily

        # what `kasa --port 4433 --https -e tpap -df SMART.TAPOROBOVAC` builds
        ctype = DeviceConnectionParameters(DeviceFamily("SMART.TAPOROBOVAC"), DeviceEncryptionType("TPAP"), None, True)
        config = DeviceConfig(
            host=self.host,
            port_override=self.port,
            credentials=Credentials(self._username, self._password),
            timeout=self.timeout,
            connection_type=ctype,
        )
        return await Device.connect(config=config)

    async def _raw(self, method: str, params: dict[str, Any] | None) -> Any:
        from kasa import KasaException

        try:
            if self._dev is None:
                self._dev = await self._connect()
            reply = await self._dev.protocol.query({method: params})
        except KasaException as e:
            # drop the session; the next query reconnects.  No retry here: a
            # write may have gone through, and sending it twice is worse.
            await self.close()
            raise RobotError(f"{method}: {e}") from e
        except (TimeoutError, OSError) as e:
            await self.close()
            raise RobotError(f"{method}: {e!r}") from e
        except Exception as e:
            # a bug in the library (the TPAP branch is unreleased): one line for
            # the user, the traceback with -v
            _LOGGER.debug("%s failed", method, exc_info=True)
            await self.close()
            raise RobotError(f"{method}: {type(e).__name__}: {e} (python-kasa bug? -v shows the traceback)") from e
        if method not in reply:
            raise RobotError(f"{method}: empty reply")
        return reply[method]

    async def close(self) -> None:
        dev, self._dev = self._dev, None
        if dev is not None:
            try:
                await dev.disconnect()
            except Exception:  # noqa: BLE001 — closing a broken session
                _LOGGER.debug("disconnect failed", exc_info=True)

"""The robot's clock and its quiet hours (do-not-disturb), against the house's time zone.

The robot has the right Unix time, but its own offset from UTC (`time_diff`),
which it does not move for summer time; its quiet hours are in that robot
time (docs/protocol.md).  Inside them it is silent and does not empty the bin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo


def hhmm(minutes: int) -> str:
    minutes %= 1440
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


@dataclass(frozen=True)
class RobotClock:
    #: Unix time, as the robot read it
    timestamp: int
    #: the robot's offset from UTC, minutes
    time_diff: int
    quiet_on: bool
    #: the quiet hours, minutes after midnight, robot time
    quiet_start: int
    quiet_end: int
    #: the house's time zone (None: the local one)
    timezone: str | None = None

    @classmethod
    def from_replies(cls, device_time: dict[str, Any], dnd: dict[str, Any], timezone: str | None) -> RobotClock:
        """From the getDeviceTime and getDoNotDisturb replies; KeyError when one lacks a field."""
        start = end = 0
        if dnd.get("do_not_disturb"):
            start, end = int(dnd["s_min"]), int(dnd["e_min"])
        # quiet hours from 22:00 to 22:00 are none
        return cls(int(device_time["timestamp"]), int(device_time["time_diff"]), start != end, start, end, timezone)

    def _local(self) -> datetime:
        t = datetime.fromtimestamp(self.timestamp, UTC)
        return t.astimezone(ZoneInfo(self.timezone)) if self.timezone else t.astimezone()

    @property
    def skew(self) -> int:
        """How far the robot's time is ahead of the house's, minutes (negative: behind)."""
        offset = self._local().utcoffset()
        return self.time_diff - (int(offset.total_seconds()) // 60 if offset else 0)

    def _now(self) -> int:
        """When the robot read the time: minutes after midnight, robot time."""
        return (self.timestamp // 60 + self.time_diff) % 1440

    def quiet(self) -> bool:
        """Whether the robot was inside its quiet hours when it read the time."""
        if not self.quiet_on:
            return False
        now = self._now()
        if self.quiet_start < self.quiet_end:
            return self.quiet_start <= now < self.quiet_end
        return now >= self.quiet_start or now < self.quiet_end

    def starts_in(self) -> int | None:
        """Minutes until the quiet hours start; None when off or inside them."""
        if not self.quiet_on or self.quiet():
            return None
        return (self.quiet_start - self._now()) % 1440

    def ends_in(self) -> int | None:
        """Minutes until the quiet hours end; None when outside them."""
        return (self.quiet_end - self._now()) % 1440 if self.quiet() else None

    def _hours(self, shift: int = 0) -> str:
        return f"{hhmm(self.quiet_start + shift)}–{hhmm(self.quiet_end + shift)}"

    def describe_quiet(self) -> str | None:
        """The quiet hours, and in the house's time when that differs; None when they are off."""
        if not self.quiet_on:
            return None
        if not self.skew:
            return self._hours()
        return f"{self._hours()} robot time ({self._hours(-self.skew)} here)"

    def describe_skew(self) -> str | None:
        if not self.skew:
            return None
        where = self.timezone or "the local time"
        return f"the robot's clock is {abs(self.skew)} min {'ahead of' if self.skew > 0 else 'behind'} {where}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "time_diff": self.time_diff,
            "skew": self.skew,
            "quiet_hours": self._hours() if self.quiet_on else None,
            "quiet_hours_local": self._hours(-self.skew) if self.quiet_on else None,
            "quiet_now": self.quiet(),
            "quiet_starts_in": self.starts_in(),
            "quiet_ends_in": self.ends_in(),
        }

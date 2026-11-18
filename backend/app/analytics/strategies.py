"""Immutable delay policies. Only selection of the initial countdown differs."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import json
import re
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def positive_delay(value: timedelta):
    if not isinstance(value, timedelta) or value <= timedelta(0):
        raise ValueError("Delay must be a positive timedelta")


def minute_of_day(value: str, *, end: bool = False) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError("Time must have HH:MM format")
    hour, minute = map(int, value.split(":"))
    if end and (hour, minute) == (24, 0):
        return 1440
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Invalid clock time; 24:00 is allowed only as interval end")
    return hour * 60 + minute


@dataclass(frozen=True)
class FixedDelay:
    delay: timedelta

    def __post_init__(self):
        positive_delay(self.delay)

    def delay_at(self, at: datetime) -> timedelta:
        return self.delay

    @property
    def name(self):
        return "fixed_delay"

    @property
    def configuration(self):
        return {"delay_seconds": self.delay.total_seconds()}

    @property
    def minimum_delay(self):
        return self.delay


@dataclass(frozen=True)
class ScheduleWindow:
    weekdays: tuple[int, ...]  # Monday=0 ... Sunday=6
    start: str
    end: str
    delay: timedelta

    def __post_init__(self):
        days = tuple(self.weekdays)
        if not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise ValueError("Weekdays must be nonempty integers in [0, 6]")
        object.__setattr__(self, "weekdays", tuple(sorted(set(days))))
        if minute_of_day(self.start) >= minute_of_day(self.end, end=True):
            raise ValueError("Require start < end; split overnight windows into two daily windows")
        positive_delay(self.delay)


@dataclass(frozen=True)
class WeeklySchedule:
    default_delay: timedelta
    windows: tuple[ScheduleWindow, ...] = ()
    timezone_name: str = "Europe/Warsaw"
    _zone: ZoneInfo = field(init=False, repr=False, compare=False, hash=False)

    def __post_init__(self):
        positive_delay(self.default_delay)
        windows = tuple(self.windows)
        if any(not isinstance(w, ScheduleWindow) for w in windows):
            raise ValueError("Expected ScheduleWindow entries")
        windows = tuple(sorted(windows, key=lambda w: (w.weekdays, w.start, w.end)))
        for index, left in enumerate(windows):
            for right in windows[index + 1:]:
                if (set(left.weekdays) & set(right.weekdays)
                        and minute_of_day(left.start) < minute_of_day(right.end, end=True)
                        and minute_of_day(right.start) < minute_of_day(left.end, end=True)):
                    raise ValueError("Overlapping schedule windows")
        object.__setattr__(self, "windows", windows)
        try:
            zone = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError) as error:
            raise ValueError("Unknown/unavailable timezone. Install backend requirements (tzdata "
                             "on Windows) and verify the IANA timezone name.") from error
        object.__setattr__(self, "_zone", zone)

    def delay_at(self, at: datetime) -> timedelta:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Timestamp must include timezone")
        local = at.astimezone(self._zone)
        minute = local.hour * 60 + local.minute
        for window in self.windows:
            if (local.weekday() in window.weekdays
                    and minute_of_day(window.start) <= minute < minute_of_day(window.end, end=True)):
                return window.delay
        return self.default_delay

    @property
    def name(self):
        return "weekly_schedule"

    @property
    def configuration(self):
        return {"timezone": self.timezone_name, "default_delay_seconds": self.default_delay.total_seconds(),
                "windows": [{"weekdays": list(w.weekdays), "start": w.start, "end": w.end,
                             "delay_seconds": w.delay.total_seconds()} for w in self.windows]}

    @property
    def minimum_delay(self):
        return min([self.default_delay] + [w.delay for w in self.windows])


@runtime_checkable
class Strategy(Protocol):
    """Immutable delay-selection policy; controller owns all switching logic."""

    def delay_at(self, at: datetime) -> timedelta: ...

    @property
    def name(self) -> str: ...

    @property
    def configuration(self) -> dict: ...

    @property
    def minimum_delay(self) -> timedelta: ...


def as_strategy(value: timedelta | Strategy) -> Strategy:
    if isinstance(value, timedelta):
        return FixedDelay(value)
    if isinstance(value, Strategy):
        return value
    raise ValueError("Expected a timedelta or a delay-selection strategy")


def strategy_id(strategy: Strategy) -> str:
    return strategy.name + ":" + json.dumps(strategy.configuration, sort_keys=True, separators=(",", ":"))


def example_schedule() -> WeeklySchedule:
    return WeeklySchedule(timedelta(minutes=2), (
        ScheduleWindow(tuple(range(5)), "08:00", "16:00", timedelta(minutes=10)),
    ))

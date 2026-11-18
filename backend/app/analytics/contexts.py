"""Local context partition and immutable history-trained delay policy."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .strategies import ScheduleWindow, WeeklySchedule, minute_of_day, positive_delay


@dataclass(frozen=True)
class ContextGroup:
    name: str
    weekdays: tuple[int, ...]
    hours: tuple[tuple[str, str], ...]

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Context name must be nonempty")
        days = tuple(self.weekdays)
        hours = tuple(sorted(tuple(pair) for pair in self.hours))
        if not hours:
            raise ValueError("Context must have at least one time range")
        for start, end in hours:
            ScheduleWindow(days, start, end, timedelta(minutes=1))
        object.__setattr__(self, "weekdays", tuple(sorted(set(days))))
        object.__setattr__(self, "hours", hours)

    @property
    def configuration(self):
        return {"name": self.name, "weekdays": list(self.weekdays),
                "hours": [list(pair) for pair in self.hours]}


@dataclass(frozen=True)
class ContextPartition:
    groups: tuple[ContextGroup, ...]
    timezone_name: str = "Europe/Warsaw"

    def __post_init__(self):
        groups = tuple(self.groups)
        if not groups or any(not isinstance(g, ContextGroup) for g in groups):
            raise ValueError("Require context groups")
        if len({g.name for g in groups}) != len(groups):
            raise ValueError("Context names must be unique")
        windows = tuple(ScheduleWindow(g.weekdays, start, end, timedelta(minutes=1))
                        for g in groups for start, end in g.hours)
        # Reuse clock, timezone and overlap validation; a partition must also cover 24/7.
        WeeklySchedule(timedelta(minutes=1), windows, self.timezone_name)
        for day in range(7):
            if sum(minute_of_day(w.end, end=True) - minute_of_day(w.start)
                   for w in windows if day in w.weekdays) != 1440:
                raise ValueError("Contexts must cover every local day completely without overlap")
        object.__setattr__(self, "groups", groups)

    def index_at(self, at: datetime) -> int:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Timestamp must include timezone")
        local = at.astimezone(ZoneInfo(self.timezone_name))
        minute = local.hour * 60 + local.minute
        for index, group in enumerate(self.groups):
            if local.weekday() in group.weekdays and any(
                minute_of_day(start) <= minute < minute_of_day(end, end=True)
                for start, end in group.hours
            ):
                return index
        raise ValueError("Uncovered local time")


def default_contexts() -> ContextPartition:
    return ContextPartition((
        ContextGroup("workday_day", tuple(range(5)), (("08:00", "16:00"),)),
        ContextGroup("workday_other", tuple(range(5)), (("00:00", "08:00"), ("16:00", "24:00"))),
        ContextGroup("weekend_day", (5, 6), (("08:00", "16:00"),)),
        ContextGroup("weekend_other", (5, 6), (("00:00", "08:00"), ("16:00", "24:00"))),
    ))


@dataclass(frozen=True)
class AdaptiveDelay:
    contexts: ContextPartition
    delays: tuple[timedelta, ...]

    def __post_init__(self):
        delays = tuple(self.delays)
        if len(delays) != len(self.contexts.groups):
            raise ValueError("One delay is required per context")
        for delay in delays:
            positive_delay(delay)
        object.__setattr__(self, "delays", delays)

    def delay_at(self, at: datetime) -> timedelta:
        return self.delays[self.contexts.index_at(at)]

    @property
    def name(self):
        return "history_trained_delay"

    @property
    def minimum_delay(self):
        return min(self.delays)

    @property
    def configuration(self):
        return {"timezone": self.contexts.timezone_name,
                "groups": [dict(g.configuration, delay_seconds=d.total_seconds())
                           for g, d in zip(self.contexts.groups, self.delays)]}

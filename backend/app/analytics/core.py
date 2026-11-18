"""Pure UTC reconstruction and simulation with shared fixed/scheduled control."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Iterable

from .strategies import FixedDelay, Strategy, as_strategy


class Presence(str, Enum):
    PRESENT = "present"
    ABSENT = "absent"
    UNKNOWN = "unknown"


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone")
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class Report:
    at: datetime
    occupied: bool

    def __post_init__(self):
        object.__setattr__(self, "at", utc(self.at))
        if type(self.occupied) is not bool:
            raise ValueError("occupied must be a bool")


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime
    presence: Presence


def reconstruct(
    reports: Iterable[Report], start: datetime, end: datetime, freshness: timedelta
) -> tuple[Interval, ...]:
    """Partition [start, end). Reports expire at at + freshness; no backfilling.

    A fresh report at expiry wins. Equal duplicate reports collapse; conflicting
    reports at the same instant are rejected. Input ordering is irrelevant.
    """
    start, end = utc(start), utc(end)
    if end <= start or freshness <= timedelta(0):
        raise ValueError("Require start < end and positive freshness")
    by_time: dict[datetime, bool] = {}
    for report in reports:
        if report.at in by_time and by_time[report.at] != report.occupied:
            raise ValueError("Conflicting reports at the same timestamp")
        by_time[report.at] = report.occupied
    ordered = sorted(by_time.items())
    boundaries = {start, end}
    for at, _ in ordered:
        if start < at < end:
            boundaries.add(at)
        if start < at + freshness < end:
            boundaries.add(at + freshness)
    points = sorted(boundaries)
    result: list[Interval] = []
    index = 0
    latest = None
    for left, right in zip(points, points[1:]):
        while index < len(ordered) and ordered[index][0] <= left:
            latest = ordered[index]
            index += 1
        state = Presence.UNKNOWN
        if latest is not None and left < latest[0] + freshness:
            state = Presence.PRESENT if latest[1] else Presence.ABSENT
        if result and result[-1].presence == state:
            result[-1] = Interval(result[-1].start, right, state)
        else:
            result.append(Interval(left, right, state))
    return tuple(result)


class ControlState(str, Enum):
    ON = "on"
    WAITING = "waiting"
    SUSPENDED = "suspended"
    OFF = "off"


class DelayController:
    """Initially ON. UNKNOWN cancels pending countdown, preserving power state.

    update receives the current reconstructed presence, before evaluating timers.
    Caller must visit every presence boundary and pending deadline in time order.
    """

    def __init__(self, delay: timedelta | Strategy):
        self.strategy = as_strategy(delay)
        self.delay = self.strategy.delay if isinstance(self.strategy, FixedDelay) else None
        self.state = ControlState.ON
        self.deadline: datetime | None = None
        self.last_at: datetime | None = None

    def update(self, at: datetime, presence: Presence) -> bool | None:
        """Return True/False on an ON/OFF transition, otherwise None."""
        at = utc(at)
        if not isinstance(presence, Presence):
            raise ValueError("presence must be a Presence")
        if self.last_at is not None and at < self.last_at:
            raise ValueError("Controller time must not go backwards")
        self.last_at = at
        was_off = self.state == ControlState.OFF
        if presence == Presence.PRESENT:
            self.deadline = None
            self.state = ControlState.ON
            return True if was_off else None
        if presence == Presence.UNKNOWN:
            self.deadline = None
            if not was_off:
                self.state = ControlState.SUSPENDED
            return None
        if was_off:
            return None
        if self.deadline is None:
            self.deadline = at + self.strategy.delay_at(at)
        self.state = ControlState.WAITING
        if at >= self.deadline:
            self.state = ControlState.OFF
            self.deadline = None
            return False
        return None


# Compatibility name: both policies use exactly the same state machine.
FixedDelayController = DelayController


@dataclass(frozen=True)
class Transition:
    at: datetime
    previous: ControlState
    current: ControlState
    reason: str
    deadline: datetime | None = None


@dataclass(frozen=True)
class ReturnEvent:
    shutdown_at: datetime
    at: datetime
    is_short: bool
    uncertain: bool = False


@dataclass(frozen=True)
class Simulation:
    delay: timedelta | None  # None for a schedule, which has no single delay.
    short_return_window: timedelta
    shutdowns: tuple[datetime, ...]
    off_duration: timedelta
    short_returns: tuple[datetime, ...]
    # Off spells ending at the analysis boundary are censored, not observed returns.
    off_at_end: bool
    transitions: tuple[Transition, ...]
    # Segments split at presence boundaries, including UNKNOWN while already OFF.
    off_intervals: tuple[Interval, ...]
    return_events: tuple[ReturnEvent, ...]
    strategy: Strategy

    @property
    def off_known_absence(self) -> timedelta:
        return sum((i.end - i.start for i in self.off_intervals
                    if i.presence == Presence.ABSENT), timedelta(0))

    @property
    def off_unknown(self) -> timedelta:
        return sum((i.end - i.start for i in self.off_intervals
                    if i.presence == Presence.UNKNOWN), timedelta(0))


def simulate(
    intervals: Iterable[Interval], delay: timedelta | Strategy,
    short_return_window: timedelta = timedelta(minutes=2),
) -> Simulation:
    """Simulate one continuous timeline; no switch is emitted at its exclusive end.

    A short return is the first observed PRESENT after OFF with elapsed time
    <= short_return_window. Unknown intervals never count as a return.
    """
    if short_return_window < timedelta(0):
        raise ValueError("short_return_window must be nonnegative")
    machine = DelayController(delay)
    shutdowns, returns = [], []
    transitions, off_intervals, return_events = [], [], []

    def update(at: datetime, presence: Presence):
        previous = machine.state
        transition = machine.update(at, presence)
        if machine.state != previous:
            if presence == Presence.PRESENT:
                reason = "presence_restored"
            elif presence == Presence.UNKNOWN:
                reason = "data_unknown_countdown_cancelled"
            elif machine.state == ControlState.OFF:
                reason = "absence_delay_elapsed"
            else:
                reason = "absence_countdown_started"
            transitions.append(Transition(at, previous, machine.state, reason, machine.deadline))
        return transition

    off_since = None
    off_had_unknown = False
    off_duration = timedelta(0)
    previous_end = None
    for interval in intervals:
        left, right = utc(interval.start), utc(interval.end)
        if left >= right or (previous_end is not None and left != previous_end):
            raise ValueError("Intervals must be positive, ordered and contiguous")
        transition = update(left, interval.presence)
        if transition is True and off_since is not None:
            elapsed = left - off_since
            off_duration += elapsed
            if elapsed <= short_return_window:
                returns.append(left)
            return_events.append(ReturnEvent(off_since, left, elapsed <= short_return_window,
                                            off_had_unknown))
            off_since = None
            off_had_unknown = False
        elif transition is False:
            shutdowns.append(left)
            off_since = left
        # Strict inequality gives new observations/expiry and the period end priority.
        if machine.deadline is not None and machine.deadline < right:
            at = machine.deadline
            if update(at, interval.presence) is False:
                shutdowns.append(at)
                off_since = at
        if off_since is not None:
            off_intervals.append(Interval(max(left, off_since), right, interval.presence))
            off_had_unknown |= interval.presence == Presence.UNKNOWN
        previous_end = right
    if off_since is not None:
        off_duration += previous_end - off_since
    return Simulation(machine.delay, short_return_window, tuple(shutdowns), off_duration,
                      tuple(returns), off_since is not None, tuple(transitions),
                      tuple(off_intervals), tuple(return_events), machine.strategy)

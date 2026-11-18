"""Simulated constant-power model for ONE logical receiver, not socket metering."""

from dataclasses import dataclass
from datetime import timedelta
from math import isfinite
from typing import Iterable

from .core import Interval, Presence, ReturnEvent, Simulation, simulate, utc
from .strategies import Strategy


@dataclass(frozen=True)
class ReceiverModel:
    on_watts: float
    off_watts: float

    def __post_init__(self):
        for name in ("on_watts", "off_watts"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite nonnegative number")
            try:
                valid = isfinite(value) and value >= 0
            except OverflowError:
                valid = False
            if not valid:
                raise ValueError(f"{name} must be finite and nonnegative")


def watt_hours(watts: float, duration: timedelta) -> float:
    """Integrate constant power; callers supply validated powers and durations."""
    return watts * (duration.total_seconds() / 3600)


@dataclass(frozen=True)
class CategoryEnergy:
    presence: Presence
    on_duration: timedelta
    off_duration: timedelta
    baseline_wh: float
    strategy_wh: float
    difference_wh: float

    @property
    def duration(self) -> timedelta:
        return self.on_duration + self.off_duration


@dataclass(frozen=True)
class EnergyComparison:
    model: ReceiverModel
    simulation: Simulation
    categories: tuple[CategoryEnergy, ...]
    baseline_wh: float
    strategy_wh: float
    difference_wh: float
    difference_percent: float | None
    known_difference_wh: float
    unknown_difference_wh: float
    # Known includes both PRESENT and ABSENT, not just occupied time.
    known_time_share: float
    shutdown_count: int
    restart_count: int
    certain_returns: tuple[ReturnEvent, ...]
    uncertain_returns: tuple[ReturnEvent, ...]
    short_returns: tuple[ReturnEvent, ...]
    uncertain_short_returns: tuple[ReturnEvent, ...]
    simulated: bool = True

    @property
    def strategy_name(self) -> str:
        return self.simulation.strategy.name

    @property
    def configuration(self) -> dict:
        return self.simulation.strategy.configuration


def compare_energy(
    intervals: Iterable[Interval], model: ReceiverModel, delay: timedelta | Strategy,
    short_return_window: timedelta = timedelta(minutes=2),
) -> EnergyComparison:
    """Initially ON, compared with the same receiver always ON over [start, end).

    Positive difference = baseline minus strategy. UNKNOWN differences are model
    arithmetic, not evidence of reduced unnecessary consumption. Short returns
    with UNKNOWN anywhere in their OFF spell are reported separately.
    """
    timeline = tuple(Interval(utc(i.start), utc(i.end), i.presence) for i in intervals)
    if not timeline:
        raise ValueError("Energy comparison requires a nonempty analysis period")
    result = simulate(timeline, delay, short_return_window)
    categories = []
    for presence in Presence:
        duration = sum((i.end - i.start for i in timeline if i.presence == presence), timedelta(0))
        off = sum((i.end - i.start for i in result.off_intervals
                   if i.presence == presence), timedelta(0))
        on = duration - off
        baseline = watt_hours(model.on_watts, duration)
        strategy = watt_hours(model.on_watts, on) + watt_hours(model.off_watts, off)
        categories.append(CategoryEnergy(presence, on, off, baseline, strategy, baseline - strategy))
    total_seconds = sum(c.duration.total_seconds() for c in categories)
    baseline = sum(c.baseline_wh for c in categories)
    strategy = sum(c.strategy_wh for c in categories)
    difference = baseline - strategy
    known_difference = sum(c.difference_wh for c in categories if c.presence != Presence.UNKNOWN)
    unknown = next(c for c in categories if c.presence == Presence.UNKNOWN)
    certain = tuple(r for r in result.return_events if not r.uncertain)
    uncertain = tuple(r for r in result.return_events if r.uncertain)
    return EnergyComparison(
        model=model, simulation=result, categories=tuple(categories),
        baseline_wh=baseline, strategy_wh=strategy, difference_wh=difference,
        difference_percent=100 * difference / baseline if baseline != 0 else None,
        known_difference_wh=known_difference, unknown_difference_wh=unknown.difference_wh,
        known_time_share=(total_seconds - unknown.duration.total_seconds()) / total_seconds,
        shutdown_count=len(result.shutdowns), restart_count=len(result.return_events),
        certain_returns=certain, uncertain_returns=uncertain,
        short_returns=tuple(r for r in certain if r.is_short),
        uncertain_short_returns=tuple(r for r in uncertain if r.is_short),
    )

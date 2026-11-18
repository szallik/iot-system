"""Exhaustive offline parameter fitting with global constraints, no prediction."""

from dataclasses import asdict, dataclass
from datetime import timedelta
from itertools import product
from math import prod
from typing import Iterable
from zoneinfo import ZoneInfo

from .contexts import AdaptiveDelay, ContextPartition, default_contexts
from .core import Interval, Presence, simulate
from .energy import ReceiverModel
from .recommendation import Constraints, Recommendation, _prepare, evaluate_later, recommend
from .strategies import positive_delay


@dataclass(frozen=True)
class DataRequirements:
    min_days: int = 3
    min_complete_episodes: int = 3
    min_known_time_per_day: timedelta = timedelta(minutes=30)

    def __post_init__(self):
        for value in (self.min_days, self.min_complete_episodes):
            if type(value) is not int or value < 0:
                raise ValueError("Minimum counts must be nonnegative integers")
        positive_delay(self.min_known_time_per_day)


@dataclass(frozen=True)
class GroupEvidence:
    name: str
    reliable_dates: tuple[str, ...]
    complete_episodes: int
    known_seconds: float
    fallback_reasons: tuple[str, ...]

    @property
    def uses_default(self):
        return bool(self.fallback_reasons)


@dataclass(frozen=True)
class AdaptiveTraining:
    selection: Recommendation | None
    contexts: ContextPartition
    evidence: tuple[GroupEvidence, ...]
    requirements: DataRequirements
    default_delay: timedelta
    choices: tuple[timedelta, ...]
    model: ReceiverModel
    constraints: Constraints
    short_return_window: timedelta
    energy_tolerance_wh: float
    reasons: tuple[str, ...]

    @property
    def policy(self) -> AdaptiveDelay | None:
        return self.selection.selected.policy if self.selection and self.selection.selected else None

    def configuration(self) -> dict:
        """JSON-ready reproducible training artifact; no mutable references escape."""
        periods = self.selection.periods if self.selection else ()
        return {
            "schema_version": 1, "method": "exhaustive_history_parameter_selection",
            "status": "recommended" if self.policy else "no_recommendation",
            "strategy": self.policy.configuration if self.policy else None,
            "timezone": self.contexts.timezone_name,
            "training_periods_utc": [[start.isoformat(), end.isoformat()] for start, end in periods],
            "training_duration_seconds": self.selection.duration.total_seconds() if self.selection else 0,
            "receiver_model_watts": asdict(self.model), "constraints": asdict(self.constraints),
            "candidate_delays_seconds": [d.total_seconds() for d in self.choices],
            "default_delay_seconds": self.default_delay.total_seconds(),
            "short_return_window_seconds": self.short_return_window.total_seconds(),
            "energy_tolerance_wh": self.energy_tolerance_wh,
            "requirements": {"min_days": self.requirements.min_days,
                             "min_complete_episodes": self.requirements.min_complete_episodes,
                             "min_known_seconds_per_day": self.requirements.min_known_time_per_day.total_seconds()},
            "groups": [dict(g.configuration, reliable_dates=list(e.reliable_dates),
                            reliable_days=len(e.reliable_dates), complete_episodes=e.complete_episodes,
                            known_seconds=e.known_seconds, uses_default=e.uses_default,
                            fallback_reasons=list(e.fallback_reasons),
                            delay_seconds=self.policy.delays[index].total_seconds() if self.policy else None)
                       for index, (g, e) in enumerate(zip(self.contexts.groups, self.evidence))],
            "evaluated_configurations": len(self.selection.candidates) if self.selection else 0,
            "reasons": list(self.reasons), "energy_is_simulated": True,
        }


def group_evidence(periods, contexts: ContextPartition, requirements: DataRequirements):
    daily = [dict() for _ in contexts.groups]
    episodes = [0 for _ in contexts.groups]
    zone = ZoneInfo(contexts.timezone_name)
    for period in periods:
        # Validate continuity/time order using the same simulator, never infer over gaps.
        simulate(period, timedelta(minutes=1))
        merged = []
        for interval in period:
            if merged and merged[-1].presence == interval.presence:
                merged[-1] = Interval(merged[-1].start, interval.end, interval.presence)
            else:
                merged.append(interval)
            if interval.presence == Presence.UNKNOWN:
                continue
            cursor = interval.start
            # Context boundaries have minute resolution. UTC slicing also handles DST folds/gaps.
            while cursor < interval.end:
                right = min(cursor.replace(second=0, microsecond=0) + timedelta(minutes=1), interval.end)
                index = contexts.index_at(cursor)
                date = cursor.astimezone(zone).date().isoformat()
                daily[index][date] = daily[index].get(date, 0) + (right - cursor).total_seconds()
                cursor = right
        for index in range(1, len(merged) - 1):
            previous, current, following = merged[index - 1:index + 2]
            if (previous.presence == following.presence == Presence.PRESENT
                    and current.presence == Presence.ABSENT):
                episodes[contexts.index_at(current.start)] += 1
    evidence = []
    for group, days, count in zip(contexts.groups, daily, episodes):
        dates = tuple(sorted(date for date, seconds in days.items()
                             if seconds >= requirements.min_known_time_per_day.total_seconds()))
        reasons = []
        if not days:
            reasons.append("Brak wiarygodnych danych w grupie")
        if len(dates) < requirements.min_days:
            reasons.append(f"Wiarygodne dni: {len(dates)} < {requirements.min_days}")
        if count < requirements.min_complete_episodes:
            reasons.append(f"Kompletne epizody: {count} < {requirements.min_complete_episodes}")
        evidence.append(GroupEvidence(group.name, dates, count, sum(days.values()), tuple(reasons)))
    return tuple(evidence)


def train_adaptive(
    periods: Iterable[Iterable[Interval]], model: ReceiverModel, constraints: Constraints, *,
    contexts: ContextPartition | None = None,
    choices: Iterable[timedelta] = tuple(timedelta(minutes=m) for m in (1, 2, 5, 10, 15)),
    requirements: DataRequirements = DataRequirements(), default_delay: timedelta = timedelta(minutes=5),
    short_return_window: timedelta = timedelta(minutes=2), energy_tolerance_wh: float = 1e-6,
    max_configurations: int = 10000,
) -> AdaptiveTraining:
    contexts = contexts if contexts is not None else default_contexts()
    positive_delay(default_delay)
    options = tuple(sorted(set(choices)))
    if not options:
        raise ValueError("Require candidate delays")
    for delay in options:
        positive_delay(delay)
    if type(max_configurations) is not int or max_configurations <= 0:
        raise ValueError("max_configurations must be positive")
    raw = tuple(tuple(p) for p in periods)
    data = _prepare(raw) if raw else ()
    evidence = group_evidence(data, contexts, requirements)
    per_group = tuple((default_delay,) if e.uses_default else options for e in evidence)
    if prod(map(len, per_group)) > max_configurations:
        raise ValueError("Search exceeds max_configurations; explicitly reduce the configuration space")
    selection = recommend(data, model, (AdaptiveDelay(contexts, delays) for delays in product(*per_group)),
                          constraints, short_return_window, energy_tolerance_wh) if data else None
    reasons = selection.reasons if selection else ("Brak rekomendacji: brak okresu treningowego.",)
    return AdaptiveTraining(selection, contexts, evidence, requirements, default_delay, options,
                            model, constraints, short_return_window, energy_tolerance_wh, reasons)


def evaluate_adaptive(training: AdaptiveTraining, periods, constraints: Constraints):
    """Freeze all context delays, evidence and training metadata; never refit."""
    if training.selection is None or training.policy is None:
        raise ValueError("No trained configuration to evaluate")
    return evaluate_later(training.selection, periods, constraints=constraints)

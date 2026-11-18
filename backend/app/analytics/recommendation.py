"""Offline constrained selection; holdout evaluation never selects again."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Iterable

from .core import Interval, Presence, utc
from .energy import ReceiverModel, compare_energy
from .strategies import FixedDelay, Strategy, as_strategy, strategy_id


@dataclass(frozen=True)
class Constraints:
    max_short_returns: int
    max_shutdowns: int
    min_known_share: float
    max_uncertain_returns: int

    def __post_init__(self):
        for name in ("max_short_returns", "max_shutdowns", "max_uncertain_returns"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if (isinstance(self.min_known_share, bool)
                or not isinstance(self.min_known_share, (int, float))
                or not isfinite(self.min_known_share) or not 0 <= self.min_known_share <= 1):
            raise ValueError("min_known_share must be finite and in [0, 1]")


@dataclass(frozen=True)
class Candidate:
    delay: timedelta | None
    known_energy_wh: float
    unknown_energy_wh: float
    shutdowns: int
    short_returns: int
    uncertain_returns: int
    known_share: float
    rejection_reasons: tuple[str, ...]
    strategy: Strategy | None = None

    @property
    def policy(self) -> Strategy:
        return self.strategy if self.strategy is not None else as_strategy(self.delay)

    @property
    def strategy_name(self) -> str:
        return self.policy.name

    @property
    def configuration(self) -> dict:
        return self.policy.configuration

    @property
    def identifier(self) -> str:
        return strategy_id(self.policy)

    @property
    def admissible(self) -> bool:
        return not self.rejection_reasons


@dataclass(frozen=True)
class Recommendation:
    candidates: tuple[Candidate, ...]
    ranking: tuple[Candidate, ...]  # Admissible only, best first.
    selected: Candidate | None
    reasons: tuple[str, ...]
    periods: tuple[tuple[datetime, datetime], ...]
    duration: timedelta  # Sum of evaluated periods; excludes gaps between them.
    model: ReceiverModel
    constraints: Constraints
    short_return_window: timedelta
    energy_tolerance_wh: float


@dataclass(frozen=True)
class HoldoutEvaluation:
    candidate: Candidate
    periods: tuple[tuple[datetime, datetime], ...]
    duration: timedelta


def _prepare(periods: Iterable[Iterable[Interval]]):
    data = tuple(tuple(Interval(utc(i.start), utc(i.end), i.presence) for i in period)
                 for period in periods)
    if not data or any(not period for period in data):
        raise ValueError("Require nonempty evaluation periods")
    data = tuple(sorted(data, key=lambda p: p[0].start))
    for previous, current in zip(data, data[1:]):
        if previous[-1].end > current[0].start:
            raise ValueError("Evaluation periods must not overlap")
    return data


def _metadata(data):
    bounds = tuple((p[0].start, p[-1].end) for p in data)
    return bounds, sum((end - start for start, end in bounds), timedelta(0))


def _assess(data, model, delay, constraints, short_return_window):
    strategy = as_strategy(delay)
    results = tuple(compare_energy(p, model, strategy, short_return_window) for p in data)
    categories = tuple(c for r in results for c in r.categories)
    known = sum(c.duration.total_seconds() for c in categories if c.presence != Presence.UNKNOWN)
    total = sum(c.duration.total_seconds() for c in categories)
    share = known / total
    short = sum(len(r.short_returns) for r in results)
    shutdowns = sum(r.shutdown_count for r in results)
    uncertain = sum(len(r.uncertain_returns) for r in results)
    reasons = []
    if short > constraints.max_short_returns:
        reasons.append(f"Pewne krotkie powroty: {short} > limit {constraints.max_short_returns}")
    if shutdowns > constraints.max_shutdowns:
        reasons.append(f"Wylaczenia: {shutdowns} > limit {constraints.max_shutdowns}")
    if share < constraints.min_known_share:
        reasons.append(f"Udzial znanych danych: {share:.6%} < minimum {constraints.min_known_share:.6%}")
    if uncertain > constraints.max_uncertain_returns:
        reasons.append(f"Niepewne powroty: {uncertain} > limit {constraints.max_uncertain_returns}")
    return Candidate(
        strategy.delay if isinstance(strategy, FixedDelay) else None,
        sum(c.strategy_wh for c in categories if c.presence != Presence.UNKNOWN),
        sum(c.strategy_wh for c in categories if c.presence == Presence.UNKNOWN),
        shutdowns, short, uncertain, share, tuple(reasons), strategy,
    )


def rank_candidates(candidates: Iterable[Candidate], tolerance_wh: float) -> tuple[Candidate, ...]:
    """Tie groups anchored at the lowest remaining known energy, never UNKNOWN.

    Anchoring avoids nontransitive pairwise approximate comparisons. Within each
    group use certain short returns, shutdowns, then descending minimum configured
    delay (the fixed delay for fixed policies), then canonical strategy identity.
    """
    if (isinstance(tolerance_wh, bool) or not isinstance(tolerance_wh, (int, float))
            or not isfinite(tolerance_wh) or tolerance_wh < 0):
        raise ValueError("Energy tolerance must be finite and nonnegative")
    remaining = [c for c in candidates if c.admissible]
    ranked = []
    while remaining:
        lowest = min(c.known_energy_wh for c in remaining)
        tied = [c for c in remaining if c.known_energy_wh - lowest <= tolerance_wh]
        ranked.extend(sorted(tied, key=lambda c: (
            c.short_returns, c.shutdowns, -c.policy.minimum_delay.total_seconds(), c.identifier)))
        remaining = [c for c in remaining if c not in tied]
    return tuple(ranked)


def recommend(
    periods: Iterable[Iterable[Interval]], model: ReceiverModel,
    delays: Iterable[timedelta | Strategy], constraints: Constraints,
    short_return_window: timedelta = timedelta(minutes=2),
    energy_tolerance_wh: float = 1e-6,
) -> Recommendation:
    data = _prepare(periods)
    options = tuple(sorted(set(as_strategy(d) for d in delays),
                           key=lambda s: (s.minimum_delay, strategy_id(s))))
    if not options:
        raise ValueError("Require at least one positive delay")
    candidates = tuple(_assess(data, model, delay, constraints, short_return_window) for delay in options)
    ranking = rank_candidates(candidates, energy_tolerance_wh)
    selected = ranking[0] if ranking else None
    if selected is None:
        reasons = ("Brak rekomendacji: zaden wariant nie spelnia ograniczen.",) + tuple(
            f"{c.identifier}: {'; '.join(c.rejection_reasons)}" for c in candidates)
    else:
        reasons = (
            f"Wybrano {selected.identifier}: spelnia wszystkie ograniczenia.",
            f"Minimalna energia w znanych okresach z tolerancja {energy_tolerance_wh:g} Wh; "
            "remisy: mniej pewnych krotkich powrotow, mniej wylaczen, dluzsze minimalne "
            "opoznienie konfiguracji, identyfikator strategii.",
            "Energia UNKNOWN nie uczestniczy w rankingu.",
        )
    bounds, duration = _metadata(data)
    return Recommendation(candidates, ranking, selected, reasons, bounds, duration,
                          model, constraints, short_return_window, energy_tolerance_wh)


def assess_strategy(periods: Iterable[Iterable[Interval]], model: ReceiverModel,
                    strategy: timedelta | Strategy, constraints: Constraints,
                    short_return_window: timedelta = timedelta(minutes=2)) -> HoldoutEvaluation:
    """Assess one frozen policy globally, without ranking or parameter selection."""
    data = _prepare(periods)
    candidate = _assess(data, model, strategy, constraints, short_return_window)
    bounds, duration = _metadata(data)
    return HoldoutEvaluation(candidate, bounds, duration)


def evaluate_later(selection: Recommendation, periods: Iterable[Iterable[Interval]], *,
                   constraints: Constraints | None = None) -> HoldoutEvaluation:
    """Evaluate the entire frozen strategy, with the original model and limits."""
    if selection.selected is None:
        raise ValueError("Cannot evaluate without a recommendation")
    data = _prepare(periods)
    if data[0][0].start < max(end for _, end in selection.periods):
        raise ValueError("Holdout periods must be later than all selection periods")
    candidate = _assess(data, selection.model, selection.selected.policy,
                        selection.constraints if constraints is None else constraints,
                        selection.short_return_window)
    bounds, duration = _metadata(data)
    return HoldoutEvaluation(candidate, bounds, duration)

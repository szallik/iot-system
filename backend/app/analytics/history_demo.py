"""Seeded multi-day history. The generator never reads candidate strategies."""

from datetime import datetime, timedelta, timezone
from random import Random
from zoneinfo import ZoneInfo

from .core import Report
from .scenarios import Scenario


def history_scenarios(seed: int = 20260105) -> tuple[Scenario, Scenario]:
    rng = Random(seed)
    zone = ZoneInfo("Europe/Warsaw")
    start = datetime(2026, 1, 5, tzinfo=zone).astimezone(timezone.utc)
    absent = set()
    missing = set()
    for day in range(21):
        # Variable breaks in every context, plus nights crossing local dates/context boundaries.
        for nominal in (390, 555, 660, 825, 945, 1065, 1185):
            beginning = day * 1440 + nominal + rng.randint(-12, 12)
            duration = rng.choice((2, 3, 4, 6, 8, 12, 20, 35))
            absent.update(range(beginning, beginning + duration))
        night = day * 1440 + 1320 + rng.randint(-10, 10)
        absent.update(range(night, (day + 1) * 1440 + 360))
        if day % 3 == 1:
            missing.update(range(day * 1440 + 865, day * 1440 + 880))
        if day % 4 == 2:
            missing.update(range(day * 1440 + 1330, day * 1440 + 1355))
    reports = tuple(Report(start + timedelta(minutes=m), m not in absent)
                    for m in range(21 * 1440) if m not in missing)
    split = start + timedelta(days=14)
    end = start + timedelta(days=21)
    # Include a pre-window report for freshness only; no future reports in training.
    training = Scenario("history_training_14d", "14 dni syntetyczne, ziarno 20260105.",
                        tuple(r for r in reports if r.at < split), start, split)
    holdout = Scenario("history_evaluation_7d", "Kolejne 7 dni syntetyczne, bez strojenia.",
                       tuple(r for r in reports if r.at >= split - timedelta(minutes=2)), split, end)
    return training, holdout

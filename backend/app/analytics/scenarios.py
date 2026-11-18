"""One-hour synthetic scenarios, one report/minute, demo freshness of 2 minutes."""

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from .core import Interval, Report, reconstruct


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    reports: tuple[Report, ...]
    start: datetime
    end: datetime
    freshness: timedelta = timedelta(minutes=2)

    def intervals(self) -> tuple[Interval, ...]:
        return reconstruct(self.reports, self.start, self.end, self.freshness)


def energy_scenarios() -> tuple[Scenario, ...]:
    start = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    definitions = (
        ("krotkie_przerwy", "Nieobecnosc [05,08), [15,18), [25,28), [35,38), [45,48), [55,58).",
         lambda m: not 5 <= m % 10 < 8, lambda m: True),
        ("dluga_nieobecnosc", "Nieobecnosc [10,50); pozostaly czas obecnosc.",
         lambda m: not 10 <= m < 50, lambda m: True),
        ("stala_obecnosc", "Obecnosc przez cala godzine.",
         lambda m: True, lambda m: True),
        ("luki_danych", "Nieobecnosc [10,35); brak raportow [01,05) i [21,30). "
         "UNKNOWN [02,05) podczas ON i [22,30) podczas OFF dla opoznien 2, 5, 10 min.",
         lambda m: not 10 <= m < 35, lambda m: not (1 <= m < 5 or 21 <= m < 30)),
    )
    return tuple(
        Scenario(name, description,
                 tuple(Report(start + timedelta(minutes=m), occupied(m))
                       for m in range(60) if reporting(m)),
                 start, start + timedelta(hours=1))
        for name, description, occupied, reporting in definitions
    )


def recommendation_scenarios() -> tuple[tuple[Scenario, ...], tuple[Scenario, ...]]:
    """Disjoint earlier selection set and different, later holdout data."""
    training = tuple(
        replace(case, start=case.start + timedelta(days=index),
                end=case.end + timedelta(days=index),
                reports=tuple(Report(r.at + timedelta(days=index), r.occupied) for r in case.reports))
        for index, case in enumerate(energy_scenarios())
    )
    later = []
    for day, name, description, occupied, reporting in (
        (5, "pozniejsze_przerwy", "Nieobecnosc [05,09), [15,19), [25,29), [35,39), [45,49), [55,59).",
         lambda m: not 5 <= m % 10 < 9, lambda m: True),
        (6, "pozniejsza_luka", "Nieobecnosc [05,50); brak raportow [25,40), UNKNOWN [26,40).",
         lambda m: not 5 <= m < 50, lambda m: not 25 <= m < 40),
    ):
        start = datetime(2026, 1, day, 8, tzinfo=timezone.utc)
        later.append(Scenario(name, description,
                              tuple(Report(start + timedelta(minutes=m), occupied(m))
                                    for m in range(60) if reporting(m)),
                              start, start + timedelta(hours=1)))
    return training, tuple(later)


def schedule_scenarios(*, later: bool = False) -> tuple[Scenario, ...]:
    """Work hours/boundary, evening and weekend; separate later synthetic sample."""
    from zoneinfo import ZoneInfo

    zone = ZoneInfo("Europe/Warsaw")
    day = 16 if later else 9  # Fridays in January 2026; weekend follows.
    definitions = (
        ("praca_i_granica", day, 15, 120,
         ((5, 20), (55, 68), (95, 110)) if later else ((5, 18), (55, 72), (100, 115))),
        ("wieczor", day, 18, 60,
         ((5, 10), (25, 50)) if later else ((5, 8), (20, 50))),
        ("weekend", day + 1, 10, 60,
         ((10, 20), (35, 58)) if later else ((8, 16), (30, 55))),
    )
    cases = []
    for name, date, hour, length, absences in definitions:
        start = datetime(2026, 1, date, hour, tzinfo=zone).astimezone(timezone.utc)
        cases.append(Scenario(name, f"Nieobecnosc w minutach od poczatku: {absences}.",
                              tuple(Report(start + timedelta(minutes=m),
                                           not any(left <= m < right for left, right in absences))
                                    for m in range(length)), start, start + timedelta(minutes=length)))
    return tuple(cases)

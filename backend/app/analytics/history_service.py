"""Shared history simulation and serialization, without CLI or application startup."""
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from math import isfinite
from .core import reconstruct
from .energy import compare_energy


def jsonable(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return str(value)
    if is_dataclass(value):
        return {f.name: jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [jsonable(item) for item in value]
    return value


def seconds(value):
    number = float(value)
    if not isfinite(number) or number <= 0:
        raise ValueError("Czas musi byc skonczony i dodatni")
    result = timedelta(seconds=number)
    if result <= timedelta(0):
        raise ValueError("Czas musi wynosic co najmniej mikrosekunde")
    return result


def simulate_import(imported, *, start, end, ttl, strategy, model, short_return_window):
    reports = imported.presence_reports()
    intervals = reconstruct(reports, start, end, ttl)
    energy = compare_energy(intervals, model, strategy, short_return_window)
    previous = next((report for report in reversed(reports) if report.at < start), None)
    return {
        "energy_simulated": True,
        "energy": {f.name: getattr(energy, f.name) for f in fields(energy)
                   if f.name not in ("simulation", "model")},
        "presence_intervals": intervals,
        "last_pre_start_report": previous,
        "pre_start_report_is_fresh": previous is not None and previous.at + ttl > start,
        "simulation": {"initial_power_state": "ON", "off_at_end": energy.simulation.off_at_end,
                       "transitions": energy.simulation.transitions,
                       "off_intervals": energy.simulation.off_intervals,
                       "off_duration_seconds": energy.simulation.off_duration.total_seconds()},
    }

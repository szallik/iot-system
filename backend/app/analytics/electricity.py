"""Shared observed counter increments and the whole-power-strip entry point."""
from decimal import Decimal
from math import isfinite

from .core import utc


def finite_float(value):
    result = float(value)
    if not isfinite(result):
        raise OverflowError("Wynik energii lub kosztu przekracza zakres liczbowy")
    return result


def analyze_meter(energy, power, *, start, end, price_per_kwh=None, scope="whole_power_strip"):
    start, end = utc(start), utc(end)
    if start >= end or energy.metric != "energy" or power.metric != "power" or energy.device_id != power.device_id:
        raise ValueError("Wymagany poprawny okres i energy/power jednego urzadzenia")
    for history in (energy, power):
        if history.quality["blocking_reasons"]:
            raise ValueError("Analiza zablokowana: " + "; ".join(history.quality["blocking_reasons"]))
    price = Decimal(str(price_per_kwh)) if price_per_kwh is not None else None
    if price is not None and (not price.is_finite() or price < 0):
        raise ValueError("Cena musi byc skonczona i nieujemna")
    readings = sorted((r for r in energy.rows if start <= r.at <= end), key=lambda r: r.at)
    powers = sorted((r for r in power.rows if start <= r.at < end), key=lambda r: r.at)
    accepted, drops = [], []
    total = Decimal(0)
    for left, right in zip(readings, readings[1:]):
        increment = right.value - left.value
        pair = {"start": left.at, "end": right.at, "duration_seconds": (right.at-left.at).total_seconds(),
                "start_kwh": finite_float(left.value), "end_kwh": finite_float(right.value)}
        if increment < 0:
            drops.append(dict(pair, reason="counter_reset_or_anomaly"))
        else:
            total += increment
            accepted.append(dict(pair, increment_kwh=finite_float(increment)))
    covered = sum(p["duration_seconds"] for p in accepted)
    peak = max(powers, key=lambda r: r.value) if powers else None  # earliest timestamp on ties
    return {
        "energy_simulated": False, "scope": scope, "energy_unit": "kWh", "power_unit": "W",
        "energy_kwh": finite_float(total) if accepted else None,
        "estimated_cost": finite_float(total * price) if accepted and price is not None else None,
        "price_per_kwh": price_per_kwh,
        "max_power_w": finite_float(peak.value) if peak else None, "max_power_at": peak.at if peak else None,
        "energy_readings_count": len(readings), "power_readings_count": len(powers),
        "accepted_pairs_count": len(accepted), "skipped_pairs_count": len(drops), "counter_drops_count": len(drops),
        "observed_energy_bounds": {"start": readings[0].at, "end": readings[-1].at} if readings else None,
        "used_energy_bounds": {"start": accepted[0]["start"], "end": accepted[-1]["end"]} if accepted else None,
        "covered_seconds": covered, "uncovered_seconds": (end-start).total_seconds()-covered,
        "duration_seconds": (end-start).total_seconds(), "accepted_pairs": accepted, "skipped_pairs": drops,
        "assumptions": [
            "Zakladamy ciaglosc licznika miedzy odczytami bez zaobserwowanego spadku; ukrytych resetow nie mozna wykluczyc.",
            "Dluga przerwa nie uniewaznia przyrostu, ale nie odtwarza przebiegu zuzycia wewnatrz pary.",
            "Bez interpolacji granic, odczytow sprzed zakresu i szacowania energii przy spadku licznika.",
            "Koszt obejmuje tylko zaakceptowana energie wedlug jednej stawki, bez oplat stalych i zalozonej waluty.",
            ("Wynik dotyczy cieplomierza i energii cieplnej, nie elektrycznej." if scope == "heat_meter" else
             "Wynik dotyczy calej listwy, nie gniazd; nie opisuje oszczednosci."),
        ],
    }


def analyze_electricity(energy, power, *, start, end, price_per_kwh=None):
    return analyze_meter(energy, power, start=start, end=end, price_per_kwh=price_per_kwh)

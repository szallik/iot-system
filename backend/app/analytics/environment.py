"""Historical step-function statistics; no interpolation or device control."""
from datetime import timedelta
from decimal import Decimal

from .core import utc
from .measurement_history import EXPECTED_UNITS
from .freshness import valid_segments


def analyze_environment(imported, *, start, end, ttl, lower_threshold=None, upper_threshold=None):
    start, end = utc(start), utc(end)
    if imported.metric not in ("temperature", "humidity"):
        raise ValueError("Wymagana metryka temperature lub humidity")
    if start >= end or ttl <= timedelta(0):
        raise ValueError("Wymagany poprawny zakres i dodatni TTL")
    if imported.quality["blocking_reasons"]:
        raise ValueError("Analiza zablokowana: " + "; ".join(imported.quality["blocking_reasons"]))
    lower = Decimal(str(lower_threshold)) if lower_threshold is not None else None
    upper = Decimal(str(upper_threshold)) if upper_threshold is not None else None
    if any(t is not None and not t.is_finite() for t in (lower, upper)):
        raise ValueError("Progi musza byc skonczone")
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("Dolny prog nie moze przekraczac gornego")
    rows = sorted(imported.rows, key=lambda row: row.at)
    readings = [row.value for row in rows if start <= row.at < end]
    known = below = within = above = timedelta(0)
    weighted_sum = Decimal(0)
    thresholds = lower is not None or upper is not None
    for left, right, value in valid_segments(rows, start=start, end=end, ttl=ttl):
        duration = right - left
        known += duration
        weighted_sum += value * Decimal(str(duration.total_seconds()))
        if thresholds:
            if lower is not None and value < lower:
                below += duration
            elif upper is not None and value > upper:
                above += duration
            else:
                within += duration
    duration = end - start
    return {
        "unit": EXPECTED_UNITS[imported.metric],
        "valid_readings_count": len(readings),
        "minimum": float(min(readings)) if readings else None,
        "maximum": float(max(readings)) if readings else None,
        "time_weighted_mean": float(weighted_sum / Decimal(str(known.total_seconds()))) if known else None,
        "duration_seconds": duration.total_seconds(), "known_seconds": known.total_seconds(),
        "unknown_seconds": (duration - known).total_seconds(),
        "coverage_percent": 100 * known / duration,
        "below_seconds": below.total_seconds() if thresholds else None,
        "within_seconds": within.total_seconds() if thresholds else None,
        "above_seconds": above.total_seconds() if thresholds else None,
        "thresholds_evaluated": thresholds,
    }

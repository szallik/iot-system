"""Finite validity intervals of sampled values, without filling gaps."""
from datetime import timedelta
from decimal import Decimal

from .core import utc


def valid_segments(rows, *, start, end, ttl):
    start, end = utc(start), utc(end)
    if start >= end or ttl <= timedelta(0):
        raise ValueError("Wymagany poprawny zakres i dodatni TTL")
    rows = sorted(rows, key=lambda row: row.at)
    for index, row in enumerate(rows):
        if row.at >= end:
            break
        left = max(start, row.at)
        right = row.at + min(ttl, end - row.at)
        if index + 1 < len(rows):
            right = min(right, rows[index + 1].at)
        if right > left:
            yield left, right, row.value


def time_summary(segments, *, start, end):
    segments = tuple(segments)
    known = sum((right-left for left, right, _ in segments), timedelta())
    weighted = sum((value * Decimal(str((right-left).total_seconds()))
                    for left, right, value in segments), Decimal(0))
    return {
        "time_weighted_mean": float(weighted / Decimal(str(known.total_seconds()))) if known else None,
        "known_seconds": known.total_seconds(), "unknown_seconds": (end-start-known).total_seconds(),
        "coverage_percent": 100 * known / (end-start),
    }


def difference_segments(left_segments, right_segments):
    """Intersect independent timelines; expiry on either side ends joint validity."""
    left_segments, right_segments = tuple(left_segments), tuple(right_segments)
    i = j = 0
    while i < len(left_segments) and j < len(right_segments):
        a, b = left_segments[i], right_segments[j]
        start, end = max(a[0], b[0]), min(a[1], b[1])
        if start < end:
            yield start, end, a[2] - b[2]
        if a[1] <= b[1]:
            i += 1
        else:
            j += 1

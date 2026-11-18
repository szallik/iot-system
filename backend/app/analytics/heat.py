"""Heat-meter history: cumulative counter plus observed sample summaries."""
from .electricity import analyze_meter, finite_float
from .measurement_history import EXPECTED_UNITS
from .freshness import valid_segments, time_summary, difference_segments

HEAT_METRICS = ("energy", "power", "temperature_1", "temperature_2", "flow")


def summarize_heat(histories, *, start, end, ttl):
    result = analyze_meter(histories["energy"], histories["power"], start=start, end=end, scope="heat_meter")
    result.pop("estimated_cost")
    result.pop("price_per_kwh")
    result["assumptions"] = [note for note in result["assumptions"] if not note.startswith("Koszt")]
    summaries = {}
    timelines = {}
    for metric in HEAT_METRICS[1:]:
        history = histories[metric]
        if history.quality["blocking_reasons"]:
            raise ValueError("Niepoprawne dane " + metric)
        rows = sorted((r for r in history.rows if start <= r.at < end), key=lambda r: r.at)
        timelines[metric] = tuple(valid_segments(history.rows, start=start, end=end, ttl=ttl))
        summaries[metric] = {
            "unit": EXPECTED_UNITS[metric], "readings_count": len(rows),
            "minimum": finite_float(min(r.value for r in rows)) if rows else None,
            "maximum": finite_float(max(r.value for r in rows)) if rows else None,
            "first_at": rows[0].at if rows else None, "last_at": rows[-1].at if rows else None,
            "last_value": finite_float(rows[-1].value) if rows else None,
            **time_summary(timelines[metric], start=start, end=end),
        }
    difference = tuple(difference_segments(timelines["temperature_1"], timelines["temperature_2"]))
    delta = dict(time_summary(difference, start=start, end=end), unit="°C",
                 minimum=finite_float(min(v for _, _, v in difference)) if difference else None,
                 maximum=finite_float(max(v for _, _, v in difference)) if difference else None,
                 negative_seconds=sum((b-a).total_seconds() for a, b, v in difference if v < 0) if difference else None)
    result["assumptions"].append("Ujemna roznica temperature_1 - temperature_2 jest obserwacja, nie diagnoza awarii.")
    return dict(result, energy_kind="thermal", measurements=summaries, ttl_seconds=ttl.total_seconds(),
                temperature_difference=delta)

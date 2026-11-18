"""Shared measurement validation and quality reporting for offline data adapters."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import ceil, isfinite
from statistics import mean, median

from .core import Report, utc


COLUMNS = ("id", "device_id", "metric", "value", "unit", "ts")
EXPECTED_UNITS = {"occupancy": "bool", "temperature": "°C", "humidity": "%", "energy": "kWh", "power": "W"}
EXPECTED_UNITS.update(temperature_1="°C", temperature_2="°C", flow="l/min")
REPORTING_NOTE = (
    "Odstepy miedzy komunikatami nie rozstrzygaja, czy czujnik raportuje cyklicznie "
    "czy przy zmianie stanu. Przerwa nie dowodzi utraty lacznosci ani nieobecnosci. "
    "TTL wymaga jawnego ustalenia na podstawie sposobu raportowania urzadzenia."
)


def parse_timestamp(value: str) -> datetime:
    try:
        return utc(datetime.fromisoformat(value.strip()))
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError("Wymagany poprawny znacznik ISO 8601 ze strefa czasowa") from error


@dataclass(frozen=True)
class MeasurementRow:
    record: int  # CSV record number, header = 1; can span physical lines.
    at: datetime
    value: Decimal
    unit: str


@dataclass(frozen=True)
class ImportedHistory:
    source: dict
    device_id: str
    metric: str
    rows: tuple[MeasurementRow, ...]  # Unique, valid, nonconflicting observations.
    quality: dict

    def presence_reports(self) -> tuple[Report, ...]:
        if self.metric != "occupancy":
            raise ValueError("Symulacja obecnosci wymaga metric=occupancy")
        if self.quality["blocking_reasons"]:
            raise ValueError("Symulacja zablokowana: " + "; ".join(self.quality["blocking_reasons"]))
        return tuple(Report(row.at, row.value == 1) for row in self.rows)


def spacing_statistics(timestamps):
    times = sorted(set(timestamps))
    gaps = [{"start": left.isoformat(), "end": right.isoformat(), "seconds": (right-left).total_seconds()}
            for left, right in zip(times, times[1:])]
    values = sorted(gap["seconds"] for gap in gaps)
    def percentile(p):
        return values[ceil(p * len(values)) - 1] if values else None
    return {
        "basis": "distinct valid selected timestamps, including conflicting timestamps; no TTL interpretation",
        "count": len(values), "min_seconds": min(values) if values else None,
        "max_seconds": max(values) if values else None,
        "mean_seconds": mean(values) if values else None, "median_seconds": median(values) if values else None,
        "p90_seconds": percentile(.90), "p95_seconds": percentile(.95),
        "percentile_method": "nearest_rank",
        "histogram": {"(0,60]": sum(v <= 60 for v in values),
                      "(60,300]": sum(60 < v <= 300 for v in values),
                      "(300,900]": sum(300 < v <= 900 for v in values),
                      "(900,infinity)": sum(v > 900 for v in values)},
        "largest_gaps": sorted(gaps, key=lambda g: (-g["seconds"], g["start"]))[:10],
    }


def validate_measurements(records, *, device_id: str, metric: str, source: dict,
                          structural_issues=(), parse_complete=True) -> ImportedHistory:
    if not device_id.strip() or not metric.strip():
        raise ValueError("device_id i metric nie moga byc puste")
    records = tuple(records)
    malformed = len(structural_issues)
    input_count = len(records) + malformed
    selected = filtered = invalid_rows = 0
    issues, valid = list(structural_issues), []
    complete = parse_complete
    for record, raw in records:
        row = {key: "" if raw.get(key) is None else str(raw[key]) for key in COLUMNS}
        if row["device_id"] != device_id or row["metric"] != metric:
            filtered += 1
            continue
        selected += 1
        errors = []
        try:
            at = parse_timestamp(row["ts"])
        except ValueError:
            errors.append(("invalid_timestamp", row["ts"]))
        try:
            value = Decimal(row["value"].strip())
            if not value.is_finite() or not isfinite(float(value)):
                raise ValueError()
            if metric in ("flow", "energy") and value < 0:
                raise ValueError()
            if metric == "humidity" and not 0 <= value <= 100:
                raise ValueError()
            if metric == "occupancy" and value not in (Decimal(0), Decimal(1)):
                raise ValueError()
        except (InvalidOperation, ValueError, OverflowError):
            errors.append(("invalid_value", row["value"]))
        if not row["id"].isascii() or not row["id"].isdigit() or not row["id"].strip("0"):
            errors.append(("invalid_id", row["id"]))
        if not row["unit"].strip() or (metric in EXPECTED_UNITS and row["unit"] != EXPECTED_UNITS[metric]):
            errors.append(("invalid_unit", row["unit"]))
        if errors:
            invalid_rows += 1
            issues.extend({"record": record, "code": code, "detail": detail} for code, detail in errors)
        else:
            valid.append(MeasurementRow(record, at, value, row["unit"]))
    by_time = {}
    for row in valid:
        by_time.setdefault(row.at, []).append(row)
    accepted, duplicates, conflicts = [], [], []
    conflict_rows = duplicate_rows = 0
    for at, rows in sorted(by_time.items()):
        seen = {}
        for row in rows:
            key = (row.value, row.unit)
            if key in seen:
                duplicates.append({"record": row.record, "duplicate_of": seen[key].record, "at": at.isoformat()})
            else:
                seen[key] = row
        if len(seen) > 1:
            conflict_rows += len(rows)
            conflicts.append({"at": at.isoformat(), "records": [r.record for r in rows],
                              "observations": [{"value": str(value), "unit": unit} for value, unit in seen]})
        else:
            accepted.append(rows[0])
            duplicate_rows += len(rows) - 1
    reasons = []
    if conflicts:
        reasons.append(f"Sprzeczne pomiary dla tego samego czasu: {len(conflicts)}; wymagane wyjasnienie zrodla")
    if invalid_rows:
        codes = ", ".join(sorted({issue["code"] for issue in issues}))
        reasons.append(f"Niepoprawne wybrane wiersze: {invalid_rows}; kody: {codes}")
    if malformed:
        reasons.append(f"Uszkodzone rekordy CSV: {malformed}; popraw strukture pliku")
    quality = {
        "input_rows": input_count, "selected_rows": selected, "filtered_rows": filtered,
        "unclassified_malformed_rows": malformed, "invalid_selected_rows": invalid_rows,
        "accepted_unique_rows": len(accepted),
        "rejected_rows": malformed + invalid_rows + conflict_rows + duplicate_rows,
        "duplicate_rows": len(duplicates), "duplicates": duplicates,
        "conflicting_timestamps": len(conflicts), "conflict_rows": conflict_rows, "conflicts": conflicts,
        "invalid_values": sum(i["code"] == "invalid_value" for i in issues),
        "invalid_timestamps": sum(i["code"] == "invalid_timestamp" for i in issues), "issues": issues,
        "parse_complete": complete,
        "time_range": {"start": min(by_time).isoformat(), "end": max(by_time).isoformat()} if by_time else None,
        "spacing": spacing_statistics(by_time), "blocking_reasons": reasons,
        "notes": [REPORTING_NOTE, "Walidacja wartosci i dat dotyczy wybranego urzadzenia i metryki.",
                  "Duplikat: ten sam czas UTC, wartosc i jednostka; id rekordu moze sie roznic.",
                  "Konflikty blokuja symulacje calego wybranego strumienia, rowniez poza zakresem analizy."],
    }
    return ImportedHistory(source, device_id, metric, tuple(accepted), quality)


def period_records(records, *, start, end, include_previous, include_end):
    parsed = []
    for record, row in records:
        try:
            at = parse_timestamp(row["ts"] if isinstance(row["ts"], str) else "")
        except ValueError:
            at = None
        parsed.append(((record, row), at))
    previous = max((at for _, at in parsed if at is not None and at < start), default=None) if include_previous else None
    chosen = [row for row, at in parsed if at is None or start <= at < end
              or (include_end and at == end) or (previous is not None and at == previous)]
    return chosen, previous

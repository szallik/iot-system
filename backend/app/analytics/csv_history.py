"""CSV transport for shared measurement validation; no backend imports."""

import csv
from hashlib import sha256
from io import StringIO
from pathlib import Path

from .measurement_history import COLUMNS, ImportedHistory, parse_timestamp, period_records, validate_measurements


def read_measurements(path: str | Path):
    path = Path(path)
    content = path.read_bytes()
    reader = csv.reader(StringIO(content.decode("utf-8-sig"), newline=""), delimiter=",", strict=True)
    try:
        header = next(reader, None)
    except csv.Error as error:
        raise ValueError(f"Uszkodzony naglowek CSV: {error}") from error
    if header is None or len(header) != len(COLUMNS) or set(header) != set(COLUMNS):
        raise ValueError("Niepoprawny naglowek measurements; wymagane kolumny: " + ",".join(COLUMNS))
    records, issues = [], []
    complete = True
    record = 1
    while True:
        record += 1
        try:
            fields = next(reader)
        except StopIteration:
            break
        except csv.Error as error:
            issues.append({"record": record, "code": "malformed_csv", "detail": str(error)})
            complete = False
            break
        if len(fields) != len(header):
            issues.append({"record": record, "code": "column_count", "detail": f"{len(fields)} columns"})
        else:
            records.append((record, dict(zip(header, fields))))
    source = {"path": str(path.resolve()), "sha256": sha256(content).hexdigest(),
              "format": "backend measurements CSV", "encoding": "utf-8-sig"}
    return records, source, issues, complete


def import_measurements(path: str | Path, *, device_id: str, metric: str) -> ImportedHistory:
    records, source, issues, complete = read_measurements(path)
    return validate_measurements(records, device_id=device_id, metric=metric, source=source,
                                 structural_issues=issues, parse_complete=complete)


def import_sensor_period(path, *, device_id, metrics, start, end, command):
    records, source, issues, complete = read_measurements(path)
    histories = {}
    for metric in metrics:
        stream = ((n, row) for n, row in records if row["device_id"] == device_id and row["metric"] == metric)
        selected, _ = period_records(stream, start=start, end=end, include_end=metric == "energy",
                                     include_previous=command != "electricity" and metric != "energy")
        histories[metric] = validate_measurements(selected, device_id=device_id, metric=metric, source=source,
                                                  structural_issues=issues, parse_complete=complete)
    return histories

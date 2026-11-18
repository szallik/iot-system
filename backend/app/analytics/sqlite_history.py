"""Explicit-path read-only SQLite adapter, independent of SQLAlchemy/startup."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3

from .core import utc
from .measurement_history import COLUMNS, period_records, validate_measurements


@contextmanager
def open_readonly(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"Plik bazy nie istnieje: {path}")
    connection = None
    try:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='measurements'").fetchone() is None:
            raise ValueError("Brak wymaganej tabeli measurements")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(measurements)")}
        if not set(COLUMNS) <= columns:
            raise ValueError("Tabela measurements nie ma wymaganych kolumn: " + ",".join(sorted(set(COLUMNS)-columns)))
        yield connection
    except sqlite3.Error as error:
        raise ValueError(f"Blad odczytu SQLite: {error}") from error
    finally:
        if connection is not None:
            connection.close()


def source_metadata(path):
    return {"path": str(Path(path).resolve()), "format": "SQLite measurements", "read_only": True,
            "record_numbering": "Ordinal in selected query result, starting at 2 (same as CSV header convention)"}


class RecordLimitExceeded(ValueError):
    pass


def bounded_rows(cursor, max_records):
    try:
        rows = cursor.fetchall() if max_records is None else cursor.fetchmany(max_records + 1)
    finally:
        cursor.close()
    if max_records is not None and len(rows) > max_records:
        raise RecordLimitExceeded(f"Przekroczono limit {max_records} odczytow; historia nie zostala obcieta")
    return [dict(row) for row in rows]


def read_rows(connection, sql, parameters, max_records):
    if max_records is not None:
        # Also bound SQLite's result/sort buffer, not just Python's fetch buffer.
        sql += " LIMIT ?"
        parameters = (*parameters, max_records + 1)
    return bounded_rows(connection.execute(sql, parameters), max_records)


def import_sqlite(path, *, device_id, metric, start, end, max_records=None):
    start, end = utc(start), utc(end)
    if start >= end:
        raise ValueError("Zakres musi spelniac from < to")
    if not device_id.strip() or not metric.strip():
        raise ValueError("device_id i metric nie moga byc puste")
    with open_readonly(path) as connection:
        # Offsets and invalid dates make lexical SQL timestamp bounds unreliable.
        stream = read_rows(connection,
            "SELECT id,device_id,metric,value,unit,ts FROM measurements "
            "WHERE device_id=? AND metric=? ORDER BY id", (device_id, metric), max_records)
    return select_period(stream, path=path, device_id=device_id, metric=metric, start=start, end=end)


def select_period(stream, *, path, device_id, metric, start, end, include_previous=True, include_end=False):
    selected, previous = period_records(enumerate(stream), start=start, end=end,
                                        include_previous=include_previous, include_end=include_end)
    chosen = [row for _, row in selected]
    source = source_metadata(path)
    source.update(requested_range={"start": start.isoformat(), "end": end.isoformat(), "end_exclusive": True},
                  timestamp_selection="UTC-normalized; latest pre-start instant plus [from,to); invalid dates retained",
                  scanned_stream_rows=len(stream), excluded_by_time=len(stream)-len(chosen),
                  pre_start_timestamp=previous.isoformat() if previous else None,
                  measurement_ids=[row["id"] for row in chosen])
    if not include_previous or include_end:
        source["requested_range"]["end_exclusive"] = not include_end
        source["timestamp_selection"] = (
            f"UTC-normalized; include_previous={include_previous}; include_end={include_end}; invalid dates retained")
    return validate_measurements(enumerate(chosen, start=2), device_id=device_id, metric=metric, source=source)


class ElectricityDeviceError(ValueError):
    pass


def import_meter(path, *, device_id, start, end, max_records, device_type="power_strip",
                 metrics=("energy", "power")):
    """One snapshot, combined record budget, strict device type (never infer from id)."""
    with open_readonly(path) as connection:
        device = connection.execute("SELECT typ FROM devices WHERE id=?", (device_id,)).fetchone()
        if device is None or device["typ"] != device_type:
            raise ElectricityDeviceError(f"Analiza wymaga istniejacego urzadzenia typu {device_type}")
        stream = read_rows(connection,
            "SELECT id,device_id,metric,value,unit,ts FROM measurements "
            f"WHERE device_id=? AND metric IN ({','.join('?' for _ in metrics)}) ORDER BY id",
            (device_id, *metrics), max_records)
    return tuple(select_period([row for row in stream if row["metric"] == metric], path=path,
                 device_id=device_id, metric=metric, start=utc(start), end=utc(end),
                 include_previous=device_type == "heat_meter" and metric != "energy",
                 include_end=metric == "energy") for metric in metrics)


def list_devices(path, *, max_records=None):
    with open_readonly(path) as connection:
        rows = read_rows(connection,
            "SELECT id,device_id,metric,value,unit,ts FROM measurements WHERE metric=? ORDER BY device_id,id",
            ("occupancy",), max_records)
    groups = {}
    for row in rows:
        groups.setdefault(row["device_id"], []).append(row)
    devices = []
    for device_id, selected in groups.items():
        quality = validate_measurements(enumerate(selected, start=2), device_id=device_id,
                                        metric="occupancy", source=source_metadata(path)).quality
        devices.append({"device_id": device_id, "readings": len(selected), "time_range": quality["time_range"],
                        "invalid_timestamps": quality["invalid_timestamps"], "invalid_values": quality["invalid_values"],
                        "measurement_ids": [row["id"] for row in selected], "quality": quality})
    return {"source": source_metadata(path), "devices": devices}


def import_periods(path, *, device_id, periods, max_records):
    """One read snapshot for selection and later assessment; one bounded stream."""
    with open_readonly(path) as connection:
        stream = read_rows(connection,
            "SELECT id,device_id,metric,value,unit,ts FROM measurements "
            "WHERE device_id=? AND metric=? ORDER BY id", (device_id, "occupancy"), max_records)
    return tuple(select_period(stream, path=path, device_id=device_id, metric="occupancy",
                               start=utc(start), end=utc(end)) for start, end in periods)


def import_electricity(path, *, device_id, start, end, max_records):
    return import_meter(path, device_id=device_id, start=start, end=end, max_records=max_records)

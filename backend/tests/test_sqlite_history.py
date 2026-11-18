import ast
import csv
from contextlib import closing, redirect_stdout, redirect_stderr
from datetime import timedelta
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from app.analytics.csv_history import import_measurements
from app.analytics.history_cli import jsonable, main, simulate_import
from app.analytics.measurement_history import COLUMNS, parse_timestamp
from app.analytics.sqlite_history import import_sqlite, list_devices, open_readonly
from app.analytics.energy import ReceiverModel
from app.analytics.strategies import FixedDelay
from tests.sqlite_fixture import create_database


START = parse_timestamp("2026-01-05T08:00:00Z")
END = parse_timestamp("2026-01-05T08:15:00Z")
ROOT = Path(__file__).resolve().parents[2]


def row(id, ts, value=1, device="room", metric="occupancy"):
    return (id, device, metric, value, "bool" if metric == "occupancy" else "C", ts)


class SqliteHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic # pomiar.db"
        self.output = Path(self.temp.name) / "result.json"

    def load(self, **kwargs):
        parameters = dict(device_id="room", metric="occupancy", start=START, end=END)
        parameters.update(kwargs)
        return import_sqlite(self.path, **parameters)

    def run_cli(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return main(list(args), source_type="sqlite")

    def cli_args(self, command):
        args = [command, str(self.path), "--output", str(self.output)]
        if command != "devices":
            args += ["--device-id", "room", "--from", START.isoformat(), "--to", END.isoformat()]
        if command == "simulate":
            args += ["--ttl-seconds", "180", "--strategy", "fixed", "--delay-minutes", "2",
                     "--on-watts", "60", "--off-watts", "2"]
        return args

    def test_fixture_columns_match_sqlalchemy_model_without_import(self):
        tree = ast.parse((ROOT / "backend/app/models/entities.py").read_text(encoding="utf-8"))
        model = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Measurement")
        columns = {node.targets[0].id for node in model.body if isinstance(node, ast.Assign)
                   and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                   and node.value.func.id == "Column"}
        create_database(self.path)
        with open_readonly(self.path) as connection:
            actual = {record["name"] for record in connection.execute("PRAGMA table_info(measurements)")}
        self.assertEqual(actual, columns)

    def test_range_filters_device_metric_and_retains_all_predecessors(self):
        create_database(self.path, [
            row(1, "2026-01-05T07:00:00Z"),
            row(2, "2026-01-05T07:59:00Z"), row(3, "2026-01-05T08:59:00+01:00"),
            row(4, "2026-01-05T08:00:00Z", 0),
            row(5, "2026-01-05T08:14:59Z", 0),
            row(6, "2026-01-05T08:15:00Z"), row(7, "2026-01-05T08:16:00Z"),
            row(8, "2026-01-05T08:00:00Z", device="other"),
            row(9, "2026-01-05T08:00:00Z", metric="temperature"),
        ])
        imported = self.load()
        self.assertEqual(imported.source["measurement_ids"], [2, 3, 4, 5])
        self.assertEqual(imported.quality["duplicate_rows"], 1)
        self.assertEqual(imported.source["scanned_stream_rows"], 7)
        self.assertEqual(imported.quality["input_rows"], 4)
        self.assertEqual(imported.rows[0].at, START - timedelta(minutes=1))

    def test_conflict_in_predecessor_blocks_even_with_different_offsets(self):
        create_database(self.path, [row(1, "2026-01-05T07:59:00Z", 0),
                                    row(2, "2026-01-05T08:59:00+01:00", 1)])
        imported = self.load()
        self.assertEqual(imported.quality["conflicting_timestamps"], 1)
        with self.assertRaisesRegex(ValueError, "Sprzeczne"):
            imported.presence_reports()

    def test_conflict_at_start_blocks_but_at_exclusive_end_does_not(self):
        create_database(self.path, [row(1, END.isoformat(), 0), row(2, END.isoformat(), 1)])
        self.assertEqual(self.load().quality["input_rows"], 0)
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.executemany("INSERT INTO measurements VALUES (?,?,?,?,?,?)",
                                   [row(3, START.isoformat(), 0), row(4, START.isoformat(), 1)])
        self.assertEqual(self.load().quality["conflicting_timestamps"], 1)

    def test_invalid_dates_are_not_silently_lost_by_range_filter(self):
        create_database(self.path, [row(1, "bad"), row(2, "2026-01-05T08:00:00"),
                                    row(3, "2026-01-05T08:01:00Z", 2)])
        imported = self.load()
        self.assertEqual(imported.quality["invalid_timestamps"], 2)
        self.assertEqual(imported.quality["invalid_values"], 1)
        with self.assertRaises(ValueError):
            imported.presence_reports()

    def test_latest_invalid_value_does_not_fall_back_to_older_valid_value(self):
        create_database(self.path, [row(1, "2026-01-05T07:58:00Z"), row(2, "2026-01-05T07:59:00Z", .5)])
        imported = self.load()
        self.assertEqual(imported.source["measurement_ids"], [2])
        self.assertEqual(imported.quality["invalid_values"], 1)

    def test_missing_file_never_created(self):
        with self.assertRaisesRegex(ValueError, "nie istnieje"):
            self.load()
        self.assertFalse(self.path.exists())
        self.assertEqual(self.run_cli(*self.cli_args("quality")), 2)
        self.assertFalse(self.path.exists())

    def test_missing_table_columns_and_corrupt_database(self):
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE unrelated(id INTEGER)")
        with self.assertRaisesRegex(ValueError, "tabeli measurements"):
            self.load()
        with closing(sqlite3.connect(self.path)) as connection, connection:
            connection.execute("CREATE TABLE measurements(id INTEGER)")
        with self.assertRaisesRegex(ValueError, "kolumn"):
            self.load()
        self.path.write_bytes(b"not a database")
        with self.assertRaisesRegex(ValueError, "SQLite"):
            self.load()

    def test_file_content_mtime_schema_and_files_unchanged(self):
        create_database(self.path, [row(1, START.isoformat())])
        before = self.path.read_bytes(), self.path.stat().st_mtime_ns, set(Path(self.temp.name).iterdir())
        self.load().presence_reports()
        list_devices(self.path)
        simulate_import(self.load(), start=START, end=END, ttl=timedelta(minutes=3),
                        strategy=FixedDelay(timedelta(minutes=2)), model=ReceiverModel(60, 2),
                        short_return_window=timedelta(minutes=2))
        with open_readonly(self.path) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("DELETE FROM measurements")
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute("CREATE TABLE forbidden(id INTEGER)")
        after = self.path.read_bytes(), self.path.stat().st_mtime_ns, set(Path(self.temp.name).iterdir())
        self.assertEqual(before, after)

    def test_reads_committed_wal_data_without_ignoring_or_modifying_it(self):
        create_database(self.path)
        writer = sqlite3.connect(self.path)
        self.addCleanup(writer.close)
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO measurements VALUES (?,?,?,?,?,?)", row(1, START.isoformat()))
        writer.commit()
        wal = Path(str(self.path) + "-wal")
        before = self.path.read_bytes(), wal.read_bytes()
        self.assertEqual(self.load().quality["input_rows"], 1)
        self.assertEqual(before, (self.path.read_bytes(), wal.read_bytes()))

    def test_devices_lists_only_occupancy_and_counts_duplicates(self):
        create_database(self.path, [row(1, START.isoformat()), row(2, START.isoformat()),
                                    row(3, END.isoformat(), device="second"),
                                    row(4, START.isoformat(), device="temperature_only", metric="temperature")])
        listing = list_devices(self.path)["devices"]
        self.assertEqual([(d["device_id"], d["readings"]) for d in listing], [("room", 2), ("second", 1)])

    def test_same_data_produces_identical_csv_sqlite_quality_and_simulation(self):
        rows = [row(1, "2026-01-05T07:59:00Z"), row(2, START.isoformat()), row(3, START.isoformat()),
                row(4, "2026-01-05T08:02:00Z", 0), row(5, "2026-01-05T08:03:00Z", 0),
                row(6, "2026-01-05T08:10:00Z")]
        create_database(self.path, rows)
        csv_path = Path(self.temp.name) / "same.csv"
        # Serialize SQLite float values exactly like the project's export.
        with closing(sqlite3.connect(self.path)) as connection, csv_path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(COLUMNS)
            writer.writerows(connection.execute("SELECT id,device_id,metric,value,unit,ts FROM measurements ORDER BY id"))
        csv_history = import_measurements(csv_path, device_id="room", metric="occupancy")
        db = self.load()
        self.assertEqual(csv_history.quality, db.quality)
        self.assertEqual(csv_history.presence_reports(), db.presence_reports())
        parameters = dict(start=START, end=END, ttl=timedelta(seconds=180), strategy=FixedDelay(timedelta(minutes=2)),
                          model=ReceiverModel(60, 2), short_return_window=timedelta(minutes=2))
        self.assertEqual(jsonable(simulate_import(csv_history, **parameters)), jsonable(simulate_import(db, **parameters)))

    def test_cli_commands_ttl_and_source_marking(self):
        create_database(self.path, [row(1, "2026-01-05T07:59:00Z")])
        for command in ("devices", "quality", "simulate"):
            self.assertEqual(self.run_cli(*self.cli_args(command)), 0)
            doc = json.loads(self.output.read_text(encoding="utf-8"))
            self.assertEqual(doc["source"]["kind"], "unknown")
            self.assertTrue(doc["source"]["read_only"])
            if command == "simulate":
                self.assertTrue(doc["result"]["energy_simulated"])
                self.assertTrue(doc["result"]["pre_start_report_is_fresh"])
        args = self.cli_args("simulate")
        index = args.index("--ttl-seconds")
        del args[index:index+2]
        with self.assertRaises(SystemExit):
            self.run_cli(*args)

    def test_empty_stream_keeps_unknown_and_initial_on(self):
        create_database(self.path)
        self.assertEqual(self.run_cli(*self.cli_args("simulate")), 0)
        result = json.loads(self.output.read_text(encoding="utf-8"))["result"]
        self.assertEqual(result["energy"]["known_time_share"], 0)
        self.assertEqual(result["energy"]["shutdown_count"], 0)

    def test_output_cannot_overwrite_database_or_wal(self):
        create_database(self.path)
        for path in (self.path, Path(str(self.path) + "-wal")):
            self.assertEqual(self.run_cli("devices", str(self.path), "--output", str(path)), 2)

    def test_bound_parameters_do_not_select_other_devices(self):
        create_database(self.path, [row(1, START.isoformat())])
        self.assertEqual(self.load(device_id="room' OR 1=1 --").quality["input_rows"], 0)

    def test_existing_schedule_and_frozen_adaptive_work_with_sqlite(self):
        create_database(self.path, [row(1, START.isoformat(), 0)])
        for name, config in [("schedule", ROOT / "docs/examples/example-schedule.json"),
                             ("adaptive", ROOT / "docs/analytics-adaptive-example.json")]:
            before = config.read_bytes()
            args = self.cli_args("simulate")
            args[args.index("--strategy") + 1] = name
            index = args.index("--delay-minutes")
            del args[index:index + 2]
            self.assertEqual(self.run_cli(*args, "--strategy-config", str(config), "--source-kind", "real"), 0)
            self.assertEqual(config.read_bytes(), before)
            document = json.loads(self.output.read_text(encoding="utf-8"))
            self.assertEqual(document["source"]["kind"], "real")
            self.assertTrue(document["result"]["energy_simulated"])


if __name__ == "__main__":
    unittest.main()

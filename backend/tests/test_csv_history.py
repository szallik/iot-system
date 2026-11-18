import ast
import csv
import json
from contextlib import redirect_stdout, redirect_stderr
from datetime import timedelta
from io import StringIO
from pathlib import Path
import tempfile
import unittest

from app.analytics.csv_cli import main, simulate_import
from app.analytics.csv_history import COLUMNS, import_measurements, parse_timestamp
from app.analytics.core import Presence
from app.analytics.energy import ReceiverModel
from app.analytics.strategies import FixedDelay


ROOT = Path(__file__).resolve().parents[2]


class CsvHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "history.csv"
        self.output = Path(self.temp.name) / "result.json"

    def write(self, rows, header=COLUMNS, bom=False):
        with self.path.open("w", encoding="utf-8-sig" if bom else "utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(header)
            writer.writerows(rows)

    def load(self, metric="occupancy"):
        return import_measurements(self.path, device_id="room", metric=metric)

    def row(self, value="1.0", ts="2026-01-05T08:00:00Z", device="room", metric="occupancy", unit="bool"):
        return [1, device, metric, value, unit, ts]

    def cli(self, *args):
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            return main(list(args))

    def simulate_args(self):
        return ["simulate", str(self.path), "--device-id", "room", "--output", str(self.output),
                "--from", "2026-01-05T08:00:00Z", "--to", "2026-01-05T09:00:00Z",
                "--ttl-seconds", "180", "--strategy", "fixed", "--delay-minutes", "2",
                "--on-watts", "60", "--off-watts", "2"]

    def test_header_matches_actual_export_without_importing_backend(self):
        tree = ast.parse((ROOT / "backend/app/routers/access_reports.py").read_text(encoding="utf-8"))
        headers = [tuple(ast.literal_eval(node.args[0])) for node in ast.walk(tree)
                   if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                   and node.func.attr == "writerow" and node.args and isinstance(node.args[0], ast.List)
                   and all(isinstance(item, ast.Constant) for item in node.args[0].elts)]
        self.assertIn(COLUMNS, headers)

    def test_export_float_values_bom_sorting_and_utc(self):
        self.write([self.row("0.0", "2026-01-05T09:02:00+01:00"), self.row()], bom=True)
        loaded = self.load()
        reports = loaded.presence_reports()
        self.assertEqual([r.occupied for r in reports], [True, False])
        self.assertEqual(reports[1].at.isoformat(), "2026-01-05T08:02:00+00:00")
        self.assertEqual(loaded.quality["spacing"]["max_seconds"], 120)

    def test_device_and_metric_filter_before_value_validation(self):
        self.write([self.row(), self.row("bad", "bad", device="other"),
                    self.row("bad", "bad", metric="temperature")])
        quality = self.load().quality
        self.assertEqual((quality["input_rows"], quality["selected_rows"], quality["filtered_rows"]), (3, 1, 2))
        self.assertEqual(quality["invalid_values"], 0)
        self.assertEqual(quality["blocking_reasons"], [])

    def test_nonoccupancy_measurements_quality_only(self):
        self.write([self.row("21.5", metric="temperature", unit="°C"), self.row()])
        loaded = self.load("temperature")
        self.assertEqual(str(loaded.rows[0].value), "21.5")
        with self.assertRaisesRegex(ValueError, "occupancy"):
            loaded.presence_reports()

    def test_strict_presence_rejects_nonbinary_including_rounding(self):
        values = ("true", "False", "", "2", "-1", "0.5", "1.00000000000000001", "NaN", "Infinity")
        self.write([self.row(value) for value in values])
        loaded = self.load()
        self.assertEqual(loaded.quality["invalid_values"], len(values))
        self.assertEqual(loaded.quality["rejected_rows"], len(values))
        with self.assertRaises(ValueError):
            loaded.presence_reports()

    def test_invalid_and_naive_dates_are_reported(self):
        self.write([self.row(ts=value) for value in ("bad", "2026-02-30T08:00:00Z", "2026-01-05T08:00:00")])
        quality = self.load().quality
        self.assertEqual(quality["invalid_timestamps"], 3)
        self.assertEqual([issue["record"] for issue in quality["issues"]], [2, 3, 4])
        self.assertIsNone(quality["time_range"])

    def test_duplicate_normalizes_offsets_and_ignores_database_id(self):
        a, b = self.row(), self.row("1", "2026-01-05T09:00:00+01:00")
        b[0] = 9
        self.write([a, b])
        loaded = self.load()
        self.assertEqual(loaded.quality["duplicate_rows"], 1)
        self.assertEqual(len(loaded.presence_reports()), 1)
        self.assertEqual(loaded.quality["spacing"]["count"], 0)

    def test_conflicts_block_instead_of_choosing_value(self):
        self.write([self.row("0"), self.row("1"), self.row("1")])
        loaded = self.load()
        self.assertEqual(loaded.quality["conflicting_timestamps"], 1)
        self.assertEqual(loaded.quality["conflict_rows"], 3)
        self.assertEqual(loaded.quality["duplicate_rows"], 1)  # overlaps conflict diagnostics
        self.assertEqual(loaded.quality["rejected_rows"], 3)  # never double counted
        with self.assertRaisesRegex(ValueError, "Sprzeczne"):
            loaded.presence_reports()
        self.assertEqual(self.cli(*self.simulate_args()), 2)
        result = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn("result", result)
        self.assertEqual(result["ttl_seconds"], 180)

    def test_pre_start_report_used_only_while_fresh(self):
        self.write([self.row(ts="2026-01-05T07:59:00Z")])
        imported = self.load()
        start, end = parse_timestamp("2026-01-05T08:00:00Z"), parse_timestamp("2026-01-05T08:05:00Z")
        for ttl, expected in [(180, [Presence.PRESENT, Presence.UNKNOWN]), (60, [Presence.UNKNOWN])]:
            result = simulate_import(imported, start=start, end=end, ttl=timedelta(seconds=ttl),
                                     strategy=FixedDelay(timedelta(minutes=2)), model=ReceiverModel(60, 2),
                                     short_return_window=timedelta(minutes=2))
            self.assertEqual([i.presence for i in result["presence_intervals"]], expected)
            self.assertEqual(result["pre_start_report_is_fresh"], ttl == 180)

    def test_empty_selection_stays_unknown_no_auto_off(self):
        self.write([self.row(device="other")])
        self.assertEqual(self.cli(*self.simulate_args()), 0)
        result = json.loads(self.output.read_text(encoding="utf-8"))["result"]
        self.assertEqual(result["energy"]["known_time_share"], 0)
        self.assertEqual(result["energy"]["shutdown_count"], 0)
        self.assertEqual(result["energy"]["strategy_wh"], 60)

    def test_spacing_distribution_uses_sorted_distinct_times(self):
        self.write([self.row(ts=t) for t in ("2026-01-05T08:07:00Z", "2026-01-05T08:00:00Z",
                                           "2026-01-05T08:01:00Z", "2026-01-05T08:07:00Z")])
        spacing = self.load().quality["spacing"]
        self.assertEqual(spacing["count"], 2)
        self.assertEqual(spacing["median_seconds"], 210)
        self.assertEqual(spacing["p95_seconds"], 360)
        self.assertEqual(spacing["largest_gaps"][0]["seconds"], 360)
        self.assertEqual(sum(spacing["histogram"].values()), 2)

    def test_quality_does_not_require_ttl_and_simulation_does(self):
        self.write([self.row()])
        self.assertEqual(self.cli("quality", str(self.path), "--device-id", "room", "--output", str(self.output)), 0)
        report = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertIsNone(report["ttl_seconds"])
        self.assertIsNone(report["strategy"])
        args = self.simulate_args()
        index = args.index("--ttl-seconds")
        del args[index:index+2]
        with self.assertRaises(SystemExit):
            self.cli(*args)

    def test_malformed_rows_and_headers_are_not_silent(self):
        self.write([[1, "room"]])
        self.assertEqual(self.load().quality["unclassified_malformed_rows"], 1)
        self.write([], header=("device_id", "ts", "payload"))
        with self.assertRaisesRegex(ValueError, "naglowek"):
            self.load()
        self.path.write_text(','.join(COLUMNS) + '\n1,room,occupancy,"unterminated', encoding="utf-8")
        self.assertFalse(self.load().quality["parse_complete"])

    def test_counts_partition_input_rows(self):
        self.write([self.row(), self.row(), self.row("2"), self.row(device="other"), [1, "room"]])
        q = self.load().quality
        self.assertEqual(q["input_rows"], q["accepted_unique_rows"] + q["filtered_rows"] + q["rejected_rows"])
        self.assertEqual(q["selected_rows"], 3)

    def test_simulated_energy_metadata_for_hardware_source(self):
        self.write([self.row("0")])
        self.assertEqual(self.cli(*self.simulate_args(), "--source-kind", "hardware"), 0)
        doc = json.loads(self.output.read_text(encoding="utf-8"))
        self.assertTrue(doc["result"]["energy_simulated"])
        self.assertTrue(doc["result"]["energy"]["simulated"])
        self.assertEqual(doc["source"]["kind"], "hardware")
        self.assertEqual(len(doc["source"]["sha256"]), 64)
        self.assertEqual(doc["receiver_model_watts"], {"on_watts": 60, "off_watts": 2})
        self.assertEqual(doc["strategy"]["name"], "fixed_delay")

    def test_load_frozen_schedule_and_adaptive_configuration(self):
        self.write([self.row()])
        for name, config in [("schedule", ROOT / "docs/examples/example-schedule.json"),
                             ("adaptive", ROOT / "docs/analytics-adaptive-example.json")]:
            before = config.read_bytes()
            args = self.simulate_args()
            index = args.index("--delay-minutes")
            del args[index:index+2]
            args[args.index("--strategy")+1] = name
            self.assertEqual(self.cli(*args, "--strategy-config", str(config)), 0)
            self.assertEqual(config.read_bytes(), before)

    def test_output_cannot_overwrite_input(self):
        self.write([self.row()])
        before = self.path.read_bytes()
        self.assertEqual(self.cli("quality", str(self.path), "--device-id", "room", "--output", str(self.path)), 2)
        self.assertEqual(self.path.read_bytes(), before)

    def test_synthetic_example_matches_export_and_expected_counts(self):
        example = ROOT / "docs/examples/synthetic-measurements.csv"
        q = import_measurements(example, device_id="SYNTHETIC_ROOM_A", metric="occupancy").quality
        self.assertEqual((q["input_rows"], q["selected_rows"], q["accepted_unique_rows"]), (10, 8, 7))
        self.assertEqual(q["filtered_rows"], 2)
        self.assertEqual(q["duplicate_rows"], 1)


if __name__ == "__main__":
    unittest.main()

"""Compare offline CSV reports with the SQLite-backed HTTP endpoints."""
import csv
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import unittest

from app.analytics.history_cli import main
from tests import test_analytics_api as api_tests


EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples"


class SensorCsvTests(unittest.TestCase):
    setUp = api_tests.AnalyticsApiTests.setUp
    call = api_tests.AnalyticsApiTests.call

    def compare(self, command, device, device_type, options, invalid=False):
        filename = "invalid" if invalid else command
        source = EXAMPLES / f"synthetic-{filename}.csv"
        with source.open(encoding="utf8", newline="") as handle:
            rows = [row for row in csv.DictReader(handle) if row["device_id"] == device]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO devices(id,nazwa,typ,online) VALUES (?,?,?,1)", (device, device, device_type))
            db.executemany("INSERT INTO measurements(device_id,metric,value,unit,ts) "
                           "VALUES (:device_id,:metric,:value,:unit,:ts)", rows)
        query = {"from": self.period["from"], "to": "2026-01-01T08:10:00Z", "device_id": device, **options}
        output = self.path.with_suffix(".json")
        args = [command, str(source), "--output", str(output), "--source-kind", "synthetic"]
        for key, value in query.items():
            args.extend(["--" + key.replace("_", "-"), str(value)])
        if command != "environment":
            args.extend(["--device-type", device_type])
        self.assertEqual(main(args), 2 if invalid else 0)
        report = json.loads(output.read_text(encoding="utf8"))
        status, response = self.call(command, query=query)
        if invalid:
            self.assertEqual(status, 409, response)
            self.assertIn("invalid_value", str(response))
            self.assertIn("invalid_value", report["error"])
        else:
            self.assertEqual(status, 200, response)
            # HTTP serializes UTC as Z, while the CLI uses +00:00.
            actual = json.loads(json.dumps(report["result"]).replace("+00:00", "Z"))
            self.assertEqual(actual, {key: response[key] for key in actual})

    def test_csv_matches_sqlite_api(self):
        self.compare("environment", "room", "environment_sensor", {"metric": "temperature", "ttl_seconds": 180,
                                                                   "lower_threshold": 0})
        self.compare("electricity", "strip", "power_strip", {"price_per_kwh": 0.8})
        self.compare("heat", "meter", "heat_meter", {"ttl_seconds": 180})

    def test_invalid_values_block_both_transports(self):
        self.compare("environment", "room", "environment_sensor", {"metric": "humidity", "ttl_seconds": 180}, True)
        self.compare("electricity", "strip", "power_strip", {}, True)
        self.compare("heat", "meter", "heat_meter", {"ttl_seconds": 180}, True)

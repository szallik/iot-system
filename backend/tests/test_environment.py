from contextlib import closing
from datetime import datetime, timedelta, timezone
from dataclasses import replace
import hashlib
import sqlite3
import unittest

from app.analytics.environment import analyze_environment
from app.analytics.measurement_history import validate_measurements
from tests import test_analytics_api as api_tests


START = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)


def history(points, metric="temperature", unit="\u00b0C"):
    rows = [(index + 2, {"id": index + 1, "device_id": "sensor", "metric": metric,
             "unit": unit, "value": value, "ts": (START + timedelta(seconds=at)).isoformat()})
            for index, (at, value) in enumerate(points)]
    return validate_measurements(rows, device_id="sensor", metric=metric, source={"kind": "synthetic"})


def analyze(points, ttl=600, end=600, **kwargs):
    return analyze_environment(history(points), start=START, end=START+timedelta(seconds=end),
                               ttl=timedelta(seconds=ttl), **kwargs)


class EnvironmentTests(unittest.TestCase):
    def test_irregular_intervals_weighted_not_sample_average(self):
        result = analyze([(0, 10), (60, 20), (300, 30)])
        self.assertAlmostEqual(result["time_weighted_mean"], 24)  # (10*60+20*240+30*300)/600
        self.assertEqual(result["valid_readings_count"], 3)
        self.assertEqual((result["minimum"], result["maximum"]), (10, 30))
        self.assertEqual(result["coverage_percent"], 100)
        self.assertIsNone(result["below_seconds"])
        self.assertFalse(result["thresholds_evaluated"])

    def test_expiry_and_no_interpolation(self):
        result = analyze([(0, 10), (300, 30)], ttl=120, lower_threshold=15, upper_threshold=25)
        self.assertEqual(result["time_weighted_mean"], 20)
        self.assertEqual(result["known_seconds"], 240)
        self.assertEqual(result["unknown_seconds"], 360)
        self.assertEqual(result["coverage_percent"], 40)
        self.assertEqual((result["below_seconds"], result["within_seconds"], result["above_seconds"]), (120, 0, 120))

    def test_pre_start_remaining_ttl_and_exclusive_end(self):
        result = analyze([(-60, 50), (120, 10), (600, -100)], ttl=180)
        self.assertEqual(result["valid_readings_count"], 1)
        self.assertEqual((result["minimum"], result["maximum"]), (10, 10))
        self.assertEqual(result["time_weighted_mean"], 26)  # (50*120 + 10*180)/300
        self.assertEqual(result["unknown_seconds"], 300)
        result = analyze([(-60, 50)], ttl=180)
        self.assertEqual(result["valid_readings_count"], 0)
        self.assertIsNone(result["minimum"])
        self.assertEqual(result["time_weighted_mean"], 50)
        self.assertEqual(result["known_seconds"], 120)

    def test_empty_and_expired_previous(self):
        for points in ([], [(-120, 20)]):
            result = analyze(points, ttl=120)
            for key in ("minimum", "maximum", "time_weighted_mean"):
                self.assertIsNone(result[key])
            self.assertEqual(result["unknown_seconds"], 600)
            self.assertEqual(result["coverage_percent"], 0)

    def test_threshold_equality_and_one_sided_limits(self):
        points = [(0, 10), (100, 20), (200, 30), (400, 40)]
        result = analyze(points, lower_threshold=20, upper_threshold=30)
        self.assertEqual((result["below_seconds"], result["within_seconds"], result["above_seconds"]), (100, 300, 200))
        self.assertEqual(sum(result[k] for k in ("below_seconds", "within_seconds", "above_seconds", "unknown_seconds")), 600)
        result = analyze(points, upper_threshold=30)
        self.assertEqual((result["below_seconds"], result["within_seconds"], result["above_seconds"]), (0, 400, 200))
        result = analyze(points, lower_threshold=20)
        self.assertEqual((result["below_seconds"], result["within_seconds"], result["above_seconds"]), (100, 500, 0))

    def test_duplicates_conflicts_and_units(self):
        result = analyze([(0, 10), (0, 10), (300, 20)])
        self.assertEqual(result["valid_readings_count"], 2)
        self.assertEqual(result["time_weighted_mean"], 15)
        with self.assertRaisesRegex(ValueError, "Sprzeczne"):
            analyze([(0, 10), (0, 20)])
        for metric, unit in (("temperature", "%"), ("humidity", "C")):
            imported = history([(0, 20)], metric=metric, unit=unit)
            self.assertEqual(imported.quality["issues"][0]["code"], "invalid_unit")
            with self.assertRaisesRegex(ValueError, "zablokowana"):
                analyze_environment(imported, start=START, end=START+timedelta(minutes=10), ttl=timedelta(minutes=1))


class EnvironmentApiTests(unittest.TestCase):
    setUp = api_tests.AnalyticsApiTests.setUp
    call = api_tests.AnalyticsApiTests.call

    def query(self, **changes):
        return dict(device_id="sensor", metric="temperature", ttl_seconds=600,
                    **{"from": "2026-01-01T08:00:00Z", "to": "2026-01-01T08:10:00Z"}) | changes

    def insert(self, points, metric="temperature", unit="\u00b0C", device="sensor"):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany("INSERT INTO measurements(device_id,metric,value,unit,ts) VALUES (?,?,?,?,?)",
                           [(device, metric, value, unit, (START+timedelta(seconds=at)).isoformat()) for at, value in points])

    def test_endpoint_core_parity_filtering_quality_and_readonly(self):
        points = [(-60, 50), (120, 10), (120, 10), (600, -100)]
        self.insert(points)
        self.insert([(0, -999)], device="other")
        self.insert([(0, 90)], metric="humidity", unit="%")
        before = hashlib.sha256(self.path.read_bytes()).digest()
        status, data = self.call("environment", query=self.query(ttl_seconds=180, lower_threshold=15, upper_threshold=30))
        self.assertEqual(status, 200, data)
        expected = analyze(points, ttl=180, lower_threshold=15, upper_threshold=30)
        for key, value in expected.items():
            self.assertEqual(data[key], value, key)
        self.assertEqual(data["quality"]["duplicate_rows"], 1)
        self.assertEqual(data["source"]["kind"], "synthetic")
        self.assertEqual(data["metric"], "temperature")
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())
        status, data = self.call("environment", query=self.query(metric="humidity"))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["unit"], "%")
        self.assertEqual(data["time_weighted_mean"], 90)

    def test_validation_auth_and_limits(self):
        for changes in ({"ttl_seconds": 0}, {"ttl_seconds": "nan"}, {"ttl_seconds": "inf"},
                        {"lower_threshold": 30, "upper_threshold": 20}, {"upper_threshold": "nan"},
                        {"metric": "pressure"}, {"from": "2026-01-01T08:00:00"},
                        {"database_path": "other.db"}, {"to": "2026-01-01T07:00:00Z"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.call("environment", query=self.query(**changes))[0], 422)
        query = self.query(); del query["ttl_seconds"]
        self.assertEqual(self.call("environment", query=query)[0], 422)
        self.assertEqual(self.call("environment", query=self.query(), token=None)[0], 401)
        self.assertEqual(self.call("environment", query=self.query(), token=api_tests.create_access_token({"sub": "2"}))[0], 403)
        self.assertEqual(self.call("environment", query=self.query(to="2027-01-01T00:00:00Z"))[0], 413)
        self.insert([(0, 10), (60, 20)])
        self.service = replace(self.service, max_records=1)
        self.assertEqual(self.call("environment", query=self.query())[0], 413)

    def test_empty_conflicts_invalid_units(self):
        status, data = self.call("environment", query=self.query())
        self.assertEqual(status, 200, data)
        self.assertIsNone(data["time_weighted_mean"])
        self.assertEqual(data["unknown_seconds"], 600)
        self.insert([(-60, 10), (-60, 20)])  # all rows at predecessor instant retained
        status, data = self.call("environment", query=self.query())
        self.assertEqual(status, 409, data)
        self.assertEqual(data["error"]["code"], "ANALYTICS_DATA_QUALITY")
        self.insert([(0, 50)], metric="humidity", unit="wrong")
        self.assertEqual(self.call("environment", query=self.query(metric="humidity"))[0], 409)


if __name__ == "__main__":
    unittest.main()

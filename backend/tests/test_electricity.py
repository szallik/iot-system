from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest

from app.analytics.electricity import analyze_electricity
from app.analytics.history_service import jsonable
from app.analytics.measurement_history import validate_measurements
from tests import test_analytics_api as api_tests


START = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)


def history(points, metric="energy", unit=None):
    unit = unit if unit is not None else {"energy": "kWh", "power": "W"}[metric]
    records = [(n+2, {"id": n+1, "device_id": "strip", "metric": metric, "unit": unit,
                      "value": value, "ts": (START+timedelta(seconds=at)).isoformat()})
               for n, (at, value) in enumerate(points)]
    return validate_measurements(records, device_id="strip", metric=metric, source={})


def analyze(points, powers=(), end=3600, price=None):
    return analyze_electricity(history(points), history(powers, "power"), start=START,
                               end=START+timedelta(seconds=end), price_per_kwh=price)


class ElectricityTests(unittest.TestCase):
    def test_complete_result_unchanged_since_before_heat_refactor(self):
        # Frozen output of the pre-heat implementation in commit 7beeae5.
        expected = json.loads(Path(__file__).with_name("electricity_before_heat.json").read_text(encoding="utf-8"))
        actual = analyze([(-60, 9), (0, 10), (600, 12), (1200, 1), (3600, 4), (3660, 20)],
                         [(0, 10), (600, 40), (3600, 999)], price=1.25)
        self.assertEqual(jsonable(actual), expected)

    def test_normal_growth_and_cost(self):
        result = analyze([(1800, 12), (0, 10), (3600, 13)], price=1.25)
        self.assertEqual(result["energy_kwh"], 3)
        self.assertEqual(result["estimated_cost"], 3.75)
        self.assertEqual(result["covered_seconds"], 3600)
        self.assertEqual(result["uncovered_seconds"], 0)
        self.assertEqual(result["accepted_pairs_count"], 2)
        self.assertFalse(result["energy_simulated"])

    def test_constant_counter_is_zero_not_null(self):
        result = analyze([(0, 10), (3600, 10)], price=2)
        self.assertEqual(result["energy_kwh"], 0)
        self.assertEqual(result["estimated_cost"], 0)
        self.assertEqual(result["covered_seconds"], 3600)
        self.assertIsNone(analyze([(0, 10), (3600, 12)])["estimated_cost"])
        self.assertEqual(analyze([(0, 10), (3600, 12)], price=0)["estimated_cost"], 0)

    def test_reset_not_max_minus_min(self):
        result = analyze([(0, 10), (600, 12), (1200, 1), (1800, 4)], end=2400)
        self.assertEqual(result["energy_kwh"], 5)  # 2 + 3, NOT 12 - 1
        self.assertEqual(result["counter_drops_count"], 1)
        self.assertEqual(result["skipped_pairs_count"], 1)
        self.assertEqual(result["covered_seconds"], 1200)
        self.assertEqual(result["uncovered_seconds"], 1200)
        self.assertEqual(result["used_energy_bounds"]["end"], START+timedelta(seconds=1800))
        result = analyze([(0, 10), (3600, 1)], price=1)
        self.assertIsNone(result["energy_kwh"])
        self.assertIsNone(result["estimated_cost"])
        self.assertIsNone(result["used_energy_bounds"])

    def test_no_data_and_single_reading(self):
        for points in ([], [(120, 10)]):
            result = analyze(points, price=1)
            self.assertIsNone(result["energy_kwh"])
            self.assertIsNone(result["estimated_cost"])
            self.assertIsNone(result["max_power_w"])
            self.assertIsNone(result["max_power_at"])
            self.assertEqual(result["uncovered_seconds"], 3600)

    def test_boundaries_no_interpolation_and_power_end_exclusive(self):
        result = analyze([(-60, 1), (600, 2), (3000, 4), (3600, 5), (3660, 99)],
                         [(0, 10), (600, 30), (1200, 30), (3600, 999)])
        self.assertEqual(result["energy_kwh"], 3)
        self.assertEqual(result["energy_readings_count"], 3)
        self.assertEqual(result["used_energy_bounds"]["start"], START+timedelta(seconds=600))
        self.assertEqual(result["uncovered_seconds"], 600)
        self.assertEqual(result["max_power_w"], 30)
        self.assertEqual(result["max_power_at"], START+timedelta(seconds=600))

    def test_long_gap_is_valid_counter_increment(self):
        result = analyze([(0, 10), (86400, 15)], end=86400)
        self.assertEqual(result["energy_kwh"], 5)
        self.assertEqual(result["covered_seconds"], 86400)
        self.assertEqual(result["uncovered_seconds"], 0)
        self.assertTrue(any("ciaglosc" in note for note in result["assumptions"]))

    def test_duplicates_conflicts_and_units(self):
        result = analyze([(0, 10), (0, 10), (3600, 12)])
        self.assertEqual(result["energy_kwh"], 2)
        self.assertEqual(result["energy_readings_count"], 2)
        with self.assertRaisesRegex(ValueError, "Sprzeczne"):
            analyze([(0, 10), (0, 11), (3600, 12)])
        for metric, unit in (("energy", "Wh"), ("power", "kW")):
            data = history([(0, 1)], metric, unit)
            self.assertTrue(data.quality["blocking_reasons"])
            self.assertEqual(data.quality["issues"][0]["code"], "invalid_unit")


class ElectricityApiTests(unittest.TestCase):
    call = api_tests.AnalyticsApiTests.call

    def setUp(self):
        api_tests.AnalyticsApiTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE devices SET typ='power_strip' WHERE id='sensor'")

    def query(self, **changes):
        return dict(self.period, device_id="sensor") | changes

    def insert(self, points, metric="energy", unit=None, device="sensor"):
        unit = unit if unit is not None else {"energy": "kWh", "power": "W"}[metric]
        with closing(sqlite3.connect(self.path)) as db, db:
            db.executemany("INSERT INTO measurements(device_id,metric,value,unit,ts) VALUES (?,?,?,?,?)",
                           [(device, metric, value, unit, (START+timedelta(seconds=at)).isoformat()) for at, value in points])

    def test_endpoint_numbers_quality_source_and_no_write(self):
        self.insert([(-60, 0), (0, 10), (1800, 12), (1800, 12), (3600, 13), (3660, 99)])
        self.insert([(0, 40), (300, 60), (3600, 1000)], metric="power")
        self.insert([(0, 100), (3600, 1000)], device="other")
        before = hashlib.sha256(self.path.read_bytes()).digest()
        status, data = self.call("electricity", query=self.query(price_per_kwh=1.25))
        self.assertEqual(status, 200, data)
        self.assertEqual(data["energy_kwh"], 3)
        self.assertEqual(data["estimated_cost"], 3.75)
        self.assertEqual(data["max_power_w"], 60)
        self.assertEqual(data["quality"]["energy"]["duplicate_rows"], 1)
        self.assertEqual(data["source"]["kind"], "synthetic")
        self.assertEqual(data["scope"], "whole_power_strip")
        self.assertFalse(data["energy_simulated"])
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())

    def test_power_only_no_counter_and_reset(self):
        self.insert([(10, 70)], metric="power")
        status, data = self.call("electricity", query=self.query())
        self.assertEqual(status, 200, data)
        self.assertIsNone(data["energy_kwh"])
        self.assertEqual(data["max_power_w"], 70)
        self.insert([(0, 12), (3600, 1)])
        _, data = self.call("electricity", query=self.query(price_per_kwh=2))
        self.assertIsNone(data["energy_kwh"])
        self.assertEqual(data["skipped_pairs_count"], 1)
        self.assertEqual(data["uncovered_seconds"], 3600)

    def test_wrong_device_type_even_if_named_listwa(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("INSERT INTO devices(id,nazwa,typ,online) VALUES ('listwa','listwa','heat_meter',0)")
        for device in ("listwa", "other", "missing"):
            self.assertEqual(self.call("electricity", query=self.query(device_id=device))[0], 422)

    def test_conflicts_at_closing_boundary_and_invalid_units(self):
        self.insert([(0, 10), (3600, 11), (3600, 12)])
        status, data = self.call("electricity", query=self.query())
        self.assertEqual(status, 409, data)
        self.assertEqual(data["error"]["code"], "ANALYTICS_DATA_QUALITY")
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM measurements WHERE metric='energy'")
        self.insert([(0, 30)], metric="power", unit="kW")
        status, data = self.call("electricity", query=self.query())
        self.assertEqual(status, 409)
        self.assertIn("invalid_unit", data["error"]["message"])

    def test_validation_auth_and_combined_record_limit(self):
        for changes in ({"price_per_kwh": -1}, {"price_per_kwh": "nan"}, {"price_per_kwh": "inf"},
                        {"from": "2026-01-01T08:00:00"}, {"to": self.period["from"]}, {"database_path": "x"}):
            self.assertEqual(self.call("electricity", query=self.query(**changes))[0], 422)
        self.assertEqual(self.call("electricity", query=self.query(), token=None)[0], 401)
        self.assertEqual(self.call("electricity", query=self.query(), token=api_tests.create_access_token({"sub": "2"}))[0], 403)
        self.assertEqual(self.call("electricity", query=self.query(to="2027-01-01T00:00:00Z"))[0], 413)
        self.insert([(0, 1)])
        self.insert([(0, 10)], metric="power")
        self.service = replace(self.service, max_records=1)
        self.assertEqual(self.call("electricity", query=self.query())[0], 413)


if __name__ == "__main__":
    unittest.main()

from contextlib import closing
from dataclasses import replace
import hashlib
import sqlite3
import unittest

from tests import test_electricity as electricity_tests
from tests import test_analytics_api as api_tests


class HeatApiTests(unittest.TestCase):
    call = api_tests.AnalyticsApiTests.call
    insert = electricity_tests.ElectricityApiTests.insert

    def query(self, **changes):
        return dict(self.period, device_id="sensor", ttl_seconds=120) | changes

    def setUp(self):
        api_tests.AnalyticsApiTests.setUp(self)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE devices SET typ='heat_meter' WHERE id='sensor'")

    def test_heat_summary_and_readonly(self):
        self.insert([(0, 10), (1200, 12), (2400, 1), (3600, 4)])
        self.insert([(0, 500), (1800, 900), (3600, 9999)], metric="power")
        self.insert([(0, 55), (1800, 65)], metric="temperature_1", unit="\u00b0C")
        self.insert([(0, 30)], metric="temperature_2", unit="\u00b0C")
        self.insert([(0, 2)], metric="flow", unit="l/min")
        before = hashlib.sha256(self.path.read_bytes()).digest()
        status, result = self.call("heat", query=self.query())
        self.assertEqual(status, 200, result)
        self.assertEqual(result["energy_kwh"], 5)
        self.assertEqual(result["counter_drops_count"], 1)
        self.assertEqual(result["covered_seconds"], 2400)
        self.assertEqual(result["max_power_w"], 900)
        self.assertEqual(result["measurements"]["temperature_1"]["maximum"], 65)
        self.assertEqual(result["measurements"]["flow"]["last_value"], 2)
        self.assertEqual(result["energy_kind"], "thermal")
        self.assertFalse(result["energy_simulated"])
        self.assertEqual(result["source"]["kind"], "synthetic")
        self.assertNotIn("estimated_cost", result)
        self.assertEqual(before, hashlib.sha256(self.path.read_bytes()).digest())

    def test_empty_and_type_separation(self):
        status, result = self.call("heat", query=self.query())
        self.assertEqual(status, 200, result)
        self.assertIsNone(result["energy_kwh"])
        self.assertIsNone(result["measurements"]["flow"]["maximum"])
        self.assertEqual(self.call("electricity", query=self.query())[0], 422)
        self.assertEqual(self.call("heat", query=self.query(device_id="other"))[0], 422)

    def test_quality_auth_validation_and_limits(self):
        self.assertEqual(self.call("heat", query=self.query(), token=None)[0], 401)
        self.assertEqual(self.call("heat", query=self.query(price_per_kwh=1))[0], 422)
        self.assertEqual(self.call("heat", query=self.query(to="2027-01-01T00:00:00Z"))[0], 413)
        self.insert([(0, 10), (3600, 12)])
        self.service = replace(self.service, max_records=1)
        self.assertEqual(self.call("heat", query=self.query())[0], 413)
        self.service = replace(self.service, max_records=20000)
        self.insert([(0, 1)], metric="flow", unit="wrong")
        self.assertEqual(self.call("heat", query=self.query())[0], 409)

    def test_conflicting_counter_blocks_summary(self):
        self.insert([(0, 10), (3600, 12), (3600, 13)])
        self.assertEqual(self.call("heat", query=self.query())[0], 409)

    def test_ttl_required_positive_and_finite(self):
        query = self.query(); del query["ttl_seconds"]
        self.assertEqual(self.call("heat", query=query)[0], 422)
        for value in (0, -1, "nan", "inf"):
            self.assertEqual(self.call("heat", query=self.query(ttl_seconds=value))[0], 422)

    def test_weighted_delta_previous_report_and_negative_duration(self):
        self.insert([(-60, 50), (60, 20), (240, 40)], metric="temperature_1", unit="\u00b0C")
        self.insert([(0, 30), (180, 50)], metric="temperature_2", unit="\u00b0C")
        status, result = self.call("heat", query=self.query(ttl_seconds=180, to="2026-01-01T08:10:00Z"))
        self.assertEqual(status, 200, result)
        t1 = result["measurements"]["temperature_1"]
        self.assertEqual(t1["readings_count"], 2)  # predecessor affects time, not sample count
        self.assertAlmostEqual(t1["time_weighted_mean"], (50*60+20*180+40*180)/420)
        delta = result["temperature_difference"]
        self.assertEqual((delta["minimum"], delta["maximum"]), (-30, 20))
        self.assertAlmostEqual(delta["time_weighted_mean"], -3000/360)
        self.assertEqual(delta["negative_seconds"], 300)
        self.assertEqual(delta["known_seconds"], 360)
        self.assertEqual(delta["unknown_seconds"], 240)
        self.assertEqual(delta["coverage_percent"], 60)
        self.assertNotIn("failure", delta)
        self.assertIsNone(result["energy_kwh"])
        self.assertIsNone(result["measurements"]["flow"]["time_weighted_mean"])

    def test_independent_expiry_and_no_forward_backfill(self):
        self.insert([(0, 50)], metric="temperature_1", unit="\u00b0C")
        self.insert([(60, 30)], metric="temperature_2", unit="\u00b0C")
        _, result = self.call("heat", query=self.query(to="2026-01-01T08:10:00Z"))
        self.assertEqual(result["temperature_difference"]["known_seconds"], 60)
        self.assertEqual(result["temperature_difference"]["time_weighted_mean"], 20)
        self.insert([(100, 55)], metric="temperature_1", unit="\u00b0C")
        _, result = self.call("heat", query=self.query(to="2026-01-01T08:10:00Z"))
        delta = result["temperature_difference"]
        self.assertEqual(delta["known_seconds"], 120)  # t2 expires at 180, not 220
        self.assertAlmostEqual(delta["time_weighted_mean"], (20*40+25*80)/120)

    def test_missing_temperature_does_not_block_other_data(self):
        self.insert([(0, 1), (3600, 2)])
        self.insert([(0, 3), (60, 6)], metric="flow", unit="l/min")
        self.insert([(0, 50)], metric="temperature_1", unit="\u00b0C")
        status, result = self.call("heat", query=self.query())
        self.assertEqual(status, 200, result)
        self.assertEqual(result["energy_kwh"], 1)
        self.assertEqual(result["measurements"]["flow"]["time_weighted_mean"], 5)
        delta = result["temperature_difference"]
        for key in ("minimum", "maximum", "time_weighted_mean", "negative_seconds"):
            self.assertIsNone(delta[key])
        self.assertEqual(delta["known_seconds"], 0)
        self.assertEqual(delta["unknown_seconds"], 3600)

    def test_previous_temperature_conflict_and_units_block(self):
        self.insert([(-60, 40), (-60, 50)], metric="temperature_1", unit="\u00b0C")
        self.assertEqual(self.call("heat", query=self.query())[0], 409)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM measurements WHERE metric='temperature_1'")
        self.insert([(0, 50)], metric="temperature_2", unit="F")
        self.assertEqual(self.call("heat", query=self.query())[0], 409)

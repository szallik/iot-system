import unittest
from datetime import timedelta
from contextlib import redirect_stdout
from io import StringIO

from app.analytics.core import Interval, Presence
from app.analytics.demo import print_energy_comparison
from app.analytics.energy import ReceiverModel, compare_energy
from app.analytics.scenarios import energy_scenarios
from tests.test_analytics import at, timeline


class EnergyTests(unittest.TestCase):
    def compare(self, intervals, on=60, off=2, delay=2, short=2):
        return compare_energy(intervals, ReceiverModel(on, off), timedelta(minutes=delay),
                              timedelta(minutes=short))

    def test_sixty_watts_one_hour(self):
        result = self.compare(timeline([(0, True)], end=60, freshness=61))
        self.assertEqual(result.baseline_wh, 60)
        self.assertEqual(result.strategy_wh, 60)
        self.assertEqual(result.difference_wh, 0)
        self.assertEqual(result.difference_percent, 0)
        self.assertEqual(result.shutdown_count, 0)
        self.assertEqual(result.restart_count, 0)
        self.assertTrue(result.simulated)

    def test_half_hour_on_half_hour_standby(self):
        result = self.compare(timeline([(0, True), (28, False)], end=60, freshness=61))
        self.assertEqual(result.strategy_wh, 31)  # 60 * .5 + 2 * .5
        self.assertEqual(result.baseline_wh, 60)
        self.assertEqual(result.difference_wh, 29)
        self.assertAlmostEqual(result.difference_percent, 100 * 29 / 60)
        self.assertEqual(result.shutdown_count, 1)
        self.assertEqual(result.restart_count, 0)  # No synthetic return at period end.

    def test_power_validation(self):
        for invalid in (-1, float("nan"), float("inf"), -float("inf"), True, "60", None):
            for on, off in ((invalid, 2), (60, invalid)):
                with self.subTest(on=on, off=off), self.assertRaises(ValueError):
                    ReceiverModel(on, off)
        self.assertEqual(ReceiverModel(0, 0).off_watts, 0)

    def test_zero_baseline(self):
        for off, expected in ((0, 0), (2, 1)):
            result = self.compare(timeline([(0, False)], end=60, freshness=61),
                                  on=0, off=off, delay=30)
            self.assertEqual(result.baseline_wh, 0)
            self.assertEqual(result.strategy_wh, expected)
            self.assertEqual(result.difference_wh, -expected)
            self.assertIsNone(result.difference_percent)

    def test_off_power_can_exceed_on_power(self):
        result = self.compare(timeline([(0, False)], end=60, freshness=61),
                              on=2, off=60, delay=30)
        self.assertEqual(result.strategy_wh, 31)
        self.assertEqual(result.difference_wh, -29)

    def test_all_unknown_stays_on_and_has_zero_coverage(self):
        result = self.compare(timeline([], end=60))
        self.assertEqual(result.strategy_wh, 60)
        self.assertEqual(result.known_time_share, 0)
        self.assertEqual(result.unknown_difference_wh, 0)

    def test_categories_hand_calculation_and_uncertain_return(self):
        # OFF at minute 2; known absent OFF 2..3, unknown OFF 3..8; present 8..10.
        result = self.compare(timeline([(0, False), (8, True)], end=10, freshness=3))
        self.assertEqual(result.baseline_wh, 10)
        self.assertAlmostEqual(result.strategy_wh, 4.2)  # 4 min ON + 6 min standby
        self.assertAlmostEqual(result.known_difference_wh, 58 / 60)
        self.assertAlmostEqual(result.unknown_difference_wh, 58 * 5 / 60)
        self.assertAlmostEqual(result.difference_wh, 5.8)
        self.assertEqual(result.known_time_share, .5)
        self.assertEqual(result.restart_count, 1)
        self.assertEqual(len(result.uncertain_returns), 1)
        self.assertEqual(result.certain_returns, ())

    def test_uncertain_short_return_separate_from_certain(self):
        # OFF 2..3.5 includes UNKNOWN 3..3.5, so <=2 min is only observed latency.
        result = self.compare(timeline([(0, False), (3.5, True)], end=5, freshness=3))
        self.assertEqual(result.short_returns, ())
        self.assertEqual(len(result.uncertain_short_returns), 1)
        self.assertTrue(result.uncertain_short_returns[0].uncertain)
        # Legacy field retains observed-time definition, with uncertainty on ReturnEvent.
        self.assertEqual(result.simulation.short_returns, (at(3.5),))

    def test_uncertainty_survives_absent_recovery_and_resets_after_return(self):
        result = self.compare(timeline([(0, False), (6, False), (8, True),
                                        (9, False), (11, False), (12, True)],
                                       end=14, freshness=3))
        self.assertEqual([r.uncertain for r in result.simulation.return_events], [True, False])
        self.assertEqual(len(result.short_returns), 1)
        self.assertEqual(len(result.uncertain_returns), 1)

    def test_unknown_before_off_does_not_make_return_uncertain(self):
        result = self.compare(timeline([(5, False), (7, False), (8, True)], end=10))
        self.assertEqual(len(result.short_returns), 1)
        self.assertEqual(result.uncertain_returns, ())

    def test_scenario_expected_times_and_energy(self):
        expected = {
            "krotkie_przerwy": [(6, 6, 6, 6, 0), (0, 0, 0, 0, 0), (0, 0, 0, 0, 0)],
            "dluga_nieobecnosc": [(38, 1, 1, 0, 0), (35, 1, 1, 0, 0), (30, 1, 1, 0, 0)],
            "stala_obecnosc": [(0, 0, 0, 0, 0)] * 3,
            "luki_danych": [(23, 1, 1, 0, 1), (20, 1, 1, 0, 1), (15, 1, 1, 0, 1)],
        }
        for case in energy_scenarios():
            for delay, (off, shutdowns, restarts, short, uncertain) in zip((2, 5, 10), expected[case.name]):
                with self.subTest(scenario=case.name, delay=delay):
                    result = self.compare(case.intervals(), delay=delay)
                    self.assertEqual(result.baseline_wh, 60)
                    self.assertAlmostEqual(result.strategy_wh, 60 - 58 * off / 60)
                    self.assertEqual(result.shutdown_count, shutdowns)
                    self.assertEqual(result.restart_count, restarts)
                    self.assertEqual(len(result.short_returns), short)
                    self.assertEqual(len(result.uncertain_returns), uncertain)
                    self.assertEqual(result.simulation.off_duration, timedelta(minutes=off))
                    self.assertAlmostEqual(result.known_time_share, 49 / 60 if uncertain else 1)
                    self.assertAlmostEqual(result.unknown_difference_wh, 58 * 8 / 60 if uncertain else 0)

    def test_all_category_totals_conserve_time_and_energy(self):
        for case in energy_scenarios():
            for delay in (2, 5, 10):
                result = self.compare(case.intervals(), delay=delay)
                self.assertEqual(sum((c.duration for c in result.categories), timedelta(0)), timedelta(hours=1))
                self.assertEqual(sum((c.off_duration for c in result.categories), timedelta(0)),
                                 result.simulation.off_duration)
                self.assertAlmostEqual(sum(c.baseline_wh for c in result.categories), result.baseline_wh)
                self.assertAlmostEqual(sum(c.strategy_wh for c in result.categories), result.strategy_wh)
                self.assertAlmostEqual(sum(c.difference_wh for c in result.categories), result.difference_wh)
                self.assertAlmostEqual(result.known_difference_wh + result.unknown_difference_wh,
                                       result.difference_wh)

    def test_seconds_are_not_rounded_to_minutes(self):
        intervals = [Interval(at(0), at(1), Presence.ABSENT)]
        result = compare_energy(intervals, ReceiverModel(60, 2), timedelta(seconds=30))
        self.assertAlmostEqual(result.strategy_wh, 31 / 60)

    def test_empty_period_rejected(self):
        with self.assertRaises(ValueError):
            self.compare([])

    def test_table_zero_baseline_and_uncertainty_labels(self):
        output = StringIO()
        with redirect_stdout(output):
            print_energy_comparison(ReceiverModel(0, 2))
        text = output.getvalue()
        self.assertIn("ENERGIA SYMULOWANA", text)
        self.assertIn("ON = 0 W; OFF (czuwanie) = 2 W", text)
        self.assertIn("n/d", text)
        self.assertEqual(text.count("NIEPEWNY: OFF"), 3)


if __name__ == "__main__":
    unittest.main()

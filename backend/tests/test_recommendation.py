import unittest
from dataclasses import replace
from datetime import timedelta
from contextlib import redirect_stdout
from io import StringIO

from app.analytics.core import Interval, Presence
from app.analytics.energy import ReceiverModel
from app.analytics.recommendation import (
    Candidate, Constraints, evaluate_later, rank_candidates, recommend,
)
from app.analytics.recommendation_demo import print_recommendations
from app.analytics.scenarios import recommendation_scenarios
from tests.test_analytics import at, timeline


MODEL = ReceiverModel(60, 2)
LOOSE = Constraints(100, 100, 0, 100)
DELAYS = tuple(timedelta(minutes=m) for m in (1, 2, 3, 5, 7, 10, 15))


def shifted(period, days):
    return tuple(Interval(i.start + timedelta(days=days), i.end + timedelta(days=days), i.presence)
                 for i in period)


class RecommendationTests(unittest.TestCase):
    def test_no_admissible_candidate_and_specific_reasons(self):
        result = recommend([timeline([(0, False), (8, True)], end=10, freshness=20)], MODEL,
                           [timedelta(minutes=2)], Constraints(0, 0, 1, 0))
        self.assertIsNone(result.selected)
        self.assertEqual(result.ranking, ())
        self.assertIn("Wylaczenia: 1 > limit 0", result.candidates[0].rejection_reasons)
        self.assertIn("Brak rekomendacji", result.reasons[0])
        with self.assertRaises(ValueError):
            evaluate_later(result, [shifted(timeline([]), 1)])

    def test_tie_prefers_longer_delay_without_events(self):
        result = recommend([timeline([(0, True)], end=60, freshness=61)], MODEL, reversed(DELAYS), LOOSE)
        self.assertEqual(result.selected.delay, timedelta(minutes=15))
        self.assertEqual([c.delay for c in result.ranking], list(reversed(DELAYS)))

    def test_tie_priority_and_tolerance(self):
        base = Candidate(timedelta(minutes=1), 10, 0, 1, 1, 0, 1, ())
        fewer_short = replace(base, delay=timedelta(minutes=2), short_returns=0, shutdowns=5)
        fewer_off = replace(fewer_short, delay=timedelta(minutes=3), shutdowns=1)
        longer = replace(fewer_off, delay=timedelta(minutes=5), known_energy_wh=10.0000005)
        outside = replace(longer, delay=timedelta(minutes=7), known_energy_wh=10.000002)
        self.assertEqual(rank_candidates([base, fewer_short, fewer_off, longer, outside], 1e-6),
                         (longer, fewer_off, fewer_short, base, outside))
        self.assertEqual(rank_candidates([longer, fewer_off], 0)[0], fewer_off)

    def test_unknown_energy_cannot_improve_ranking(self):
        # Change ONLY UNKNOWN energy, leaving all eligibility and tie metrics fixed.
        first = Candidate(timedelta(minutes=1), 10, 100, 1, 0, 0, .8, ())
        second = replace(first, delay=timedelta(minutes=2), known_energy_wh=11, unknown_energy_wh=100)
        self.assertEqual(rank_candidates([second, first], 1e-6)[0], first)
        improved = replace(second, unknown_energy_wh=0)
        self.assertEqual(rank_candidates([improved, first], 1e-6)[0], first)
        tied = replace(second, known_energy_wh=10)
        self.assertEqual(rank_candidates([first, tied], 1e-6)[0], tied)
        improved_first = replace(first, unknown_energy_wh=0)
        self.assertEqual(rank_candidates([improved_first, tied], 1e-6)[0], tied)

    def test_insufficient_known_data_rejects_every_delay(self):
        result = recommend([timeline([], end=60)], MODEL, DELAYS, Constraints(10, 10, .1, 10))
        self.assertIsNone(result.selected)
        self.assertTrue(all(c.known_share == 0 for c in result.candidates))
        self.assertTrue(all("Udzial znanych danych" in c.rejection_reasons[0] for c in result.candidates))

    def test_uncertain_returns_are_not_certain_short_returns(self):
        result = recommend([timeline([(0, False), (3.5, True)], end=5, freshness=3)], MODEL,
                           [timedelta(minutes=2)], Constraints(0, 5, 0, 0))
        candidate = result.candidates[0]
        self.assertEqual(candidate.short_returns, 0)
        self.assertEqual(candidate.uncertain_returns, 1)
        self.assertEqual(candidate.rejection_reasons, ("Niepewne powroty: 1 > limit 0",))

    def test_limits_apply_to_whole_set_not_each_period(self):
        period = timeline([(0, False), (8, True)], end=10, freshness=20)
        result = recommend([period, shifted(period, 1)], MODEL, [timedelta(minutes=2)],
                           Constraints(0, 1, 1, 0))
        self.assertEqual(result.duration, timedelta(minutes=20))
        self.assertEqual(result.candidates[0].shutdowns, 2)
        self.assertIsNone(result.selected)

    def test_known_coverage_is_duration_weighted(self):
        known = timeline([(0, True)], end=10, freshness=20)
        unknown = shifted(timeline([], end=30), 1)
        result = recommend([known, unknown], MODEL, [timedelta(minutes=2)], LOOSE)
        self.assertEqual(result.selected.known_share, .25)
        self.assertEqual(result.selected.known_energy_wh, 10)
        self.assertEqual(result.selected.unknown_energy_wh, 30)

    def test_frozen_choice_is_independent_of_evaluation_data(self):
        training, holdout = recommendation_scenarios()
        selection = recommend([c.intervals() for c in training], MODEL, DELAYS,
                              Constraints(0, 4, .95, 0))
        original = selection.selected
        bad = evaluate_later(selection, [c.intervals() for c in holdout])
        good = evaluate_later(selection, [shifted(timeline([(0, True)], end=60, freshness=61), 10)])
        self.assertEqual(selection.selected, original)
        self.assertEqual(bad.candidate.delay, original.delay)
        self.assertEqual(good.candidate.delay, original.delay)
        self.assertFalse(bad.candidate.admissible)
        self.assertTrue(good.candidate.admissible)

    def test_demo_choices_and_later_constraints(self):
        training, holdout = recommendation_scenarios()
        for limits, expected, later_ok in [(Constraints(6, 10, .9, 1), 1, False),
                                           (Constraints(0, 4, .95, 0), 15, False)]:
            result = recommend((c.intervals() for c in training), MODEL, iter(DELAYS), limits)
            self.assertEqual(result.selected.delay, timedelta(minutes=expected))
            self.assertEqual(len(result.candidates), 7)
            self.assertEqual(result.duration, timedelta(hours=4))
            self.assertTrue(all(abs(c.known_share - 229 / 240) < 1e-12 for c in result.candidates))
            evaluation = evaluate_later(result, (c.intervals() for c in holdout))
            self.assertEqual(evaluation.duration, timedelta(hours=2))
            self.assertEqual(evaluation.candidate.admissible, later_ok)

    def test_invalid_limits_delays_periods_and_tolerance(self):
        for args in [(-1, 1, .5, 1), (1, True, .5, 1), (1, 1, float("nan"), 1),
                     (1, 1, 1.1, 1), (1, 1, .5, -1)]:
            with self.assertRaises(ValueError):
                Constraints(*args)
        period = timeline([(0, True)])
        for periods, delays in [([], DELAYS), ([[]], DELAYS), ([period, period], DELAYS),
                                ([period], []), ([period], [timedelta(0)])]:
            with self.assertRaises(ValueError):
                recommend(periods, MODEL, delays, LOOSE)
        for tolerance in (-1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                recommend([period], MODEL, DELAYS, LOOSE, energy_tolerance_wh=tolerance)
        selection = recommend([period], MODEL, DELAYS, LOOSE)
        with self.assertRaises(ValueError):
            evaluate_later(selection, [period])

    def test_demo_discloses_synthetic_data_and_rejection(self):
        output = StringIO()
        with redirect_stdout(output):
            print_recommendations(MODEL)
        text = output.getvalue()
        self.assertIn("DANE SYNTETYCZNE", text)
        self.assertIn("4 h", text)
        self.assertIn("2 h", text)
        self.assertEqual(text.count("OCENA ZAMROZONEGO USTAWIENIA"), 2)
        self.assertIn("NIESPELNIONE", text)


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone

from app.analytics.adaptive import DataRequirements, evaluate_adaptive, group_evidence, train_adaptive
from app.analytics.adaptive_demo import run_history_comparison
from app.analytics.contexts import AdaptiveDelay, ContextGroup, ContextPartition, default_contexts
from app.analytics.core import Interval, Presence, Report, reconstruct, simulate
from app.analytics.energy import ReceiverModel
from app.analytics.history_demo import history_scenarios
from app.analytics.recommendation import Constraints, rank_candidates


BASE = datetime(2026, 1, 5, 0, tzinfo=timezone.utc)  # Monday, 01:00 Warsaw
MODEL = ReceiverModel(60, 2)
LOOSE = Constraints(100, 100, 0, 100)
ONE = ContextPartition((ContextGroup("all", tuple(range(7)), (("00:00", "24:00"),)),))


def minute(m):
    return BASE + timedelta(minutes=m)


def history(rows, end=60):
    return reconstruct([Report(minute(m), occupied) for m, occupied in rows], BASE, minute(end),
                       timedelta(days=2))


def later(period, days=1):
    return tuple(Interval(i.start + timedelta(days=days), i.end + timedelta(days=days), i.presence)
                 for i in period)


class AdaptiveTests(unittest.TestCase):
    def fit(self, periods, **kwargs):
        defaults = dict(contexts=ONE, choices=(timedelta(minutes=1), timedelta(minutes=5)),
                        requirements=DataRequirements(1, 1, timedelta(minutes=1)))
        defaults.update(kwargs)
        return train_adaptive(periods, MODEL, LOOSE, **defaults)

    def test_no_periods_returns_no_recommendation_with_fallback_reasons(self):
        result = self.fit([])
        self.assertIsNone(result.policy)
        self.assertTrue(result.evidence[0].uses_default)
        self.assertIn("Brak wiarygodnych danych", result.evidence[0].fallback_reasons[0])
        self.assertEqual(result.configuration()["training_periods_utc"], [])
        self.assertEqual(result.configuration()["status"], "no_recommendation")
        with self.assertRaises(ValueError):
            evaluate_adaptive(result, [history([])], LOOSE)

    def test_all_unknown_uses_default_but_does_not_relax_quality(self):
        period = history([])
        result = train_adaptive([period], MODEL, Constraints(100, 100, .5, 100),
                                contexts=ONE, default_delay=timedelta(minutes=7))
        self.assertTrue(result.evidence[0].uses_default)
        self.assertEqual(len(result.selection.candidates), 1)
        self.assertEqual(result.selection.candidates[0].policy.delays, (timedelta(minutes=7),))
        self.assertIsNone(result.policy)

    def test_insufficient_days_or_episodes_uses_configurable_default(self):
        period = history([(0, True), (10, False), (15, True)])
        result = self.fit([period], requirements=DataRequirements(2, 2), default_delay=timedelta(minutes=7))
        evidence = result.evidence[0]
        self.assertEqual(evidence.complete_episodes, 1)
        self.assertEqual(len(evidence.reliable_dates), 1)
        self.assertEqual(len(evidence.fallback_reasons), 2)
        self.assertEqual(result.policy.delays, (timedelta(minutes=7),))
        self.assertEqual(len(result.selection.candidates), 1)

    def test_partial_day_does_not_satisfy_reliable_day_requirement(self):
        result = self.fit([history([(0, True)], end=10)],
                          requirements=DataRequirements(1, 0, timedelta(minutes=30)))
        self.assertEqual(result.evidence[0].reliable_dates, ())
        self.assertEqual(result.evidence[0].known_seconds, 600)

    def test_unknown_and_truncated_episodes_are_not_complete(self):
        period = (
            Interval(minute(0), minute(5), Presence.ABSENT),  # Unknown start.
            Interval(minute(5), minute(10), Presence.PRESENT),
            Interval(minute(10), minute(15), Presence.ABSENT),
            Interval(minute(15), minute(20), Presence.UNKNOWN),
            Interval(minute(20), minute(25), Presence.ABSENT),
            Interval(minute(25), minute(30), Presence.PRESENT),
            Interval(minute(30), minute(32), Presence.ABSENT),
            Interval(minute(32), minute(35), Presence.ABSENT),  # Continuous split, one episode.
            Interval(minute(35), minute(40), Presence.PRESENT),
            Interval(minute(40), minute(45), Presence.ABSENT),  # Unknown end.
        )
        evidence = group_evidence([period], ONE, DataRequirements(1, 2))
        self.assertEqual(evidence[0].complete_episodes, 1)
        self.assertTrue(evidence[0].uses_default)

    def test_group_boundary_preserves_countdown_and_episode_start_context(self):
        contexts = default_contexts()
        start = datetime(2026, 1, 5, 14, 58, tzinfo=timezone.utc)  # Local 15:58
        period = (Interval(start, start + timedelta(minutes=1), Presence.PRESENT),
                  Interval(start + timedelta(minutes=1), start + timedelta(minutes=15), Presence.ABSENT),
                  Interval(start + timedelta(minutes=15), start + timedelta(minutes=20), Presence.PRESENT))
        policy = AdaptiveDelay(contexts, tuple(timedelta(minutes=m) for m in (10, 1, 2, 5)))
        result = simulate(period, policy)
        self.assertEqual(result.shutdowns, (start + timedelta(minutes=11),))
        evidence = group_evidence([period], contexts, DataRequirements(0, 0))
        self.assertEqual([e.complete_episodes for e in evidence], [1, 0, 0, 0])

    def test_global_limits_reject_combination_valid_for_each_group_separately(self):
        groups = ContextPartition((
            ContextGroup("am", tuple(range(7)), (("00:00", "12:00"),)),
            ContextGroup("pm", tuple(range(7)), (("12:00", "24:00"),)),
        ))
        period = history([(0, True), (10, False), (13, True), (720, False), (723, True)], end=1440)
        result = train_adaptive([period], MODEL, Constraints(1, 1, 1, 0), contexts=groups,
                                choices=(timedelta(minutes=1), timedelta(minutes=5)),
                                requirements=DataRequirements(1, 1))
        self.assertEqual(len(result.selection.candidates), 4)
        both_short = next(c for c in result.selection.candidates if c.policy.delays == (timedelta(minutes=1),) * 2)
        self.assertEqual(both_short.short_returns, 2)
        self.assertEqual(both_short.shutdowns, 2)
        self.assertFalse(both_short.admissible)
        self.assertTrue(result.selection.selected.admissible)
        self.assertEqual(result.selection.selected.short_returns, 1)

    def test_no_admissible_configuration_is_not_silently_replaced(self):
        period = history([(0, True), (10, False), (13, True)])
        result = train_adaptive([period], MODEL, Constraints(0, 0, 1, 0), contexts=ONE,
                                choices=(timedelta(minutes=1),), requirements=DataRequirements(1, 1))
        self.assertIsNone(result.policy)
        self.assertIn("Brak rekomendacji", result.reasons[0])

    def test_holdout_cannot_mutate_training_or_refit(self):
        period = history([(0, True), (10, False), (20, True)])
        training = self.fit([period])
        artifact = json.dumps(training.configuration(), sort_keys=True)
        good = evaluate_adaptive(training, [later(history([(0, True)]))], LOOSE)
        bad = evaluate_adaptive(training, [later(history([]))], Constraints(0, 0, 1, 0))
        self.assertIs(good.candidate.policy, training.policy)
        self.assertIs(bad.candidate.policy, training.policy)
        self.assertTrue(good.candidate.admissible)
        self.assertFalse(bad.candidate.admissible)
        self.assertEqual(json.dumps(training.configuration(), sort_keys=True), artifact)
        self.assertEqual(self.fit([period]).configuration(), training.configuration())
        with self.assertRaises(FrozenInstanceError):
            training.policy.delays = (timedelta(minutes=99),)
        with self.assertRaises(ValueError):
            evaluate_adaptive(training, [period], LOOSE)

    def test_artifact_contains_training_evidence_and_configuration(self):
        training = self.fit([history([(0, True), (10, False), (20, True)])])
        artifact = json.loads(json.dumps(training.configuration()))
        self.assertEqual(artifact["receiver_model_watts"], {"on_watts": 60, "off_watts": 2})
        self.assertEqual(artifact["constraints"]["max_shutdowns"], 100)
        self.assertEqual(artifact["training_periods_utc"][0][0], BASE.isoformat())
        self.assertEqual(artifact["groups"][0]["complete_episodes"], 1)
        self.assertFalse(artifact["groups"][0]["uses_default"])
        self.assertEqual(artifact["strategy"]["groups"][0]["delay_seconds"], 60)

    def test_unknown_energy_does_not_change_adaptive_ranking(self):
        training = self.fit([history([(0, True), (10, False), (20, True)])])
        candidates = [replace(c, unknown_energy_wh=999 if c == training.selection.selected else 0)
                      for c in training.selection.candidates]
        self.assertEqual(rank_candidates(candidates, 1e-6)[0].identifier, training.selection.selected.identifier)

    def test_configurable_contexts_and_validation(self):
        for groups in [(), (ContextGroup("gap", (0,), (("08:00", "16:00"),)),),
                       (ONE.groups[0], ONE.groups[0])]:
            with self.assertRaises(ValueError):
                ContextPartition(groups)
        with self.assertRaises(ValueError):
            AdaptiveDelay(ONE, ())
        with self.assertRaises(ValueError):
            DataRequirements(-1, 1)
        with self.assertRaises(ValueError):
            self.fit([history([(0, True), (5, False), (20, True)])], max_configurations=1)

    def test_deterministic_generator_and_training(self):
        first = history_scenarios()
        second = history_scenarios()
        self.assertEqual(first, second)
        self.assertLess(max(r.at for r in first[0].reports), first[1].start)
        self.assertEqual(first[0].end, first[1].start)
        self.assertEqual(first[0].end - first[0].start, timedelta(days=14))
        self.assertEqual(first[1].end - first[1].start, timedelta(days=7))
        period = history([(0, True), (10, False), (20, True)])
        forward = self.fit([period])
        reverse = self.fit([period], choices=(timedelta(minutes=5), timedelta(minutes=1)))
        self.assertEqual(forward.configuration(), reverse.configuration())

    def test_local_days_handle_dst_without_resetting_evidence(self):
        start = datetime(2026, 10, 24, 22, tzinfo=timezone.utc)
        end = datetime(2026, 10, 25, 23, tzinfo=timezone.utc)
        evidence = group_evidence([(Interval(start, end, Presence.PRESENT),)], ONE,
                                  DataRequirements(1, 0, timedelta(hours=24)))
        self.assertEqual(evidence[0].reliable_dates, ("2026-10-25",))
        self.assertEqual(evidence[0].known_seconds, 25 * 3600)

    def test_multiday_training_and_frozen_evaluation_regression(self):
        _, _, fixed, adaptive, _, rows = run_history_comparison(MODEL)
        self.assertEqual(len(adaptive.selection.candidates), 625)
        self.assertEqual(fixed.selected.delay, timedelta(minutes=5))
        self.assertEqual(adaptive.policy.delays, tuple(timedelta(minutes=m) for m in (1, 2, 2, 1)))
        self.assertEqual([len(e.reliable_dates) for e in adaptive.evidence], [10, 10, 4, 4])
        expected = [(6504.3, 41, 7, True), (6464.666666666667, 41, 4, True),
                    (6343.833333333333, 55, 11, False)]
        for (_, candidate), (energy, off, short, admissible) in zip(rows, expected):
            self.assertAlmostEqual(candidate.known_energy_wh + candidate.unknown_energy_wh, energy)
            self.assertEqual(candidate.shutdowns, off)
            self.assertEqual(candidate.short_returns, short)
            self.assertEqual(candidate.admissible, admissible)


if __name__ == "__main__":
    unittest.main()

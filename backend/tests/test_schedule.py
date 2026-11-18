import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from app.analytics.core import (
    ControlState, DelayController, FixedDelayController, Interval, Presence, simulate,
)
from app.analytics.energy import ReceiverModel, compare_energy
from app.analytics.recommendation import Constraints, evaluate_later, recommend, rank_candidates
from app.analytics.scenarios import schedule_scenarios
from app.analytics.strategies import FixedDelay, ScheduleWindow, WeeklySchedule, example_schedule


def ts(value):
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def minutes(value):
    return timedelta(minutes=value)


class ScheduleTests(unittest.TestCase):
    def test_work_hour_boundaries_and_weekend(self):
        policy = example_schedule()
        for value, expected in [
            ("2026-01-05T06:59:59+00:00", 2),  # Monday local 07:59:59
            ("2026-01-05T07:00:00+00:00", 10),
            ("2026-01-05T14:59:59+00:00", 10),
            ("2026-01-05T15:00:00+00:00", 2),
            ("2026-01-09T09:00:00+00:00", 10),  # Friday
            ("2026-01-10T09:00:00+00:00", 2),
            ("2026-01-11T09:00:00+00:00", 2),
            ("2026-07-06T06:00:00+00:00", 10),  # Summer UTC+2
            ("2026-07-06T14:00:00+00:00", 2),
        ]:
            with self.subTest(value=value):
                self.assertEqual(policy.delay_at(ts(value)), minutes(expected))

    def test_countdown_crossing_work_start_is_not_extended(self):
        machine = DelayController(example_schedule())
        start = ts("2026-01-05T06:59:00+00:00")
        machine.update(start, Presence.ABSENT)
        machine.update(start + minutes(1), Presence.ABSENT)
        self.assertEqual(machine.deadline, start + minutes(2))
        self.assertIs(machine.update(start + minutes(2), Presence.ABSENT), False)

    def test_countdown_crossing_work_end_is_not_shortened(self):
        start = ts("2026-01-05T14:55:00+00:00")
        result = simulate([Interval(start, start + minutes(20), Presence.ABSENT)], example_schedule())
        self.assertEqual(result.shutdowns, (start + minutes(10),))
        self.assertEqual(result.transitions[0].deadline, start + minutes(10))
        self.assertIsNone(result.delay)
        self.assertEqual(result.strategy, example_schedule())

    def test_unknown_restarts_using_current_schedule(self):
        start = ts("2026-01-05T14:55:00+00:00")
        result = simulate([
            Interval(start, start + minutes(4), Presence.ABSENT),
            Interval(start + minutes(4), start + minutes(6), Presence.UNKNOWN),
            Interval(start + minutes(6), start + minutes(20), Presence.ABSENT),
        ], example_schedule())
        self.assertEqual(result.shutdowns, (start + minutes(8),))
        waits = [t for t in result.transitions if t.current == ControlState.WAITING]
        self.assertEqual([t.deadline - t.at for t in waits], [minutes(10), minutes(2)])

    def test_return_or_unknown_exactly_at_scheduled_deadline(self):
        start = ts("2026-01-05T14:55:00+00:00")
        for state in (Presence.PRESENT, Presence.UNKNOWN):
            result = simulate([Interval(start, start + minutes(10), Presence.ABSENT),
                               Interval(start + minutes(10), start + minutes(20), state)],
                              example_schedule())
            self.assertEqual(result.shutdowns, ())

    def test_spring_gap_has_no_invented_local_times(self):
        policy = WeeklySchedule(minutes(2), (ScheduleWindow((6,), "02:00", "03:00", minutes(10)),))
        # Warsaw jumps 01:59:59 -> 03:00:00 on 29 March 2026.
        self.assertEqual(policy.delay_at(ts("2026-03-29T00:59:59+00:00")), minutes(2))
        self.assertEqual(policy.delay_at(ts("2026-03-29T01:00:00+00:00")), minutes(2))

    def test_autumn_repeated_hour_matches_both_instants(self):
        policy = WeeklySchedule(minutes(2), (ScheduleWindow((6,), "02:00", "03:00", minutes(10)),))
        for value in ("2026-10-25T00:30:00+00:00", "2026-10-25T01:30:00+00:00"):
            self.assertEqual(policy.delay_at(ts(value)), minutes(10))
        self.assertEqual(policy.delay_at(ts("2026-10-25T02:00:00+00:00")), minutes(2))

    def test_dst_countdowns_and_energy_use_elapsed_utc_time(self):
        policy = WeeklySchedule(minutes(2), (ScheduleWindow((6,), "01:00", "03:00", minutes(10)),))
        for value in ("2026-03-29T00:55:00+00:00", "2026-10-25T00:55:00+00:00"):
            start = ts(value)
            result = compare_energy([Interval(start, start + minutes(20), Presence.ABSENT)],
                                    ReceiverModel(60, 2), policy)
            self.assertEqual(result.simulation.shutdowns, (start + minutes(10),))
            self.assertAlmostEqual(result.strategy_wh, 10 + 1 / 3)
            self.assertEqual(result.baseline_wh, 20)

    def test_window_validation(self):
        for start, end in [("8:00", "16:00"), ("08:60", "16:00"), ("24:00", "24:00"),
                           ("08:00", "25:00"), ("08:00", "08:00"), ("22:00", "02:00"),
                           ("08:00", "24:01"), ("-1:00", "12:00")]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                ScheduleWindow((0,), start, end, minutes(2))
        for days in ((), (-1,), (7,), (True,)):
            with self.assertRaises(ValueError):
                ScheduleWindow(days, "08:00", "16:00", minutes(2))
        for delay in (minutes(0), minutes(-1), 2):
            with self.assertRaises(ValueError):
                WeeklySchedule(delay)
            with self.assertRaises(ValueError):
                ScheduleWindow((0,), "08:00", "16:00", delay)

    def test_overlap_and_adjacent_windows(self):
        first = ScheduleWindow((0, 1), "08:00", "16:00", minutes(10))
        for other in (ScheduleWindow((1,), "15:00", "17:00", minutes(2)), first):
            with self.assertRaises(ValueError):
                WeeklySchedule(minutes(2), (first, other))
        WeeklySchedule(minutes(2), (first, ScheduleWindow((2,), "08:00", "16:00", minutes(2))))
        WeeklySchedule(minutes(2), (first, ScheduleWindow((0,), "16:00", "17:00", minutes(2))))

    def test_midnight_requires_two_daily_windows(self):
        policy = WeeklySchedule(minutes(2), (
            ScheduleWindow((0,), "22:00", "24:00", minutes(10)),
            ScheduleWindow((1,), "00:00", "02:00", minutes(10)),
        ))
        self.assertEqual(policy.delay_at(ts("2026-01-05T23:00:00+00:00")), minutes(10))
        self.assertEqual(policy.delay_at(ts("2026-01-06T01:00:00+00:00")), minutes(2))

    def test_timezone_errors_are_explicit(self):
        with self.assertRaisesRegex(ValueError, "tzdata"):
            WeeklySchedule(minutes(2), timezone_name="Invalid/Nowhere")
        with patch("app.analytics.strategies.ZoneInfo", side_effect=ZoneInfoNotFoundError):
            with self.assertRaisesRegex(ValueError, "tzdata"):
                example_schedule()
        with self.assertRaises(ValueError):
            example_schedule().delay_at(datetime(2026, 1, 1))

    def test_default_timezone_and_custom_timezone(self):
        self.assertEqual(example_schedule().timezone_name, "Europe/Warsaw")
        policy = WeeklySchedule(minutes(2), example_schedule().windows, "UTC")
        self.assertEqual(policy.delay_at(ts("2026-01-05T07:30:00+00:00")), minutes(2))
        self.assertEqual(example_schedule().delay_at(ts("2026-01-05T07:30:00+00:00")), minutes(10))
        self.assertEqual(WeeklySchedule(minutes(3)).delay_at(ts("2026-01-05T07:30:00+00:00")), minutes(3))

    def test_fixed_strategy_uses_same_machine_and_results(self):
        self.assertIs(FixedDelayController, DelayController)
        for case in schedule_scenarios():
            self.assertEqual(simulate(case.intervals(), minutes(2)),
                             simulate(case.intervals(), FixedDelay(minutes(2))))

    def test_configuration_immutable_and_frozen_on_holdout(self):
        days = [0, 1, 2, 3, 4]
        windows = [ScheduleWindow(days, "08:00", "16:00", minutes(10))]
        policy = WeeklySchedule(minutes(2), windows)
        days.clear()
        windows.clear()
        self.assertEqual(policy, example_schedule())
        config = policy.configuration
        config["windows"].clear()
        self.assertEqual(policy, example_schedule())
        with self.assertRaises(FrozenInstanceError):
            policy.default_delay = minutes(99)
        selection = recommend((c.intervals() for c in schedule_scenarios()), ReceiverModel(60, 2),
                              [policy], Constraints(100, 100, 0, 100))
        result = evaluate_later(selection, (c.intervals() for c in schedule_scenarios(later=True)))
        self.assertIs(result.candidate.policy, policy)
        self.assertEqual(result.candidate.configuration, selection.selected.configuration)
        self.assertIsNone(result.candidate.delay)

    def test_mixed_recommendation_uses_same_periods_without_unknown_bonus(self):
        policies = [minutes(2), minutes(5), minutes(10), example_schedule()]
        result = recommend((c.intervals() for c in schedule_scenarios()), ReceiverModel(60, 2),
                           policies, Constraints(100, 100, 0, 100))
        self.assertEqual(len(result.candidates), 4)
        self.assertEqual(result.duration, timedelta(hours=4))
        self.assertTrue(all(c.known_share == 1 for c in result.candidates))
        self.assertEqual(len({c.identifier for c in result.candidates}), 4)
        # No forced schedule victory; known energy alone decides with these limits.
        self.assertEqual(result.selected.policy, FixedDelay(minutes(2)))
        altered = [replace(c, unknown_energy_wh=999 if c.policy == result.selected.policy else 0)
                   for c in result.candidates]
        self.assertEqual(rank_candidates(altered, 1e-6)[0].identifier, result.selected.identifier)

    def test_schedule_ties_use_minimum_then_stable_identity(self):
        start = ts("2026-01-05T07:00:00+00:00")
        period = [Interval(start, start + minutes(60), Presence.PRESENT)]
        policies = [example_schedule(), minutes(2), minutes(10)]
        first = recommend([period], ReceiverModel(60, 2), policies, Constraints(0, 0, 1, 0))
        second = recommend([period], ReceiverModel(60, 2), reversed(policies), Constraints(0, 0, 1, 0))
        self.assertEqual(first.ranking, second.ranking)
        self.assertEqual(first.selected.policy, FixedDelay(minutes(10)))


if __name__ == "__main__":
    unittest.main()

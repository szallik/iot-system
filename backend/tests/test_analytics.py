import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from io import StringIO

from app.analytics.core import (
    ControlState, FixedDelayController, Interval, Presence, Report, reconstruct, simulate,
)
from app.analytics.demo import main, scenario, scenario_inputs


BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def at(minutes):
    return BASE + timedelta(minutes=minutes)


def timeline(rows, end=20, freshness=3):
    return reconstruct([Report(at(m), value) for m, value in rows], at(0), at(end),
                       timedelta(minutes=freshness))


class ReconstructionTests(unittest.TestCase):
    def test_expiry_and_no_backfill(self):
        result = timeline([(2, True), (7, False)], end=12)
        self.assertEqual([(i.start, i.end, i.presence) for i in result], [
            (at(0), at(2), Presence.UNKNOWN), (at(2), at(5), Presence.PRESENT),
            (at(5), at(7), Presence.UNKNOWN), (at(7), at(10), Presence.ABSENT),
            (at(10), at(12), Presence.UNKNOWN),
        ])

    def test_refresh_at_expiry_is_continuous(self):
        self.assertEqual(timeline([(0, False), (3, False)], end=6),
                         (Interval(at(0), at(6), Presence.ABSENT),))

    def test_preperiod_report_and_end_exclusion(self):
        self.assertEqual(timeline([(-1, True), (5, False)], end=5), (
            Interval(at(0), at(2), Presence.PRESENT),
            Interval(at(2), at(5), Presence.UNKNOWN),
        ))

    def test_empty_and_stale_history(self):
        expected = (Interval(at(0), at(20), Presence.UNKNOWN),)
        self.assertEqual(timeline([]), expected)
        self.assertEqual(timeline([(-10, False)]), expected)

    def test_sort_deduplicate_and_reject_conflict(self):
        self.assertEqual(timeline([(3, False), (0, True), (3, False)]),
                         timeline([(0, True), (3, False)]))
        with self.assertRaises(ValueError):
            timeline([(0, True), (0, False)])

    def test_invalid_inputs(self):
        for end, freshness in [(0, 2), (-1, 2), (2, 0), (2, -1)]:
            with self.subTest(end=end, freshness=freshness), self.assertRaises(ValueError):
                timeline([], end=end, freshness=freshness)
        with self.assertRaises(ValueError):
            Report(datetime(2026, 1, 1), True)
        with self.assertRaises(ValueError):
            Report(at(0), 1)

    def test_timezone_normalization(self):
        report = Report(at(0).astimezone(timezone(timedelta(hours=2))), True)
        self.assertEqual(report.at, at(0))
        self.assertEqual(report.at.tzinfo, timezone.utc)


class SimulationTests(unittest.TestCase):
    def test_return_exactly_at_deadline_cancels(self):
        result = simulate(timeline([(0, False), (5, True)], end=10, freshness=20),
                          timedelta(minutes=5))
        self.assertEqual(result.shutdowns, ())
        self.assertEqual(result.off_duration, timedelta(0))

    def test_short_exit_cancels_and_next_exit_starts_new_timer(self):
        result = simulate(timeline([(0, False), (1, True), (4, False)],
                                   end=10, freshness=20), timedelta(minutes=2))
        self.assertEqual(result.shutdowns, (at(6),))
        self.assertEqual(result.off_duration, timedelta(minutes=4))

    def test_gap_cancels_and_recovery_restarts(self):
        result = simulate(timeline([(0, False), (10, False), (12, False), (14, False)],
                                   end=17), timedelta(minutes=5))
        self.assertEqual(result.shutdowns, (at(15),))
        self.assertEqual(result.off_duration, timedelta(minutes=2))

    def test_expiry_exactly_at_deadline_prevents_shutdown(self):
        result = simulate(timeline([(0, False)], end=10, freshness=5), timedelta(minutes=5))
        self.assertEqual(result.shutdowns, ())

    def test_end_exactly_at_deadline_and_pending_countdown(self):
        for delay in (5, 10):
            result = simulate(timeline([(0, False)], end=5, freshness=20),
                              timedelta(minutes=delay))
            self.assertEqual(result.shutdowns, ())

    def test_off_duration_clipped_at_end(self):
        result = simulate(timeline([(0, False), (10, True)], end=10, freshness=20),
                          timedelta(minutes=2))
        self.assertEqual(result.off_duration, timedelta(minutes=8))
        self.assertTrue(result.off_at_end)
        self.assertEqual(result.short_returns, ())

    def test_short_return_inclusive_and_configurable(self):
        intervals = timeline([(0, False), (4, True), (5, True)], end=10, freshness=20)
        result = simulate(intervals, timedelta(minutes=2), timedelta(minutes=2))
        self.assertEqual(result.short_returns, (at(4),))
        self.assertFalse(result.off_at_end)
        self.assertEqual(simulate(intervals, timedelta(minutes=2),
                                  timedelta(seconds=119)).short_returns, ())

    def test_unknown_keeps_existing_off_state(self):
        result = simulate(timeline([(0, False), (8, True)], end=10), timedelta(minutes=2))
        self.assertEqual(result.shutdowns, (at(2),))
        self.assertEqual(result.off_duration, timedelta(minutes=6))
        self.assertEqual(result.off_intervals, (
            Interval(at(2), at(3), Presence.ABSENT),
            Interval(at(3), at(8), Presence.UNKNOWN),
        ))
        self.assertEqual(result.off_known_absence, timedelta(minutes=1))
        self.assertEqual(result.off_unknown, timedelta(minutes=5))
        self.assertEqual(result.off_duration, result.off_known_absence + result.off_unknown)
        self.assertEqual([(t.at, t.current) for t in result.transitions], [
            (at(0), ControlState.WAITING), (at(2), ControlState.OFF),
            (at(8), ControlState.ON),
        ])

    def test_repeated_absence_reports_do_not_restart_timer(self):
        machine = FixedDelayController(timedelta(minutes=5))
        for minute in range(5):
            self.assertIsNone(machine.update(at(minute), Presence.ABSENT))
            self.assertEqual(machine.deadline, at(5))
        self.assertIs(machine.update(at(5), Presence.ABSENT), False)
        result = simulate(timeline([(m, False) for m in range(8)], end=8, freshness=2),
                          timedelta(minutes=5))
        self.assertEqual(result.shutdowns, (at(5),))

    def test_refresh_at_shutdown_deadline_allows_shutdown(self):
        result = simulate(timeline([(0, False), (5, False)], end=8, freshness=5),
                          timedelta(minutes=5))
        self.assertEqual(result.shutdowns, (at(5),))
        self.assertEqual(result.off_known_absence, timedelta(minutes=3))

    def test_unknown_off_until_end_is_separate_and_censored(self):
        result = simulate(timeline([(0, False)], end=10, freshness=3), timedelta(minutes=2))
        self.assertEqual(result.off_known_absence, timedelta(minutes=1))
        self.assertEqual(result.off_unknown, timedelta(minutes=7))
        self.assertTrue(result.off_at_end)
        self.assertEqual(result.return_events, ())
        self.assertEqual(result.off_intervals[-1].end, at(10))

    def test_recovery_while_off_does_not_create_second_shutdown(self):
        result = simulate(timeline([(0, False), (6, False), (8, True)], end=10),
                          timedelta(minutes=2))
        self.assertEqual(result.shutdowns, (at(2),))
        self.assertEqual(result.off_known_absence, timedelta(minutes=3))
        self.assertEqual(result.off_unknown, timedelta(minutes=3))
        self.assertEqual(len(result.return_events), 1)

    def test_demo_transition_audit(self):
        result = simulate(scenario(), timedelta(minutes=2))
        self.assertEqual([(t.at.minute, t.previous.value, t.current.value, t.reason)
                          for t in result.transitions], [
            (5, "on", "waiting", "absence_countdown_started"),
            (7, "waiting", "off", "absence_delay_elapsed"),
            (8, "off", "on", "presence_restored"),
            (15, "on", "waiting", "absence_countdown_started"),
            (17, "waiting", "off", "absence_delay_elapsed"),
            (28, "off", "on", "presence_restored"),
            (30, "on", "waiting", "absence_countdown_started"),
            (32, "waiting", "suspended", "data_unknown_countdown_cancelled"),
            (40, "suspended", "waiting", "absence_countdown_started"),
            (42, "waiting", "off", "absence_delay_elapsed"),
            (52, "off", "on", "presence_restored"),
        ])

    def test_demo_off_intervals_and_return_evidence(self):
        for delay, spells, short in [
            (2, [(7, 8), (17, 28), (42, 52)], [True, False, False]),
            (5, [(20, 28), (45, 52)], [False, False]),
            (10, [(25, 28), (50, 52)], [False, True]),
        ]:
            with self.subTest(delay=delay):
                result = simulate(scenario(), timedelta(minutes=delay))
                self.assertEqual([(i.start.minute, i.end.minute) for i in result.off_intervals], spells)
                self.assertEqual([(r.shutdown_at.minute, r.at.minute) for r in result.return_events], spells)
                self.assertEqual([r.is_short for r in result.return_events], short)
                self.assertEqual(result.off_unknown, timedelta(0))
                self.assertEqual(result.off_known_absence, result.off_duration)

    def test_demo_prints_all_input_reports_and_audit(self):
        output = StringIO()
        with redirect_stdout(output):
            main()
        text = output.getvalue()
        reports, _, _, _ = scenario_inputs()
        self.assertEqual(len(reports), 51)
        self.assertEqual(text.count("occupancy="), len(reports))
        for report in reports:
            self.assertIn(f"{report.at:%H:%M:%S} occupancy={str(report.occupied):5}", text)
        for minutes in (2, 5, 10):
            self.assertIn(f"Opoznienie {minutes} min", text)
        self.assertIn("08:32:00 WAITING -> SUSPENDED", text)
        self.assertIn("OFF 08:50:00 -> powrot 08:52:00; 2 min; krotki powrot: TAK", text)

    def test_unknown_never_switches_off(self):
        result = simulate(timeline([]), timedelta(minutes=2))
        self.assertEqual(result.shutdowns, ())
        self.assertFalse(result.off_at_end)

    def test_machine_states(self):
        machine = FixedDelayController(timedelta(minutes=5))
        machine.update(at(0), Presence.ABSENT)
        self.assertEqual(machine.state, ControlState.WAITING)
        machine.update(at(2), Presence.UNKNOWN)
        self.assertEqual(machine.state, ControlState.SUSPENDED)
        self.assertIsNone(machine.deadline)
        machine.update(at(10), Presence.ABSENT)
        self.assertEqual(machine.update(at(15), Presence.ABSENT), False)
        self.assertEqual(machine.state, ControlState.OFF)
        self.assertEqual(machine.update(at(16), Presence.PRESENT), True)
        self.assertEqual(machine.state, ControlState.ON)
        with self.assertRaises(ValueError):
            machine.update(at(1), Presence.PRESENT)

    def test_validation(self):
        with self.assertRaises(ValueError):
            simulate([], timedelta(0))
        with self.assertRaises(ValueError):
            simulate([], timedelta(minutes=2), timedelta(seconds=-1))
        for intervals in [
            [Interval(at(0), at(0), Presence.ABSENT)],
            [Interval(at(0), at(2), Presence.ABSENT), Interval(at(3), at(4), Presence.ABSENT)],
        ]:
            with self.assertRaises(ValueError):
                simulate(intervals, timedelta(minutes=2))

    def test_demo_regression(self):
        for delay, times, duration, returns in [
            (2, (7, 17, 42), 22, 1), (5, (20, 45), 15, 0), (10, (25, 50), 5, 1),
        ]:
            with self.subTest(delay=delay):
                result = simulate(scenario(), timedelta(minutes=delay))
                self.assertEqual(tuple(t.minute for t in result.shutdowns), times)
                self.assertEqual(result.off_duration, timedelta(minutes=duration))
                self.assertEqual(len(result.short_returns), returns)


if __name__ == "__main__":
    unittest.main()

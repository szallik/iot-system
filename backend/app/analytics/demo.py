"""Deterministic synthetic scenario; runnable without importing app.main."""

from datetime import datetime, timedelta, timezone

from .core import Presence, Report, reconstruct, simulate
from .energy import ReceiverModel, compare_energy
from .scenarios import energy_scenarios
from .recommendation_demo import print_recommendations
from .schedule_demo import print_schedule_comparison
from .adaptive_demo import print_adaptive_comparison


def scenario_inputs():
    start = datetime(2026, 1, 1, 8, tzinfo=timezone.utc)
    # One report per minute except the deliberate outage [31, 40).
    reports = [
        Report(start + timedelta(minutes=m), m < 5 or 8 <= m < 15 or 28 <= m < 30 or m >= 52)
        for m in range(60) if not 31 <= m < 40
    ]
    return reports, start, start + timedelta(hours=1), timedelta(minutes=2)


def scenario():
    return reconstruct(*scenario_inputs())


PRESENCE_LABELS = {
    Presence.PRESENT: "obecnosc", Presence.ABSENT: "nieobecnosc",
    Presence.UNKNOWN: "stan nieznany",
}
REASONS = {
    "presence_restored": "obecnosc: anulowanie odliczania / wlaczenie",
    "data_unknown_countdown_cancelled": "brak aktualnych danych: anulowanie odliczania",
    "absence_delay_elapsed": "uplyw opoznienia przy wiarygodnej nieobecnosci",
    "absence_countdown_started": "wiarygodna nieobecnosc: nowe pelne odliczanie",
}


def print_energy_comparison(model: ReceiverModel):
    print("\nENERGIA SYMULOWANA - jeden modelowany odbiornik, bez pomiarow gniazd listwy.")
    print(f"Model: ON = {model.on_watts:g} W; OFF (czuwanie) = {model.off_watts:g} W.")
    print("E [Wh] = P [W] * czas [s] / 3600; poczatek ON; odniesienie: zawsze ON.")
    print("Wszystkie okresy: 08:00-09:00 UTC, 2026-01-01; TTL demo 2 min; prog powrotu <= 2 min.")
    print("Minuty w opisach liczone od 08:00. Znane = PRESENT + ABSENT.")
    print("Delta = odniesienie - strategia; dodatnia wartosc oznacza mniejsza energie modelu.")
    print("Delta UNKNOWN nie potwierdza ograniczenia zbednego zuzycia. Brak wyboru najlepszej strategii.")
    for case in energy_scenarios():
        print(f"\n{case.name}: {case.description}")
        print("Wszystkie energie i delty w Wh; n/d = zerowa energia odniesienia.")
        print("min | E odn. | E strat. | delta | delta % | d.znane | d.UNKNOWN | znane % | OFF/ON | krotkie | niepewne (krotkie)")
        for delay in (2, 5, 10):
            result = compare_energy(case.intervals(), model, timedelta(minutes=delay))
            percent = "n/d" if result.difference_percent is None else f"{result.difference_percent:.2f}"
            print(f"{delay:3} | {result.baseline_wh:6.2f} | {result.strategy_wh:8.2f} | "
                  f"{result.difference_wh:5.2f} | {percent:7} | {result.known_difference_wh:7.2f} | "
                  f"{result.unknown_difference_wh:9.2f} | {100 * result.known_time_share:7.2f} | "
                  f"{result.shutdown_count}/{result.restart_count} | {len(result.short_returns):7} | "
                  f"{len(result.uncertain_returns)} ({len(result.uncertain_short_returns)})")
            for event in result.uncertain_returns:
                print(f"    NIEPEWNY: OFF {event.shutdown_at:%H:%M:%S}, raport obecnosci "
                      f"{event.at:%H:%M:%S}; OFF zawieral UNKNOWN; rzeczywisty moment powrotu nieznany.")


def main(model: ReceiverModel | None = None, adaptive_output: str | None = None):
    reports, start, end, freshness = scenario_inputs()
    intervals = reconstruct(reports, start, end, freshness)
    print("SYMULACJA offline, UTC; odbiornik poczatkowo ON; energia modelowana w tabelach ponizej.")
    print(f"Okres: [{start.isoformat()}, {end.isoformat()})")
    print("Waznosc raportu: 2 min (zalozenie demo); krotki powrot: <= 2 min od OFF.")
    print("ON, WAITING i SUSPENDED oznaczaja wlaczone zasilanie; OFF = wylaczone.")
    print("Brak wiadomosci nie dowodzi utraty lacznosci; sprawdz tryb raportowania czujnika.")
    print("Przerwa w raportowaniu 08:31-08:40; stan nieznany od 08:32.")
    print("\nChronologiczne raporty wejsciowe (czas, occupancy, termin waznosci):")
    for report in reports:
        print(f"{report.at:%H:%M:%S} occupancy={str(report.occupied):5} "
              f"wazny do {report.at + freshness:%H:%M:%S} (chyba ze zastapi go kolejny)")
    print("\nOdtworzone przedzialy [od, do):")
    for interval in intervals:
        print(f"[{interval.start:%H:%M:%S}, {interval.end:%H:%M:%S}): "
              f"{PRESENCE_LABELS[interval.presence]}")
    for minutes in (2, 5, 10):
        result = simulate(intervals, timedelta(minutes=minutes))
        print(f"\nOpoznienie {minutes} min; stan poczatkowy ON. Wszystkie przejscia:")
        for transition in result.transitions:
            print(f"{transition.at:%H:%M:%S} {transition.previous.value.upper()} -> "
                  f"{transition.current.value.upper()}: {REASONS[transition.reason]}")
        print("Dokladne przedzialy OFF [od, do), z podzialem wedlug danych o obecnosci:")
        for interval in result.off_intervals:
            print(f"[{interval.start:%H:%M:%S}, {interval.end:%H:%M:%S}) "
                  f"{(interval.end - interval.start).total_seconds() / 60:g} min; "
                  f"{PRESENCE_LABELS[interval.presence]}")
        print("Powroty po OFF (prog wlaczny: 2 min):")
        for event in result.return_events:
            elapsed = (event.at - event.shutdown_at).total_seconds() / 60
            print(f"OFF {event.shutdown_at:%H:%M:%S} -> powrot {event.at:%H:%M:%S}; "
                  f"{elapsed:g} min; krotki powrot: {'TAK' if event.is_short else 'NIE'}; "
                  f"niepewny: {'TAK' if event.uncertain else 'NIE'}")
        print(f"Wylaczenia: {len(result.shutdowns)}; krotkie powroty: {len(result.short_returns)}")
        print(f"OFF przy znanej nieobecnosci: {result.off_known_absence.total_seconds() / 60:g} min; "
              f"OFF przy stanie nieznanym: {result.off_unknown.total_seconds() / 60:g} min.")
        print(f"Stan OFF na koncu okresu: {'TAK' if result.off_at_end else 'NIE'}; "
              "koniec okresu nie generuje przelaczenia.")
    print("\nDlaczego 10 min daje krotki powrot, a 5 min nie?")
    print("10 min: OFF 08:25 -> powrot 08:28 (3 min, NIE); "
          "OFF 08:50 -> powrot 08:52 (2 min, TAK).")
    print("5 min: OFF 08:20 -> powrot 08:28 (8 min, NIE); "
          "OFF 08:45 -> powrot 08:52 (7 min, NIE).")
    print("Kolejne raporty ABSENT podtrzymuja odliczanie, nie restartuja go.")
    print("O 08:32 wygasa raport z 08:30: UNKNOWN ma pierwszenstwo przed OFF dla 2 min.")
    print("Po odzyskaniu ABSENT o 08:40 odliczanie zaczyna sie od nowa.")
    print("Jesli dane wygasna po OFF, odbiornik pozostaje OFF; ten czas liczymy jako OFF/UNKNOWN.")
    print("Czasy OFF sa symulowane i nie sa potwierdzonymi oszczednosciami energii.")
    print_energy_comparison(model if model is not None else ReceiverModel(60, 2))
    print_recommendations(model if model is not None else ReceiverModel(60, 2))
    print_schedule_comparison(model if model is not None else ReceiverModel(60, 2))
    print_adaptive_comparison(model if model is not None else ReceiverModel(60, 2), adaptive_output)


def cli():
    import argparse

    parser = argparse.ArgumentParser(description="Analityka i energia symulowana offline")
    parser.add_argument("--on-watts", type=float, default=60)
    parser.add_argument("--off-watts", type=float, default=2)
    parser.add_argument("--adaptive-output", help="Optional path for the JSON training configuration")
    args = parser.parse_args()
    try:
        model = ReceiverModel(args.on_watts, args.off_watts)
    except ValueError as error:
        parser.error(str(error))
    main(model, args.adaptive_output)


if __name__ == "__main__":
    cli()

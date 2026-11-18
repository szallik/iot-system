"""Compare fixed and scheduled policies on the same synthetic UTC histories."""

from datetime import timedelta
from zoneinfo import ZoneInfo

from .core import ControlState
from .energy import ReceiverModel, compare_energy
from .recommendation import Constraints, evaluate_later, recommend
from .scenarios import schedule_scenarios
from .strategies import example_schedule


def print_schedule_comparison(model: ReceiverModel):
    schedule = example_schedule()
    zone = ZoneInfo(schedule.timezone_name)
    cases = schedule_scenarios()
    policies = (timedelta(minutes=2), timedelta(minutes=5), timedelta(minutes=10), schedule)
    print("\nHARMONOGRAM - DANE SYNTETYCZNE, ENERGIA SYMULOWANA.")
    print("Przyklad, NIE zalecenie: pn-pt [08:00,16:00) Europe/Warsaw = 10 min; poza tym 2 min.")
    print(f"ON {model.on_watts:g} W, OFF {model.off_watts:g} W. Kazdy okres zaczyna sie ON, TTL 2 min.")
    print("UTC w danych i odliczaniu; harmonogram wybierany raz na poczatku kazdego odliczania.")
    for case in cases:
        print(f"\n{case.name}: [{case.start.isoformat()}, {case.end.isoformat()}) UTC;")
        print(f"  lokalnie [{case.start.astimezone(zone).isoformat()}, "
              f"{case.end.astimezone(zone).isoformat()}); {case.description}")
        print("strategia | konfiguracja | odniesienie Wh | strategia Wh | znane Wh | UNKNOWN Wh | OFF | krotkie / niepewne")
        for policy in policies:
            result = compare_energy(case.intervals(), model, policy)
            config = result.simulation.strategy
            known = sum(c.strategy_wh for c in result.categories if c.presence.value != "unknown")
            print(f"{config.name} | {config.configuration} | {result.baseline_wh:.3f} | "
                  f"{result.strategy_wh:.3f} | {known:.3f} | {result.strategy_wh - known:.3f} | "
                  f"{result.shutdown_count} | {len(result.short_returns)} / {len(result.uncertain_returns)}")
            if config == schedule:
                for transition in result.simulation.transitions:
                    if transition.current == ControlState.WAITING:
                        minutes = (transition.deadline - transition.at).total_seconds() / 60
                        print(f"  odliczanie: {transition.at.isoformat()} UTC "
                              f"({transition.at.astimezone(zone):%a %H:%M} lokalnie), "
                              f"wybrane {minutes:g} min, termin {transition.deadline.isoformat()} UTC")
    limits = Constraints(2, 12, .9, 1)
    print("\nWspolna rekomendacja: okresy powyzej, lacznie 4 h; krotkie <=2, OFF <=12, znane >=90%, niepewne <=1.")
    selection = recommend((c.intervals() for c in cases), model, policies, limits)
    print("strategia i konfiguracja | E znane Wh | E UNKNOWN Wh | OFF | krotkie | niepewne | znane % | OK | powody")
    for c in selection.candidates:
        print(f"{c.identifier} | {c.known_energy_wh:.3f} | {c.unknown_energy_wh:.3f} | "
              f"{c.shutdowns} | {c.short_returns} | {c.uncertain_returns} | {c.known_share:.2%} | "
              f"{'TAK' if c.admissible else 'NIE'} | {'; '.join(c.rejection_reasons) or '-'}")
    for reason in selection.reasons:
        print(reason)
    if selection.selected is not None:
        later = schedule_scenarios(later=True)
        print("Ocena na INNYCH pozniejszych danych, lacznie 4 h:")
        for case in later:
            print(f"  {case.name}: [{case.start.isoformat()}, {case.end.isoformat()}); {case.description}")
        evaluation = evaluate_later(selection, (c.intervals() for c in later))
        c = evaluation.candidate
        print(f"Zamrozona cala konfiguracja: {c.identifier}")
        print(f"E znane {c.known_energy_wh:.3f} Wh, E UNKNOWN {c.unknown_energy_wh:.3f} Wh; "
              f"OFF {c.shutdowns}, krotkie {c.short_returns}, niepewne {c.uncertain_returns}, "
              f"znane {c.known_share:.2%}; ograniczenia: {'TAK' if c.admissible else 'NIE'}; "
              f"{'; '.join(c.rejection_reasons) or 'bez przekroczen'}")
    print("Brak ponownego strojenia na ocenie; UNKNOWN nie premiuje rankingu. "
          "To nie jest dowod skutecznosci na stanowisku.")

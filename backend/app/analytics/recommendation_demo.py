"""Synthetic chronological selection and frozen holdout evaluation."""

from datetime import timedelta

from .energy import ReceiverModel
from .recommendation import Constraints, evaluate_later, recommend
from .scenarios import recommendation_scenarios


def print_candidate(candidate):
    print(f"{candidate.delay.total_seconds() / 60:3g} | {candidate.known_energy_wh:10.3f} | "
          f"{candidate.unknown_energy_wh:10.3f} | {candidate.shutdowns:3} | "
          f"{candidate.short_returns:7} | {candidate.uncertain_returns:8} | "
          f"{candidate.known_share:7.2%} | {'TAK' if candidate.admissible else 'NIE'} | "
          f"{'; '.join(candidate.rejection_reasons) or '-'}")


def print_recommendations(model: ReceiverModel):
    training, holdout = recommendation_scenarios()
    print("\nREKOMENDACJE - DANE SYNTETYCZNE, ENERGIA SYMULOWANA.")
    print("To nie jest dowod skutecznosci na rzeczywistym stanowisku.")
    print(f"Model jednego odbiornika: ON {model.on_watts:g} W, OFF {model.off_watts:g} W.")
    for label, cases in (("Dobor", training), ("Pozniejsza ocena", holdout)):
        duration = sum((c.end - c.start for c in cases), timedelta(0))
        print(f"{label}: laczny oceniany czas {duration.total_seconds() / 3600:g} h; "
              "luki pomiedzy okresami nie sa oceniane.")
        for case in cases:
            print(f"  {case.name}: [{case.start.isoformat()}, {case.end.isoformat()}); {case.description}")
    print("Kazdy scenariusz zaczyna sie ON. Limity zdarzen dotycza calego zestawu okresow, nie dnia.")
    print("Prog krotkiego powrotu <= 2 min; energia UNKNOWN pomijana przy wyborze.")
    for label, limits in (
        ("Bardziej oszczedny", Constraints(6, 10, .90, 1)),
        ("Bardziej wymagajacy pod wzgledem wygody", Constraints(0, 4, .95, 0)),
    ):
        print(f"\n{label}: krotkie <= {limits.max_short_returns}, OFF <= {limits.max_shutdowns}, "
              f"znane >= {limits.min_known_share:.0%}, niepewne <= {limits.max_uncertain_returns}.")
        selection = recommend((c.intervals() for c in training), model,
                              (timedelta(minutes=m) for m in (1, 2, 3, 5, 7, 10, 15)), limits)
        print("DOBOR: min | E znane Wh | E UNKNOWN Wh | OFF | krotkie | niepewne | znane % | OK | odrzucenie")
        for candidate in selection.candidates:
            print_candidate(candidate)
        for reason in selection.reasons:
            print(reason)
        if selection.selected is not None:
            evaluation = evaluate_later(selection, (c.intervals() for c in holdout))
            print(f"OCENA ZAMROZONEGO USTAWIENIA: {evaluation.duration.total_seconds() / 3600:g} h; "
                  "te same limity dla calego ocenianego zestawu; bez ponownego doboru.")
            print_candidate(evaluation.candidate)
            print("Pozniejsze ograniczenia: " + ("spelnione." if evaluation.candidate.admissible
                  else "NIESPELNIONE. Wybrane opoznienie pozostaje bez zmian."))

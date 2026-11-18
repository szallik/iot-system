"""Train on 14 days, compare frozen policies on the next 7 synthetic days."""

from datetime import timedelta
import json
from pathlib import Path

from .adaptive import DataRequirements, evaluate_adaptive, train_adaptive
from .energy import ReceiverModel
from .history_demo import history_scenarios
from .recommendation import Constraints, assess_strategy, evaluate_later, recommend
from .strategies import example_schedule


TRAIN_LIMITS = Constraints(20, 180, .97, 6)
EVALUATION_LIMITS = Constraints(10, 90, .97, 3)
CHOICES = tuple(timedelta(minutes=m) for m in (1, 2, 5, 10, 15))


def run_history_comparison(model: ReceiverModel):
    train_case, test_case = history_scenarios()
    train = (train_case.intervals(),)
    test = (test_case.intervals(),)
    fixed = recommend(train, model, CHOICES, TRAIN_LIMITS)
    learned = train_adaptive(train, model, TRAIN_LIMITS, choices=CHOICES,
                             requirements=DataRequirements(3, 3), default_delay=timedelta(minutes=5))
    schedule = example_schedule()  # Defined in advance; no fitting to holdout.
    train_rows = [("stala", fixed.selected),
                  ("harmonogram", assess_strategy(train, model, schedule, TRAIN_LIMITS).candidate),
                  ("adaptacyjna", learned.selection.selected if learned.selection else None)]
    test_rows = [
        ("stala", evaluate_later(fixed, test, constraints=EVALUATION_LIMITS).candidate if fixed.selected else None),
        ("harmonogram", assess_strategy(test, model, schedule, EVALUATION_LIMITS).candidate),
        ("adaptacyjna", evaluate_adaptive(learned, test, EVALUATION_LIMITS).candidate if learned.policy else None),
    ]
    return train_case, test_case, fixed, learned, train_rows, test_rows


def print_adaptive_comparison(model: ReceiverModel, output_path: str | None = None):
    train_case, test_case, fixed, learned, train_rows, test_rows = run_history_comparison(model)
    print("\nADAPTACJA: DOBOR PARAMETROW Z HISTORII. DANE SYNTETYCZNE (ziarno 20260105).")
    print("Nie przewiduje obecnosci i nie gwarantuje oszczednosci. Wszystkie energie symulowane.")
    print(f"Model: ON {model.on_watts:g} W, OFF {model.off_watts:g} W; strefa Europe/Warsaw; "
          "kazda cala os czasu zaczyna sie ON, bez resetow na granicach grup ani dni.")
    print("Prog krotkiego powrotu <=2 min; zasob grupy: >=3 dni (>=30 min znanych/dzien) i >=3 pelne epizody; default 5 min.")
    print("Trening porownuje cale konfiguracje; limity sa globalne. UNKNOWN nie premiuje rankingu.")
    for group in learned.evidence:
        print(f"{group.name}: dni {len(group.reliable_dates)}, kompletne epizody {group.complete_episodes}, "
              f"default {'TAK' if group.uses_default else 'NIE'}; {'; '.join(group.fallback_reasons) or '-'}")
    if learned.policy:
        print("Zamrozone opoznienia: " + ", ".join(
            f"{g.name}={d.total_seconds() / 60:g} min" for g, d in zip(learned.contexts.groups, learned.policy.delays)))
    else:
        print("Brak rekomendacji adaptacyjnej: " + "; ".join(learned.reasons))
    if fixed.selected:
        print(f"Najlepsze stale na treningu: {fixed.selected.delay.total_seconds() / 60:g} min.")
    else:
        print("Brak rekomendacji stalej: " + "; ".join(fixed.reasons))
    for label, case, limits, rows in (("TRENING", train_case, TRAIN_LIMITS, train_rows),
                                     ("OCENA BEZ STROJENIA", test_case, EVALUATION_LIMITS, test_rows)):
        print(f"\n{label}: [{case.start.isoformat()}, {case.end.isoformat()}) UTC, "
              f"{(case.end-case.start).total_seconds() / 3600:g} h.")
        print(f"Limity CALEGO okresu: krotkie <= {limits.max_short_returns}, OFF <= {limits.max_shutdowns}, "
              f"znane >= {limits.min_known_share:.0%}, niepewne <= {limits.max_uncertain_returns}.")
        print("wariant | energia Wh | znane Wh | UNKNOWN Wh | OFF | krotkie pewne | niepewne | znane % | OK | powody")
        for name, c in rows:
            if c is None:
                print(f"{name} | brak rekomendacji - bez oceny")
                continue
            print(f"{name} | {c.known_energy_wh+c.unknown_energy_wh:.3f} | {c.known_energy_wh:.3f} | "
                  f"{c.unknown_energy_wh:.3f} | {c.shutdowns} | {c.short_returns} | {c.uncertain_returns} | "
                  f"{c.known_share:.2%} | {'TAK' if c.admissible else 'NIE'} | {'; '.join(c.rejection_reasons) or '-'}")
    if output_path:
        artifact = learned.configuration()
        artifact["synthetic_data"] = {"seed": 20260105, "description": "14 training days; holdout excluded from fitting"}
        Path(output_path).write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Zapisano konfiguracje uczenia: {output_path}")

"""Bounded history analysis. No application startup, MQTT, writes or fitting."""
from dataclasses import dataclass
from datetime import timedelta

from .core import reconstruct, Presence
from .history_service import jsonable, seconds, simulate_import
from .sqlite_history import import_sqlite, import_periods, list_devices, import_electricity
from .recommendation import assess_strategy, recommend, evaluate_later
from .environment import analyze_environment
from .electricity import analyze_electricity
from .heat import HEAT_METRICS, summarize_heat
from .sqlite_history import import_meter


class AnalysisLimitError(ValueError):
    pass


class HistoryQualityError(ValueError):
    pass


@dataclass(frozen=True)
class AnalyticsService:
    database_path: str
    max_days: int = 31
    max_records: int = 20000
    max_configurations: int = 32
    source_kind: str = "unknown"

    @property
    def source(self):
        return {"format": "SQLite measurements", "kind": self.source_kind,
                "read_only": True, "location": "server-configured"}

    def check_limits(self, periods, configurations=0):
        if sum((p.end - p.start for p in periods), timedelta()) > timedelta(days=self.max_days):
            raise AnalysisLimitError(f"Laczny oceniany okres przekracza limit {self.max_days} dni")
        if configurations > self.max_configurations:
            raise AnalysisLimitError(f"Liczba konfiguracji przekracza limit {self.max_configurations}")

    def devices(self):
        return dict(list_devices(self.database_path, max_records=self.max_records), source=self.source)

    def quality(self, query):
        self.check_limits([query])
        imported = import_sqlite(self.database_path, device_id=query.device_id, metric="occupancy",
                                 start=query.start, end=query.end, max_records=self.max_records)
        return self.metadata(imported, query, query.device_id)

    def environment(self, query):
        self.check_limits([query])
        imported = import_sqlite(self.database_path, device_id=query.device_id, metric=query.metric,
                                 start=query.start, end=query.end, max_records=self.max_records)
        if imported.quality["blocking_reasons"]:
            raise HistoryQualityError("Analiza zablokowana: " + "; ".join(imported.quality["blocking_reasons"]))
        result = analyze_environment(imported, start=query.start, end=query.end, ttl=seconds(query.ttl_seconds),
                                     lower_threshold=query.lower_threshold, upper_threshold=query.upper_threshold)
        return dict(self.metadata(imported, query, query.device_id), **result,
                    ttl_seconds=query.ttl_seconds, lower_threshold=query.lower_threshold,
                    upper_threshold=query.upper_threshold)

    def metadata(self, imported, period, device_id):
        return {"source": self.source, "device_id": device_id, "metric": imported.metric,
                "period": {"from": period.start.isoformat(), "to": period.end.isoformat()},
                "quality": imported.quality}

    def electricity(self, query):
        self.check_limits([query])
        energy, power = import_electricity(self.database_path, device_id=query.device_id,
                                          start=query.start, end=query.end, max_records=self.max_records)
        for history in (energy, power):
            if history.quality["blocking_reasons"]:
                issues = sorted({issue["code"] for issue in history.quality["issues"]})
                raise HistoryQualityError(f"{history.metric}: " + "; ".join(history.quality["blocking_reasons"] + issues))
        result = analyze_electricity(energy, power, start=query.start, end=query.end, price_per_kwh=query.price_per_kwh)
        return dict(result, source=self.source, device_id=query.device_id, device_type="power_strip",
                    period={"from": query.start.isoformat(), "to": query.end.isoformat()},
                    quality={"energy": energy.quality, "power": power.quality})

    def heat(self, query):
        self.check_limits([query])
        histories = dict(zip(HEAT_METRICS, import_meter(self.database_path, device_id=query.device_id,
            start=query.start, end=query.end, max_records=self.max_records, device_type="heat_meter", metrics=HEAT_METRICS)))
        for metric, history in histories.items():
            if history.quality["blocking_reasons"]:
                raise HistoryQualityError(metric + ": " + "; ".join(history.quality["blocking_reasons"]))
        return dict(summarize_heat(histories, start=query.start, end=query.end, ttl=seconds(query.ttl_seconds)), source=self.source,
                    device_id=query.device_id, device_type="heat_meter",
                    period={"from": query.start.isoformat(), "to": query.end.isoformat()},
                    quality={metric: history.quality for metric, history in histories.items()})

    @staticmethod
    def timeline(imported, period, request):
        if imported.quality["blocking_reasons"]:
            raise HistoryQualityError("Symulacja zablokowana: " + "; ".join(imported.quality["blocking_reasons"]))
        return reconstruct(imported.presence_reports(), period.start, period.end, seconds(request.ttl_seconds))

    @staticmethod
    def assumptions(request):
        return {"energy_simulated": True, "initial_power_state": "ON",
                "baseline": "same receiver always ON", "ttl_seconds": request.ttl_seconds,
                "short_return_seconds": request.short_return_seconds,
                "receiver": request.receiver.model_dump(), "notes": [
                    "Analiza historii i symulacja; brak sterowania lub aktywacji strategii.",
                    "Energia ON/OFF pochodzi z modelu jednego odbiornika, nie z pomiaru gniazd.",
                    "Roznica energii UNKNOWN nie jest potwierdzona oszczednoscia i nie uczestniczy w rankingu.",
                    "TTL musi odpowiadac semantyce raportowania czujnika; same odstepy jej nie okreslaja.",
                    "Kazdy okres rozpoczyna sie od ON; koniec zakresu jest wylaczny.",
                    "Limity zdarzen dotycza calego wskazanego okresu, nie pojedynczego dnia.",
                ]}

    def period_result(self, imported, period, request, constraints, timeline, candidates):
        rows = []
        for candidate in candidates:
            row = {key: getattr(candidate, key) for key in (
                "identifier", "strategy_name", "configuration", "known_energy_wh", "unknown_energy_wh",
                "shutdowns", "short_returns", "uncertain_returns", "known_share", "admissible", "rejection_reasons")}
            row["simulation"] = simulate_import(imported, start=period.start, end=period.end,
                ttl=seconds(request.ttl_seconds), strategy=candidate.policy, model=request.receiver.domain(),
                short_return_window=timedelta(seconds=request.short_return_seconds))
            rows.append(row)
        return dict(self.metadata(imported, period, request.device_id),
                    duration_seconds=(period.end - period.start).total_seconds(),
                    unknown_seconds=sum((i.end-i.start).total_seconds() for i in timeline if i.presence == Presence.UNKNOWN),
                    constraints=constraints.model_dump(), candidates=jsonable(rows))

    def compare(self, request):
        self.check_limits([request.period], len(request.strategies))
        imported = import_sqlite(self.database_path, device_id=request.device_id, metric="occupancy",
                                 start=request.period.start, end=request.period.end, max_records=self.max_records)
        timeline = self.timeline(imported, request.period, request)
        candidates = [assess_strategy([timeline], request.receiver.domain(), policy.domain(),
                      request.constraints.domain(), timedelta(seconds=request.short_return_seconds)).candidate
                      for policy in request.strategies]
        return {"assumptions": self.assumptions(request), "comparison": self.period_result(
            imported, request.period, request, request.constraints, timeline, candidates)}

    def recommend(self, request):
        periods = [request.selection_period, request.evaluation_period]
        self.check_limits(periods, len(request.strategies))
        selection_data, evaluation_data = import_periods(self.database_path, device_id=request.device_id,
            periods=[(p.start, p.end) for p in periods], max_records=self.max_records)
        earlier = self.timeline(selection_data, periods[0], request)
        later = self.timeline(evaluation_data, periods[1], request)
        selection = recommend([earlier], request.receiver.domain(), [s.domain() for s in request.strategies],
                              request.selection_constraints.domain(), timedelta(seconds=request.short_return_seconds))
        evaluation = None
        if selection.selected:
            frozen = evaluate_later(selection, [later], constraints=request.evaluation_constraints.domain())
            evaluation = self.period_result(evaluation_data, periods[1], request, request.evaluation_constraints,
                                            later, [frozen.candidate])
        return {"assumptions": self.assumptions(request),
                "status": "recommended" if selection.selected else "no_recommendation",
                "selected_identifier": selection.selected.identifier if selection.selected else None,
                "energy_tolerance_wh": selection.energy_tolerance_wh, "reasons": selection.reasons,
                "ranking": [c.identifier for c in selection.ranking],
                "selection": self.period_result(selection_data, periods[0], request, request.selection_constraints,
                                                earlier, selection.candidates), "evaluation": evaluation}

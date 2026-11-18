"""HTTP contract for historical analysis only; no file paths or activation fields."""
from datetime import timezone
from typing import Annotated, Literal
from zoneinfo import ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_validator, field_validator

from app.analytics.history_service import seconds
from app.analytics.strategies import FixedDelay, ScheduleWindow, WeeklySchedule
from app.analytics.contexts import AdaptiveDelay, ContextGroup, ContextPartition
from app.analytics.energy import ReceiverModel
from app.analytics.recommendation import Constraints

PositiveSeconds = Annotated[float, Field(strict=True, ge=0.000001, le=31536000, allow_inf_nan=False)]
Power = Annotated[float, Field(strict=True, ge=0, le=1e12, allow_inf_nan=False)]
Count = Annotated[int, Field(strict=True, ge=0)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Period(Model):
    start: AwareDatetime = Field(alias="from")
    end: AwareDatetime = Field(alias="to")

    @field_validator("start", "end", mode="before")
    @classmethod
    def iso_dates(cls, value):
        if not isinstance(value, str):
            raise ValueError("Wymagana data ISO 8601 ze strefa")
        return value

    @model_validator(mode="after")
    def ordered(self):
        self.start = self.start.astimezone(timezone.utc)
        self.end = self.end.astimezone(timezone.utc)
        if self.start >= self.end:
            raise ValueError("Zakres wymaga from < to; koniec jest wylaczny")
        return self


class QualityQuery(Period):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    device_id: str = Field(min_length=1, max_length=64, pattern=r"\S")


class EmptyQuery(Model):
    pass


class EnvironmentQuery(QualityQuery):
    metric: Literal["temperature", "humidity"]
    # Query parameters arrive as strings, unlike strict numeric JSON fields.
    ttl_seconds: float = Field(ge=0.000001, le=31536000, allow_inf_nan=False)
    lower_threshold: float | None = Field(default=None, allow_inf_nan=False)
    upper_threshold: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered_thresholds(self):
        if self.lower_threshold is not None and self.upper_threshold is not None and self.lower_threshold > self.upper_threshold:
            raise ValueError("Dolny prog nie moze przekraczac gornego")
        return self


class ElectricityQuery(QualityQuery):
    price_per_kwh: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class HeatQuery(QualityQuery):
    ttl_seconds: float = Field(ge=0.000001, le=31536000, allow_inf_nan=False)


class Receiver(Model):
    on_watts: Power
    off_watts: Power

    def domain(self):
        return ReceiverModel(self.on_watts, self.off_watts)


class Limits(Model):
    max_short_returns: Count
    max_shutdowns: Count
    min_known_share: Annotated[float, Field(strict=True, ge=0, le=1, allow_inf_nan=False)]
    max_uncertain_returns: Count

    def domain(self):
        return Constraints(**self.model_dump())


class Policy(Model):
    @model_validator(mode="after")
    def validate_policy(self):
        try:
            self.domain()
        except (ValueError, OverflowError, ZoneInfoNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        return self


class Fixed(Policy):
    kind: Literal["fixed"]
    delay_seconds: PositiveSeconds

    def domain(self):
        return FixedDelay(seconds(self.delay_seconds))


class Window(Model):
    weekdays: list[Annotated[int, Field(strict=True, ge=0, le=6)]] = Field(min_length=1, max_length=7)
    start: str = Field(max_length=5)
    end: str = Field(max_length=5)
    delay_seconds: PositiveSeconds

    def domain(self):
        return ScheduleWindow(tuple(self.weekdays), self.start, self.end, seconds(self.delay_seconds))


class Schedule(Policy):
    kind: Literal["schedule"]
    timezone: str = Field(default="Europe/Warsaw", max_length=100)
    default_delay_seconds: PositiveSeconds
    windows: list[Window] = Field(max_length=64)

    def domain(self):
        return WeeklySchedule(seconds(self.default_delay_seconds), tuple(w.domain() for w in self.windows), self.timezone)


class Group(Model):
    name: str = Field(min_length=1, max_length=100)
    weekdays: list[Annotated[int, Field(strict=True, ge=0, le=6)]] = Field(min_length=1, max_length=7)
    hours: list[tuple[Annotated[str, Field(max_length=5)], Annotated[str, Field(max_length=5)]]] = Field(min_length=1, max_length=24)
    delay_seconds: PositiveSeconds


class Adaptive(Policy):
    kind: Literal["adaptive"]
    timezone: str = Field(default="Europe/Warsaw", max_length=100)
    groups: list[Group] = Field(min_length=1, max_length=16)

    def domain(self):
        contexts = ContextPartition(tuple(ContextGroup(g.name, tuple(g.weekdays), tuple(g.hours)) for g in self.groups), self.timezone)
        return AdaptiveDelay(contexts, tuple(seconds(g.delay_seconds) for g in self.groups))


StrategyInput = Annotated[Fixed | Schedule | Adaptive, Field(discriminator="kind")]


class SimulationRequest(Model):
    device_id: str = Field(min_length=1, max_length=64, pattern=r"\S")
    ttl_seconds: PositiveSeconds
    receiver: Receiver
    strategies: list[StrategyInput] = Field(min_length=1, max_length=256)
    short_return_seconds: Annotated[float, Field(strict=True, ge=0, le=31536000, allow_inf_nan=False)] = 120


class CompareRequest(SimulationRequest):
    period: Period
    constraints: Limits


class RecommendRequest(SimulationRequest):
    selection_period: Period
    evaluation_period: Period
    selection_constraints: Limits
    evaluation_constraints: Limits

    @model_validator(mode="after")
    def later(self):
        if self.evaluation_period.start < self.selection_period.end:
            raise ValueError("Ocena musi nastepowac po doborze; okresy nie moga sie nakladac")
        return self


class Source(Model):
    format: Literal["SQLite measurements"] = "SQLite measurements"
    kind: Literal["synthetic", "real", "unknown"]
    read_only: Literal[True] = True
    location: Literal["server-configured"] = "server-configured"


class Quality(BaseModel):
    # Preserve the full shared quality report, including per-row diagnostics.
    model_config = ConfigDict(extra="allow")
    input_rows: int
    selected_rows: int
    rejected_rows: int
    blocking_reasons: list[str]


class QualityResponse(Model):
    source: Source
    device_id: str
    metric: Literal["occupancy"] = "occupancy"
    period: dict[str, str]
    quality: Quality


class EnvironmentResponse(Model):
    source: Source
    device_id: str
    metric: Literal["temperature", "humidity"]
    period: dict[str, str]
    ttl_seconds: float
    lower_threshold: float | None
    upper_threshold: float | None
    unit: Literal["°C", "%"]
    quality: Quality
    valid_readings_count: int
    minimum: float | None
    maximum: float | None
    time_weighted_mean: float | None
    duration_seconds: float
    known_seconds: float
    unknown_seconds: float
    coverage_percent: float
    thresholds_evaluated: bool
    below_seconds: float | None
    within_seconds: float | None
    above_seconds: float | None


class ReadingBounds(Model):
    start: AwareDatetime
    end: AwareDatetime


class CounterPair(ReadingBounds):
    duration_seconds: float
    start_kwh: float
    end_kwh: float


class AcceptedCounterPair(CounterPair):
    increment_kwh: float


class SkippedCounterPair(CounterPair):
    reason: Literal["counter_reset_or_anomaly"]


class MeterResponse(Model):
    source: Source
    device_id: str
    device_type: Literal["power_strip", "heat_meter"]
    period: dict[str, str]
    energy_simulated: Literal[False]
    scope: Literal["whole_power_strip", "heat_meter"]
    energy_unit: Literal["kWh"]
    power_unit: Literal["W"]
    energy_kwh: float | None
    max_power_w: float | None
    max_power_at: AwareDatetime | None
    energy_readings_count: int
    power_readings_count: int
    accepted_pairs_count: int
    skipped_pairs_count: int
    counter_drops_count: int
    observed_energy_bounds: ReadingBounds | None
    used_energy_bounds: ReadingBounds | None
    covered_seconds: float
    uncovered_seconds: float
    duration_seconds: float
    accepted_pairs: list[AcceptedCounterPair]
    skipped_pairs: list[SkippedCounterPair]
    quality: dict[str, Quality]
    assumptions: list[str]


class ElectricityResponse(MeterResponse):
    device_type: Literal["power_strip"]
    scope: Literal["whole_power_strip"]
    price_per_kwh: float | None
    estimated_cost: float | None


class HeatSamples(Model):
    unit: str
    readings_count: int
    minimum: float | None
    maximum: float | None
    first_at: AwareDatetime | None
    last_at: AwareDatetime | None
    last_value: float | None
    time_weighted_mean: float | None
    known_seconds: float
    unknown_seconds: float
    coverage_percent: float


class TemperatureDifference(Model):
    unit: Literal["°C"]
    minimum: float | None
    maximum: float | None
    time_weighted_mean: float | None
    known_seconds: float
    unknown_seconds: float
    coverage_percent: float
    negative_seconds: float | None


class HeatResponse(MeterResponse):
    device_type: Literal["heat_meter"]
    scope: Literal["heat_meter"]
    energy_kind: Literal["thermal"]
    ttl_seconds: float
    temperature_difference: TemperatureDifference
    measurements: dict[Literal["power", "temperature_1", "temperature_2", "flow"], HeatSamples]


class DeviceHistory(Model):
    device_id: str
    readings: int
    time_range: dict | None
    invalid_timestamps: int
    invalid_values: int
    measurement_ids: list[int]
    quality: Quality


class DevicesResponse(Model):
    source: Source
    devices: list[DeviceHistory]


class PresenceInterval(Model):
    start: AwareDatetime
    end: AwareDatetime
    presence: Literal["present", "absent", "unknown"]


class ReturnEvent(Model):
    shutdown_at: AwareDatetime
    at: AwareDatetime
    is_short: bool
    uncertain: bool


class CategoryEnergy(Model):
    presence: Literal["present", "absent", "unknown"]
    on_duration: float
    off_duration: float
    baseline_wh: float
    strategy_wh: float
    difference_wh: float


class EnergyResult(Model):
    categories: list[CategoryEnergy]
    baseline_wh: float
    strategy_wh: float
    difference_wh: float
    difference_percent: float | None
    known_difference_wh: float
    unknown_difference_wh: float
    known_time_share: float
    shutdown_count: int
    restart_count: int
    certain_returns: list[ReturnEvent]
    uncertain_returns: list[ReturnEvent]
    short_returns: list[ReturnEvent]
    uncertain_short_returns: list[ReturnEvent]
    simulated: Literal[True]


class Transition(Model):
    at: AwareDatetime
    previous: Literal["on", "waiting", "suspended", "off"]
    current: Literal["on", "waiting", "suspended", "off"]
    reason: str
    deadline: AwareDatetime | None


class SimulationTrace(Model):
    initial_power_state: Literal["ON"]
    off_at_end: bool
    transitions: list[Transition]
    off_intervals: list[PresenceInterval]
    off_duration_seconds: float


class PriorReport(Model):
    at: AwareDatetime
    occupied: bool


class SimulationResult(Model):
    energy_simulated: Literal[True]
    energy: EnergyResult
    presence_intervals: list[PresenceInterval]
    last_pre_start_report: PriorReport | None
    pre_start_report_is_fresh: bool
    simulation: SimulationTrace


class CandidateResult(Model):
    identifier: str
    strategy_name: str
    configuration: dict
    known_energy_wh: float
    unknown_energy_wh: float
    shutdowns: int
    short_returns: int
    uncertain_returns: int
    known_share: float
    admissible: bool
    rejection_reasons: list[str]
    simulation: SimulationResult


class PeriodResult(QualityResponse):
    duration_seconds: float
    unknown_seconds: float
    constraints: Limits
    candidates: list[CandidateResult]


class Assumptions(Model):
    energy_simulated: Literal[True] = True
    initial_power_state: Literal["ON"] = "ON"
    baseline: Literal["same receiver always ON"] = "same receiver always ON"
    ttl_seconds: float
    short_return_seconds: float
    receiver: Receiver
    notes: list[str]


class CompareResponse(Model):
    assumptions: Assumptions
    comparison: PeriodResult


class RecommendResponse(Model):
    assumptions: Assumptions
    status: Literal["recommended", "no_recommendation"]
    energy_tolerance_wh: float
    selected_identifier: str | None
    reasons: list[str]
    ranking: list[str]
    selection: PeriodResult
    evaluation: PeriodResult | None

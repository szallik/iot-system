import json
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppError
from app.models.entities import Measurement, Event
from app.schemas.api_schemas import (
    MeasurementResponse,
    EventResponse,
    StatsResponse,
    StatPoint,
)

router = APIRouter(prefix="/api", tags=["Pomiary, Zdarzenia i Statystyki"])


@router.get("/measurements", response_model=List[MeasurementResponse])
def get_measurements(
    device_id: Optional[str] = Query(default=None),
    metric: Optional[str] = Query(default=None),
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    agg: Optional[str] = Query(default=None),
    limit: int = Query(default=1000, ge=1, le=10000),
    db: Session = Depends(get_db),
):
    q = db.query(Measurement)
    if device_id:
        q = q.filter(Measurement.device_id == device_id)
    if metric:
        q = q.filter(Measurement.metric == metric)
    if from_ts:
        q = q.filter(Measurement.ts >= from_ts)
    if to_ts:
        q = q.filter(Measurement.ts <= to_ts)

    if not agg or agg == "raw":
        rows = q.order_by(Measurement.ts.desc()).limit(limit).all()
        return [
            MeasurementResponse(
                id=r.id,
                device_id=r.device_id,
                metric=r.metric,
                value=round(r.value, 4),
                unit=r.unit,
                ts=r.ts,
            )
            for r in rows
        ]

    agg_lower = agg.lower()
    if agg_lower in ("hour", "day"):
        substr_len = 13 if agg_lower == "hour" else 10
        bucket = func.substr(Measurement.ts, 1, substr_len).label("bucket")
        rows = (
            q.with_entities(
                Measurement.device_id,
                Measurement.metric,
                func.avg(Measurement.value).label("val"),
                func.max(Measurement.unit).label("unit"),
                bucket,
            )
            .group_by(Measurement.device_id, Measurement.metric, bucket)
            .order_by(bucket.asc())
            .limit(limit)
            .all()
        )
        return [
            MeasurementResponse(
                id=None,
                device_id=r.device_id,
                metric=r.metric,
                value=round(float(r.val), 4),
                unit=r.unit,
                ts=f"{r.bucket}:00:00Z" if agg_lower == "hour" else f"{r.bucket}T00:00:00Z",
            )
            for r in rows
        ]

    func_map = {
        "min": func.min(Measurement.value),
        "max": func.max(Measurement.value),
        "avg": func.avg(Measurement.value),
        "sum": func.sum(Measurement.value),
    }
    if agg_lower not in func_map:
        raise AppError("INVALID_AGGREGATION", f"Nieobsługiwany parametr agg='{agg}'.", 400)

    rows = (
        q.with_entities(
            Measurement.device_id,
            Measurement.metric,
            func_map[agg_lower].label("val"),
            func.max(Measurement.unit).label("unit"),
            func.max(Measurement.ts).label("ts"),
        )
        .group_by(Measurement.device_id, Measurement.metric)
        .all()
    )
    return [
        MeasurementResponse(
            id=None,
            device_id=r.device_id,
            metric=r.metric,
            value=round(float(r.val), 4),
            unit=r.unit,
            ts=r.ts,
        )
        for r in rows
        if r.val is not None
    ]


@router.get("/events", response_model=List[EventResponse])
def get_events(
    type: Optional[str] = Query(default=None),
    device_id: Optional[str] = Query(default=None),
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    q = db.query(Event)
    if type:
        q = q.filter(Event.typ == type)
    if device_id:
        q = q.filter(Event.device_id == device_id)
    if from_ts:
        q = q.filter(Event.ts >= from_ts)
    if to_ts:
        q = q.filter(Event.ts <= to_ts)

    rows = q.order_by(Event.ts.desc()).limit(limit).all()
    result = []
    for r in rows:
        try:
            parsed = json.loads(r.payload)
        except Exception:
            parsed = {"raw": r.payload}
        result.append(
            EventResponse(
                id=r.id,
                device_id=r.device_id,
                typ=r.typ,
                payload=parsed,
                ts=r.ts,
            )
        )
    return result


@router.get("/stats", response_model=StatsResponse)
def get_stats(
    device_id: str = Query(...),
    metric: str = Query(...),
    period: str = Query(default="day"),
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
):
    period_lens = {"hour": 13, "day": 10, "month": 7}
    if period not in period_lens:
        raise AppError("INVALID_PERIOD", "period musi wynosić: hour, day lub month.", 400)

    base_q = db.query(Measurement).filter(
        Measurement.device_id == device_id,
        Measurement.metric == metric,
    )
    if from_ts:
        base_q = base_q.filter(Measurement.ts >= from_ts)
    if to_ts:
        base_q = base_q.filter(Measurement.ts <= to_ts)

    overall = base_q.with_entities(
        func.min(Measurement.value),
        func.max(Measurement.value),
        func.avg(Measurement.value),
        func.sum(Measurement.value),
        func.max(Measurement.unit),
    ).first()

    bucket_expr = func.substr(Measurement.ts, 1, period_lens[period]).label("bucket")
    grouped = (
        base_q.with_entities(
            bucket_expr,
            func.min(Measurement.value).label("min_v"),
            func.max(Measurement.value).label("max_v"),
            func.avg(Measurement.value).label("avg_v"),
            func.sum(Measurement.value).label("sum_v"),
            func.count(Measurement.id).label("cnt"),
        )
        .group_by(bucket_expr)
        .order_by(bucket_expr.asc())
        .all()
    )

    series = []
    for row in grouped:
        delta = round(float(row.max_v) - float(row.min_v), 4)
        daily_cons = delta if delta > 0 else round(float(row.max_v), 4)
        series.append(
            StatPoint(
                period=row.bucket,
                min=round(float(row.min_v), 4),
                max=round(float(row.max_v), 4),
                avg=round(float(row.avg_v), 4),
                sum=round(float(row.sum_v), 4),
                daily_consumption=daily_cons if metric == "energy" else None,
                count=row.cnt,
            )
        )

    return StatsResponse(
        device_id=device_id,
        metric=metric,
        unit=overall[4] or "",
        period=period,
        overall_min=round(float(overall[0]), 4) if overall[0] is not None else None,
        overall_max=round(float(overall[1]), 4) if overall[1] is not None else None,
        overall_avg=round(float(overall[2]), 4) if overall[2] is not None else None,
        overall_sum=round(float(overall[3]), 4) if overall[3] is not None else None,
        series=series,
    )
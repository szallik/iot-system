import csv
import io
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppError
from app.models.entities import AccessAttempt, User, Measurement, Event
from app.schemas.api_schemas import (
    AccessAttemptResponse,
    UserAccessReportResponse,
    UserResponse,
)

router = APIRouter(prefix="/api", tags=["Kontrola Dostępu, Raporty i Eksport CSV"])


def _serialize_attempt(a: AccessAttempt) -> AccessAttemptResponse:
    return AccessAttemptResponse(
        id=a.id,
        user_id=a.user_id,
        user_login=a.user.login if a.user else None,
        device_id=a.device_id,
        wynik=a.wynik,
        kod_decyzji=a.kod_decyzji,
        ts=a.ts,
    )


@router.get("/access/attempts", response_model=List[AccessAttemptResponse])
def get_access_attempts(
    user_id: Optional[int] = Query(default=None),
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    limit: int = Query(default=500, ge=1, le=5000),
    db: Session = Depends(get_db),
):
    q = db.query(AccessAttempt)
    if user_id is not None:
        q = q.filter(AccessAttempt.user_id == user_id)
    if from_ts:
        q = q.filter(AccessAttempt.ts >= from_ts)
    if to_ts:
        q = q.filter(AccessAttempt.ts <= to_ts)

    rows = q.order_by(AccessAttempt.ts.desc()).limit(limit).all()
    return [_serialize_attempt(r) for r in rows]


@router.get("/reports/user/{user_id}", response_model=UserAccessReportResponse)
def get_user_access_report(
    user_id: int,
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError("USER_NOT_FOUND", f"Użytkownik ID={user_id} nie istnieje.", 404)

    q = db.query(AccessAttempt).filter(AccessAttempt.user_id == user_id)
    if from_ts:
        q = q.filter(AccessAttempt.ts >= from_ts)
    if to_ts:
        q = q.filter(AccessAttempt.ts <= to_ts)

    attempts = q.order_by(AccessAttempt.ts.desc()).all()
    granted = sum(1 for a in attempts if a.kod_decyzji == 1)
    wrong_pin = sum(1 for a in attempts if a.kod_decyzji == 2)
    forbidden = sum(1 for a in attempts if a.kod_decyzji == 3)

    return UserAccessReportResponse(
        user=UserResponse.model_validate(user),
        total_attempts=len(attempts),
        granted_count=granted,
        denied_wrong_pin_count=wrong_pin,
        denied_forbidden_count=forbidden,
        last_attempt_ts=attempts[0].ts if attempts else None,
        attempts=[_serialize_attempt(a) for a in attempts],
    )


@router.get("/export")
def export_dataset_csv(
    dataset: str = Query(...),
    device_id: Optional[str] = Query(default=None),
    user_id: Optional[int] = Query(default=None),
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    db: Session = Depends(get_db),
):
    output = io.StringIO()
    writer = csv.writer(output, delimiter=",")

    if dataset == "measurements":
        q = db.query(Measurement)
        if device_id:
            q = q.filter(Measurement.device_id == device_id)
        if from_ts:
            q = q.filter(Measurement.ts >= from_ts)
        if to_ts:
            q = q.filter(Measurement.ts <= to_ts)
        rows = q.order_by(Measurement.ts.desc()).all()

        writer.writerow(["id", "device_id", "metric", "value", "unit", "ts"])
        for r in rows:
            writer.writerow([r.id, r.device_id, r.metric, r.value, r.unit, r.ts])

    elif dataset == "events":
        q = db.query(Event)
        if device_id:
            q = q.filter(Event.device_id == device_id)
        if from_ts:
            q = q.filter(Event.ts >= from_ts)
        if to_ts:
            q = q.filter(Event.ts <= to_ts)
        rows = q.order_by(Event.ts.desc()).all()

        writer.writerow(["id", "device_id", "typ", "payload", "ts"])
        for r in rows:
            writer.writerow([r.id, r.device_id, r.typ, r.payload, r.ts])

    elif dataset == "access_attempts":
        q = db.query(AccessAttempt)
        if user_id is not None:
            q = q.filter(AccessAttempt.user_id == user_id)
        if from_ts:
            q = q.filter(AccessAttempt.ts >= from_ts)
        if to_ts:
            q = q.filter(AccessAttempt.ts <= to_ts)
        rows = q.order_by(AccessAttempt.ts.desc()).all()

        writer.writerow(["id", "user_id", "user_login", "device_id", "wynik", "kod_decyzji", "ts"])
        for r in rows:
            writer.writerow([
                r.id,
                r.user_id or "",
                r.user.login if r.user else "unknown",
                r.device_id,
                r.wynik,
                r.kod_decyzji,
                r.ts,
            ])
    else:
        raise AppError("INVALID_DATASET", "dataset musi mieć wartość: measurements, events lub access_attempts.", 400)

    output.seek(0)
    filename = f"export_{dataset}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
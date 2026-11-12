import json
from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppError
from app.models.entities import Automation
from app.schemas.api_schemas import (
    AutomationCreateRequest,
    AutomationEnableRequest,
    AutomationResponse,
)

router = APIRouter(prefix="/api/automations", tags=["Automatyzacje"])


def _serialize_rule(rule: Automation) -> AutomationResponse:
    return AutomationResponse(
        id=rule.id,
        nazwa=rule.nazwa,
        trigger=json.loads(rule.trigger),
        warunek=json.loads(rule.warunek) if rule.warunek else None,
        akcja=json.loads(rule.akcja),
        aktywna=rule.aktywna,
    )


@router.get("", response_model=List[AutomationResponse])
def list_automations(db: Session = Depends(get_db)):
    rules = db.query(Automation).order_by(Automation.id.asc()).all()
    return [_serialize_rule(r) for r in rules]


@router.post("", response_model=AutomationResponse, status_code=201)
def create_automation(req: AutomationCreateRequest, db: Session = Depends(get_db)):
    rule = Automation(
        nazwa=req.nazwa,
        trigger=json.dumps(req.trigger),
        warunek=json.dumps(req.warunek) if req.warunek else None,
        akcja=json.dumps(req.akcja),
        aktywna=req.aktywna,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _serialize_rule(rule)


@router.post("/{automation_id}/enable", response_model=AutomationResponse)
def toggle_automation(
    automation_id: int,
    req: AutomationEnableRequest,
    db: Session = Depends(get_db),
):
    rule = db.query(Automation).filter(Automation.id == automation_id).first()
    if not rule:
        raise AppError("AUTOMATION_NOT_FOUND", f"Automatyzacja ID={automation_id} nie istnieje.", 404)
    rule.aktywna = req.aktywna
    db.commit()
    db.refresh(rule)
    return _serialize_rule(rule)
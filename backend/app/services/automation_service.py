import json
import logging
from typing import Dict, Any, Callable
from sqlalchemy.orm import Session
from app.models.entities import Automation, Device

logger = logging.getLogger("iot.automations")


def evaluate_condition(db: Session, condition: Dict[str, Any]) -> bool:
    if not condition:
        return True

    cond_dev_id = condition.get("device_id")
    field = condition.get("field") or condition.get("metric")
    op = condition.get("op", "==")
    expected = condition.get("value")

    if not cond_dev_id or not field:
        return True

    device = db.query(Device).filter(Device.id == cond_dev_id).first()
    if not device or not device.last_state:
        return False

    try:
        state = json.loads(device.last_state)
    except Exception:
        return False

    actual = state.get(field)
    if actual is None:
        return False

    try:
        if op == "==":
            return actual == expected
        if op == "!=":
            return actual != expected
        if op == ">":
            return float(actual) > float(expected)
        if op == ">=":
            return float(actual) >= float(expected)
        if op == "<":
            return float(actual) < float(expected)
        if op == "<=":
            return float(actual) <= float(expected)
    except (ValueError, TypeError):
        return False

    return False


def process_automations(
    db: Session,
    source_device_id: str,
    incoming_payload: Dict[str, Any],
    publish_command_cb: Callable[[str, Dict[str, Any]], bool],
):
    active_rules = db.query(Automation).filter(Automation.aktywna.is_(True)).all()

    for rule in active_rules:
        try:
            trigger = json.loads(rule.trigger) if isinstance(rule.trigger, str) else rule.trigger
            condition = json.loads(rule.warunek) if rule.warunek and isinstance(rule.warunek, str) else rule.warunek
            action = json.loads(rule.akcja) if isinstance(rule.akcja, str) else rule.akcja
        except Exception as e:
            logger.error(f"Błąd parsowania reguły ID={rule.id}: {e}")
            continue

        if trigger.get("device_id") != source_device_id:
            continue

        trig_field = trigger.get("field") or trigger.get("metric") or "action"
        if trig_field not in incoming_payload:
            continue

        actual_val = incoming_payload[trig_field]
        expected_val = trigger.get("value")
        op = trigger.get("op", "==")

        matched = False
        try:
            if op == "==":
                matched = actual_val == expected_val
            elif op == "!=":
                matched = actual_val != expected_val
            elif op == ">":
                matched = float(actual_val) > float(expected_val)
            elif op == ">=":
                matched = float(actual_val) >= float(expected_val)
            elif op == "<":
                matched = float(actual_val) < float(expected_val)
            elif op == "<=":
                matched = float(actual_val) <= float(expected_val)
        except (ValueError, TypeError):
            matched = False

        if not matched or not evaluate_condition(db, condition):
            continue

        target_dev_id = action.get("device_id")
        command_payload = action.get("command")
        if target_dev_id and isinstance(command_payload, dict):
            logger.info(f"Wyzwalanie automatyzacji #{rule.id} -> {target_dev_id}: {command_payload}")
            publish_command_cb(target_dev_id, command_payload)
import json
import logging
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from app.models.entities import User, AccessAttempt, Event
from app.core.security import verify_secret_with_salt, utc_now_iso

logger = logging.getLogger("iot.access")

DECISION_GRANTED = 1
DECISION_INVALID_PIN = 2
DECISION_FORBIDDEN = 3

DECISION_LABELS = {
    DECISION_GRANTED: "ACCESS_GRANTED",
    DECISION_INVALID_PIN: "INVALID_PIN",
    DECISION_FORBIDDEN: "FORBIDDEN",
}


def verify_pin_and_log(
    db: Session,
    device_id: str,
    raw_presented_value: int | str,
) -> Tuple[int, Optional[User], str]:
    ts = utc_now_iso()
    try:
        pin_int = int(float(raw_presented_value))
        pin_candidates = {str(pin_int), f"{pin_int:04d}"}
    except (ValueError, TypeError):
        pin_candidates = {str(raw_presented_value).strip()}

    users = db.query(User).all()
    matched_user: Optional[User] = None

    for user in users:
        for candidate in pin_candidates:
            if verify_secret_with_salt(candidate, user.hash_pin):
                matched_user = user
                break
        if matched_user:
            break

    if matched_user is None:
        decision_code = DECISION_INVALID_PIN
    elif not matched_user.aktywny or matched_user.rola in ("blocked", "guest_no_access", "none"):
        decision_code = DECISION_FORBIDDEN
    else:
        decision_code = DECISION_GRANTED

    wynik_label = DECISION_LABELS[decision_code]

    attempt = AccessAttempt(
        user_id=matched_user.id if matched_user else None,
        device_id=device_id,
        wynik=wynik_label,
        kod_decyzji=decision_code,
        ts=ts,
    )
    db.add(attempt)

    event_payload = {
        "decision_code": decision_code,
        "result": wynik_label,
        "user_id": matched_user.id if matched_user else None,
        "user_login": matched_user.login if matched_user else "unknown",
    }
    db.add(
        Event(
            device_id=device_id,
            typ="access_attempt",
            payload=json.dumps(event_payload),
            ts=ts,
        )
    )
    db.commit()

    logger.info(
        f"Autoryzacja PIN na '{device_id}': user={matched_user.login if matched_user else 'BRAK'} -> decyzja={decision_code} ({wynik_label})"
    )
    return decision_code, matched_user, wynik_label
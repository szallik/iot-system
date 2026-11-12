import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db

bearer_scheme = HTTPBearer(auto_error=False)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_secret_with_salt(secret_value: str, salt: Optional[str] = None) -> str:
    if salt is None:
        salt = secrets.token_hex(16)
    salted = f"{salt}:{secret_value}".encode("utf-8")
    digest = hashlib.sha256(salted).hexdigest()
    return f"{salt}${digest}"


def verify_secret_with_salt(secret_value: str, stored_hash: str) -> bool:
    if not stored_hash or "$" not in stored_hash:
        return False
    salt, _ = stored_hash.split("$", 1)
    expected_hash = hash_secret_with_salt(secret_value, salt=salt)
    return hmac.compare_digest(expected_hash, stored_hash)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
):
    from app.models.entities import User

    if not credentials:
        return None
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        user_id = payload.get("sub")
        if user_id is None:
            return None
        return db.query(User).filter(User.id == int(user_id)).first()
    except Exception:
        return None
"""Existing JWT contract, shared by regular and read-only dependencies."""
from datetime import datetime, timedelta, timezone
import jwt
from fastapi.security import HTTPBearer
from .config import settings

bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token: str) -> dict:
    return jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def account_has_access(active, role):
    return bool(active) and role not in ("blocked", "guest_no_access", "none")

from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.security import hash_secret_with_salt
from app.models.entities import User
from app.schemas.api_schemas import (
    UserCreateRequest,
    UserUpdateRequest,
    UserResponse,
)

router = APIRouter(prefix="/api/users", tags=["Użytkownicy"])


@router.get("", response_model=List[UserResponse])
def list_users(db: Session = Depends(get_db)):
    return db.query(User).order_by(User.id.asc()).all()


@router.post("", response_model=UserResponse, status_code=201)
def create_user(req: UserCreateRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.login == req.login).first()
    if existing:
        raise AppError("USER_EXISTS", f"Użytkownik o loginie '{req.login}' już istnieje.", 409)

    new_user = User(
        login=req.login,
        hash_pin=hash_secret_with_salt(req.pin),
        password_hash=hash_secret_with_salt(req.password) if req.password else None,
        rola=req.rola,
        aktywny=req.aktywny,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.patch("/{user_id}", response_model=UserResponse)
def update_user(user_id: int, req: UserUpdateRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError("USER_NOT_FOUND", f"Użytkownik ID={user_id} nie istnieje.", 404)

    if req.pin is not None:
        user.hash_pin = hash_secret_with_salt(req.pin)
    if req.rola is not None:
        user.rola = req.rola
    if req.aktywny is not None:
        user.aktywny = req.aktywny

    db.commit()
    db.refresh(user)
    return user


@router.delete("/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise AppError("USER_NOT_FOUND", f"Użytkownik ID={user_id} nie istnieje.", 404)

    db.delete(user)
    db.commit()
    return {"status": "deleted", "user_id": user_id}
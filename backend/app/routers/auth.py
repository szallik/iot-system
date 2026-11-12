from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.exceptions import AppError
from app.core.security import verify_secret_with_salt, create_access_token
from app.models.entities import User
from app.schemas.api_schemas import LoginRequest, LoginResponse

router = APIRouter(prefix="/api/auth", tags=["Autoryzacja"])


@router.post("/login", response_model=LoginResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.login == req.login).first()
    if not user:
        raise AppError("INVALID_CREDENTIALS", "Nieprawidłowy login lub hasło/PIN.", 401)
    if not user.aktywny:
        raise AppError("USER_INACTIVE", "Konto użytkownika jest zablokowane.", 403)

    authenticated = False
    if req.password and user.password_hash:
        authenticated = verify_secret_with_salt(req.password, user.password_hash)
    if not authenticated and req.pin:
        authenticated = verify_secret_with_salt(req.pin, user.hash_pin)

    if not authenticated:
        raise AppError("INVALID_CREDENTIALS", "Nieprawidłowy login lub hasło/PIN.", 401)

    token = create_access_token({"sub": str(user.id), "login": user.login, "rola": user.rola})
    return LoginResponse(
        access_token=token,
        user_id=user.id,
        login=user.login,
        rola=user.rola,
    )
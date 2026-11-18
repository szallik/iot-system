"""Authenticated read-only historical analytics. Sync handlers run in the thread pool."""
from typing import Annotated
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.security import HTTPAuthorizationCredentials
from jwt import InvalidTokenError
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from app.core.auth_tokens import bearer_scheme, decode_access_token, account_has_access
from app.core.config import settings
from app.core.exceptions import AppError
from app.analytics.api_service import AnalyticsService, AnalysisLimitError, HistoryQualityError
from app.analytics.sqlite_history import open_readonly, RecordLimitExceeded, ElectricityDeviceError
from app.schemas.analytics import (EmptyQuery, QualityQuery, CompareRequest, RecommendRequest,
                                   EnvironmentQuery, EnvironmentResponse,
                                   ElectricityQuery, ElectricityResponse,
                                   HeatQuery, HeatResponse,
                                   DevicesResponse, QualityResponse, CompareResponse, RecommendResponse)


def get_service():
    try:
        url = make_url(settings.DATABASE_URL)
        if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:" or url.query:
            raise ValueError("Require file SQLite database")
        return AnalyticsService(str(Path(url.database).resolve()), settings.ANALYTICS_MAX_DAYS,
                                settings.ANALYTICS_MAX_RECORDS, settings.ANALYTICS_MAX_CONFIGURATIONS,
                                settings.ANALYTICS_SOURCE_KIND)
    except (ValueError, TypeError, ArgumentError, OSError):
        raise AppError("ANALYTICS_SOURCE_UNAVAILABLE", "Analityka wymaga skonfigurowanej plikowej bazy SQLite", 503)


def require_access(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
                   service: Annotated[AnalyticsService, Depends(get_service)]):
    if credentials is None:
        raise AppError("UNAUTHORIZED", "Wymagany token Bearer z istniejacego logowania", 401)
    try:
        user_id = int(decode_access_token(credentials.credentials)["sub"])
    except (InvalidTokenError, ValueError, TypeError, KeyError, OverflowError):
        raise AppError("UNAUTHORIZED", "Niepoprawny lub wygasly token", 401)
    try:
        # Same accounts and permissions, without the normal writable SQLAlchemy session.
        with open_readonly(service.database_path) as connection:
            user = connection.execute("SELECT aktywny,rola FROM users WHERE id=?", (user_id,)).fetchone()
    except ValueError:
        raise AppError("ANALYTICS_SOURCE_UNAVAILABLE", "Nie mozna odczytac zrodla analityki lub kont uzytkownikow", 503)
    if user is None:
        raise AppError("UNAUTHORIZED", "Uzytkownik tokenu nie istnieje", 401)
    if not account_has_access(user["aktywny"], user["rola"]):
        raise AppError("FORBIDDEN", "Uzytkownik nie ma uprawnien dostepu", 403)


router = APIRouter(prefix="/api/analytics", tags=["analytics"], dependencies=[Depends(require_access)])
Service = Annotated[AnalyticsService, Depends(get_service)]


def execute(operation, *args):
    try:
        return operation(*args)
    except (AnalysisLimitError, RecordLimitExceeded) as exc:
        raise AppError("ANALYTICS_LIMIT_EXCEEDED", str(exc), 413)
    except HistoryQualityError as exc:
        raise AppError("ANALYTICS_DATA_QUALITY", str(exc), 409)
    except ElectricityDeviceError as exc:
        raise AppError("ANALYTICS_DEVICE_TYPE", str(exc), 422)
    except OverflowError:
        raise AppError("VALIDATION_ERROR", "Obliczenie wykracza poza obslugiwany zakres dat lub wartosci liczbowych", 422)
    except (ValueError, OSError):
        # Do not disclose server paths or raw SQLite diagnostics to HTTP clients.
        raise AppError("ANALYTICS_SOURCE_UNAVAILABLE", "Nie mozna odczytac poprawnego zrodla analityki", 503)


@router.get("/devices", response_model=DevicesResponse)
def devices(service: Service, query: Annotated[EmptyQuery, Query()]):
    return execute(service.devices)


@router.get("/quality", response_model=QualityResponse)
def quality(service: Service, query: Annotated[QualityQuery, Query()]):
    return execute(service.quality, query)


@router.post("/compare", response_model=CompareResponse)
def compare(request: CompareRequest, service: Service):
    return execute(service.compare, request)


@router.get("/environment", response_model=EnvironmentResponse)
def environment(service: Service, query: Annotated[EnvironmentQuery, Query()]):
    return execute(service.environment, query)


@router.get("/electricity", response_model=ElectricityResponse)
def electricity(service: Service, query: Annotated[ElectricityQuery, Query()]):
    return execute(service.electricity, query)


@router.get("/heat", response_model=HeatResponse)
def heat(service: Service, query: Annotated[HeatQuery, Query()]):
    return execute(service.heat, query)


@router.post("/recommend", response_model=RecommendResponse)
def recommendation(request: RecommendRequest, service: Service):
    return execute(service.recommend, request)

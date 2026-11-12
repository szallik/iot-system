from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400):
        self.code = code
        self.message = message
        self.status_code = status_code


async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


async def http_exception_handler(request: Request, exc: HTTPException):
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        409: "CONFLICT",
        422: "VALIDATION_ERROR",
        500: "INTERNAL_SERVER_ERROR",
        503: "SERVICE_UNAVAILABLE",
    }
    error_code = code_map.get(exc.status_code, "HTTP_ERROR")
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": error_code, "message": str(exc.detail)}},
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    details = "; ".join(
        [f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors()]
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": f"Błąd walidacji danych: {details}",
            }
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Wystąpił nieoczekiwany błąd serwera.",
            }
        },
    )
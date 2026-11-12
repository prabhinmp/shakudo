"""Application errors and their mapping to HTTP responses.

Every failure the API can report is one of these classes, so the status codes
live in exactly one place:
  400 InvalidInputError   bad image / negative replicas / bad quantities / bad names
  401 AuthError           missing or invalid token
  403 ForbiddenError      valid token, but role or namespace not allowed
  404 NotFoundError       deployment id does not exist (or is not visible to caller)
  409 ConflictError       name already exists in namespace
  429 RateLimitError      too many requests (Retry-After header set)
  500 KubernetesError     Kubernetes API call failed (message is meaningful)
"""
import json
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

log = logging.getLogger("app.errors")


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidInputError(AppError):
    status_code, code = 400, "invalid_input"


class AuthError(AppError):
    status_code, code = 401, "unauthorized"


class ForbiddenError(AppError):
    status_code, code = 403, "forbidden"


class NotFoundError(AppError):
    status_code, code = 404, "not_found"


class ConflictError(AppError):
    status_code, code = 409, "conflict"


class RateLimitError(AppError):
    status_code, code = 429, "rate_limited"

    def __init__(self, retry_after: int):
        super().__init__("Too many requests")
        self.retry_after = retry_after


class KubernetesError(AppError):
    status_code, code = 500, "kubernetes_error"


def _body(code: str, message: str, details=None) -> dict:
    err = {"code": code, "message": message}
    if details:
        err["details"] = details
    return {"error": err}


def _audit_denied(request: Request, exc: AppError) -> None:
    """One audit line for every refused request. Never logs the token or the body."""
    principal = getattr(request.state, "principal", None)
    logging.getLogger("audit").warning(
        json.dumps(
            {
                "actor": principal.sub if principal else "anonymous",
                "role": principal.role if principal else None,
                "action": f"{request.method} {request.url.path}",
                "client_ip": request.client.host if request.client else None,
                "outcome": "denied",
                "reason": exc.code,
            }
        )
    )


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def app_error(request: Request, exc: AppError):
        headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
        if isinstance(exc, RateLimitError):
            headers = {"Retry-After": str(exc.retry_after)}
        if exc.status_code in (401, 403, 429):
            _audit_denied(request, exc)
        return JSONResponse(_body(exc.code, exc.message), status_code=exc.status_code, headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError):
        # Only field location + message are returned. We deliberately do NOT echo the
        # submitted value back, because it could contain secrets (e.g. env values).
        details = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return JSONResponse(_body("invalid_input", "Request validation failed", details), status_code=400)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        log.exception("unhandled error")  # full detail goes to logs, not to the client
        return JSONResponse(_body("internal_error", "Internal server error"), status_code=500)

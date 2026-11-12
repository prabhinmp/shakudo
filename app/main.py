import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api import deployments, health
from app.config import MIN_SECRET_BYTES, Settings, get_settings
from app.core.ratelimit import Limiters
from app.errors import register_error_handlers
from app.services.k8s import KubernetesService


def create_app(settings: Settings | None = None, kube=None, limiters: Limiters | None = None) -> FastAPI:
    """App factory. Tests pass their own settings and a fake `kube` service.

    Run with:  uvicorn app.main:create_app --factory
    """
    settings = settings or get_settings()
    if len(settings.jwt_secret.encode()) < MIN_SECRET_BYTES:
        # Fail fast: refusing to start is safer than running with a guessable signing key.
        raise RuntimeError(f"DEPLOY_API_JWT_SECRET must be set and at least {MIN_SECRET_BYTES} bytes")
    logging.basicConfig(level=settings.log_level)

    # Interactive docs are disabled: less attack surface for an admin-style API.
    app = FastAPI(title="Deployment Manager API", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.kube = kube or KubernetesService.from_env(settings)
    app.state.limiters = limiters or Limiters(
        settings.rate_limit_read_per_min, settings.rate_limit_write_per_min, settings.rate_limit_auth_fail_per_min
    )
    register_error_handlers(app)

    @app.middleware("http")
    async def limit_body_size(request: Request, call_next):
        # Rejects oversized bodies that declare Content-Length. (Chunked uploads are
        # additionally bounded by the ingress/proxy limit; see README limitations.)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > settings.max_body_bytes:
            return JSONResponse(
                {"error": {"code": "payload_too_large", "message": "Request body too large"}}, status_code=413
            )
        return await call_next(request)

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        # Added last = outermost, so it covers every response (errors and 413 included).
        # Data always comes live from the cluster; stop browsers/proxies from caching it too.
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        return response

    app.include_router(health.router)
    app.include_router(deployments.router)
    return app

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.api.deployments import get_kube
from app.errors import AppError

router = APIRouter(tags=["health"])


@router.get("/healthz")
def healthz() -> dict:
    """Liveness: the process is up. Deliberately does NOT depend on Kubernetes, otherwise a
    brief API-server blip would make Kubernetes restart perfectly healthy pods."""
    return {"status": "ok"}


@router.get("/readyz")
def readyz(kube=Depends(get_kube)):
    """Readiness: can we reach the Kubernetes API? If not, stop routing traffic to this pod."""
    try:
        kube.ping()
    except AppError:
        return JSONResponse({"status": "kubernetes unreachable"}, status_code=503)
    return {"status": "ready"}

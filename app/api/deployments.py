"""HTTP layer only: authenticate, authorize, validate, call the service, audit.

Order of checks for every request:  token -> role -> namespace -> input policy -> Kubernetes.
"""
from fastapi import APIRouter, Depends, Path, Query, Request, Response

from app.config import Settings
from app.core import policy
from app.core.audit import audit
from app.core.security import (
    Principal,
    authorize_namespace,
    get_settings_dep,
    namespace_allowed,
    require_role,
)
from app.errors import NotFoundError
from app.schemas import (
    DNS_LABEL_RE,
    UUID_RE,
    DeploymentCreate,
    DeploymentDetail,
    DeploymentPatch,
    DeploymentSummary,
    NamespaceList,
)

router = APIRouter(prefix="/api/deployments", tags=["deployments"])
namespaces_router = APIRouter(prefix="/api/namespaces", tags=["namespaces"])

IdPath = Path(pattern=UUID_RE, description="Deployment UID")


def get_kube(request: Request):
    return request.app.state.kube


def _visible(uid: str, principal: Principal, settings: Settings, kube) -> DeploymentSummary:
    """Find a deployment by id. If the caller may not access its namespace, answer 404
    (not 403) so callers can't probe for the existence of deployments they can't see."""
    found = kube.locate(uid)
    if not namespace_allowed(principal, found.namespace, settings):
        audit(principal, "access-denied", found.namespace, uid, outcome="hidden-as-404")
        raise NotFoundError(f"Deployment '{uid}' not found")
    return found


@router.post("", status_code=201, response_model=DeploymentSummary)
def create_deployment(
    body: DeploymentCreate,
    principal: Principal = Depends(require_role("admin")),
    settings: Settings = Depends(get_settings_dep),
    kube=Depends(get_kube),
):
    authorize_namespace(principal, body.namespace, settings)
    policy.check_image_allowed(body.image, settings)
    policy.check_replicas(body.replicas, settings)
    kube.validate_create(body)  # live cluster pre-flight: duplicate name / terminating namespace -> 409
    kube.ensure_namespace(body.namespace)
    created = kube.create_deployment(body)
    audit(principal, "create", body.namespace, created.id)
    return created


@router.get("", response_model=list[DeploymentSummary])
def list_deployments(
    namespace: str | None = Query(default=None, max_length=63, pattern=DNS_LABEL_RE),
    principal: Principal = Depends(require_role("viewer")),
    settings: Settings = Depends(get_settings_dep),
    kube=Depends(get_kube),
):
    if namespace:
        authorize_namespace(principal, namespace, settings)
        return kube.list_deployments(namespace)
    # No filter: return only what this caller is allowed to see.
    return [d for d in kube.list_deployments(None) if namespace_allowed(principal, d.namespace, settings)]


@namespaces_router.get("", response_model=NamespaceList)
def list_namespaces(
    principal: Principal = Depends(require_role("viewer")),
    settings: Settings = Depends(get_settings_dep),
    kube=Depends(get_kube),
):
    """Namespaces that hold managed deployments AND that this caller may see (for the UI filter)."""
    found = {d.namespace for d in kube.list_deployments(None) if namespace_allowed(principal, d.namespace, settings)}
    return NamespaceList(namespaces=sorted(found))


@router.get("/{id}", response_model=DeploymentDetail)
def get_deployment(
    id: str = IdPath,
    principal: Principal = Depends(require_role("viewer")),
    settings: Settings = Depends(get_settings_dep),
    kube=Depends(get_kube),
):
    found = _visible(id, principal, settings, kube)
    return kube.get_detail(found.namespace, found.name)


@router.patch("/{id}", response_model=DeploymentSummary)
def patch_deployment(
    body: DeploymentPatch,
    id: str = IdPath,
    principal: Principal = Depends(require_role("operator")),
    settings: Settings = Depends(get_settings_dep),
    kube=Depends(get_kube),
):
    if body.image is not None:
        policy.check_image_allowed(body.image, settings)
    if body.replicas is not None:
        policy.check_replicas(body.replicas, settings)
    found = _visible(id, principal, settings, kube)
    updated = kube.patch_deployment(found.namespace, found.name, body.replicas, body.image)
    audit(principal, "patch", found.namespace, id)
    return updated


@router.post("/{id}/restart", response_model=DeploymentSummary)
def restart_deployment(
    id: str = IdPath,
    principal: Principal = Depends(require_role("operator")),
    settings: Settings = Depends(get_settings_dep),
    kube=Depends(get_kube),
):
    found = _visible(id, principal, settings, kube)
    result = kube.restart_deployment(found.namespace, found.name)
    audit(principal, "restart", found.namespace, id)
    return result


@router.delete("/{id}", status_code=204)
def delete_deployment(
    id: str = IdPath,
    principal: Principal = Depends(require_role("admin")),
    settings: Settings = Depends(get_settings_dep),
    kube=Depends(get_kube),
):
    found = _visible(id, principal, settings, kube)
    kube.delete_deployment(found.namespace, found.name, id)
    audit(principal, "delete", found.namespace, id)
    return Response(status_code=204)

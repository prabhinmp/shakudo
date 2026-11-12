"""The ONLY module that talks to the Kubernetes API.

Routes call these methods; they never touch the kubernetes client directly. Tests swap
this class for an in-memory fake (tests/fakes.py) that has the same methods.

Every deployment we create carries the label  app.kubernetes.io/managed-by=deploy-api.
READS cover the whole cluster (so the dashboard shows built-in namespaces and foreign deployments);
each item is flagged managed/read_only, and MUTATIONS are only allowed on managed deployments in
non-protected namespaces.
"""
import functools
import json
from datetime import datetime, timezone

import urllib3
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from app.config import Settings, is_protected_namespace
from app.errors import AppError, ConflictError, ForbiddenError, KubernetesError, NotFoundError
from app.schemas import (
    MANAGED_BY_LABEL,
    MANAGED_BY_VALUE,
    NAME_LABEL,
    Condition,
    ContainerStatus,
    DeploymentCreate,
    DeploymentDetail,
    DeploymentSummary,
    PodStatus,
    Replicas,
)

MANAGED_SELECTOR = f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}"
RESTART_ANNOTATION = "kubectl.kubernetes.io/restartedAt"


# ---------- pure helpers (easy to unit-test, no cluster needed) ----------
def build_manifest(spec: DeploymentCreate, settings: Settings) -> dict:
    """Turn a validated request into a Deployment manifest with hardened defaults."""
    selector = {NAME_LABEL: spec.name, MANAGED_BY_LABEL: MANAGED_BY_VALUE}
    labels = {**spec.labels, **selector}  # reserved keys were rejected earlier, selector always wins

    container = {
        "name": spec.name,
        "image": spec.image,
        "ports": [{"containerPort": spec.port}],
        "resources": spec.resources.model_dump(),
    }
    if settings.pod_hardening:
        container["securityContext"] = {
            "runAsNonRoot": True,
            "runAsUser": spec.run_as_user or settings.pod_run_as_user,   # never 0: schema rejects it
            "allowPrivilegeEscalation": False,
            "readOnlyRootFilesystem": settings.pod_read_only_root_fs,
            "capabilities": {"drop": ["ALL"]},
            "seccompProfile": {"type": "RuntimeDefault"},
        }
    elif spec.run_as_user:
        container["securityContext"] = {"runAsUser": spec.run_as_user}   # hardening off: only an explicit uid is applied
    if spec.env:
        container["env"] = [{"name": e.name, "value": e.value} for e in spec.env]
    if spec.command:
        container["command"] = spec.command
    if spec.args:
        container["args"] = spec.args

    pod_spec = {
        "automountServiceAccountToken": False,  # workloads get no API credentials
        "containers": [container],
    }
    # Scratch space only (emptyDir: disk-backed, size-capped). The root filesystem stays read-only.
    mounts, volumes = [], []
    if settings.pod_hardening and settings.pod_writable_tmp:     # scratch /tmp only matters with a read-only root
        mounts.append({"name": "tmp", "mountPath": "/tmp"})
        volumes.append({"name": "tmp", "emptyDir": {"sizeLimit": settings.pod_tmp_size_limit}})
    for i, path in enumerate(p for p in spec.writable_paths if not (settings.pod_hardening and settings.pod_writable_tmp and p == "/tmp")):
        mounts.append({"name": f"scratch-{i}", "mountPath": path})
        volumes.append({"name": f"scratch-{i}", "emptyDir": {"sizeLimit": settings.pod_tmp_size_limit}})
    if mounts:
        container["volumeMounts"] = mounts
        pod_spec["volumes"] = volumes

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": spec.name, "namespace": spec.namespace, "labels": labels},
        "spec": {
            "replicas": spec.replicas,
            "selector": {"matchLabels": selector},
            "template": {
                "metadata": {"labels": labels},
                "spec": pod_spec,
            },
        },
    }


def to_summary(d, denied=()) -> DeploymentSummary:
    s = d.status
    labels = d.metadata.labels or {}
    managed = labels.get(MANAGED_BY_LABEL) == MANAGED_BY_VALUE
    desired = d.spec.replicas if d.spec.replicas is not None else 0
    return DeploymentSummary(
        id=d.metadata.uid,
        name=d.metadata.name,
        namespace=d.metadata.namespace,
        image=d.spec.template.spec.containers[0].image,
        labels=labels,
        replicas=Replicas(
            desired=desired,
            ready=(s.ready_replicas or 0) if s else 0,
            available=(s.available_replicas or 0) if s else 0,
            unavailable=(s.unavailable_replicas or 0) if s else 0,
        ),
        created_at=d.metadata.creation_timestamp,
        managed=managed,
        read_only=(not managed) or is_protected_namespace(d.metadata.namespace, denied),
    )


def _container_state(cs) -> str:
    st = cs.state
    if st is None:
        return "unknown"
    if st.running:
        return "running"
    if st.waiting:
        return f"waiting: {st.waiting.reason}" if st.waiting.reason else "waiting"
    if st.terminated:
        return f"terminated: {st.terminated.reason}" if st.terminated.reason else "terminated"
    return "unknown"


def to_pod_status(p) -> PodStatus:
    statuses = [
        ContainerStatus(name=c.name, ready=bool(c.ready), restart_count=c.restart_count or 0, state=_container_state(c))
        for c in (p.status.container_statuses or [])
    ]
    return PodStatus(
        name=p.metadata.name,
        phase=p.status.phase or "Unknown",
        restart_count=sum(c.restart_count for c in statuses),
        container_statuses=statuses,
    )


def _translate(exc: Exception) -> AppError:
    """Convert a Kubernetes client failure into one of our errors with a useful message."""
    if isinstance(exc, ApiException):
        message = exc.reason or "request failed"
        try:
            message = json.loads(exc.body).get("message", message)  # e.g. "exceeded quota: ..."
        except (TypeError, ValueError, AttributeError):
            pass
        if exc.status == 404:
            return NotFoundError("Resource not found")
        if exc.status == 409:
            return ConflictError(message[:300])
        return KubernetesError(f"Kubernetes API error ({exc.status}): {message[:300]}")
    return KubernetesError("Kubernetes API unreachable or timed out")


def _mutating(fn):
    """Defense in depth: a service method that changes the cluster refuses protected namespaces
    (kube-system, kube-public, kube-node-lease, default, kube-*), even if a route forgot to check.
    The namespace is the first argument after `self` (or spec.namespace for create)."""

    @functools.wraps(fn)
    def wrapper(self, first, *args, **kwargs):
        ns = getattr(first, "namespace", first)
        if is_protected_namespace(ns, self.settings.denied_namespaces):
            raise ForbiddenError(f"Namespace '{ns}' is protected and cannot be modified")
        return fn(self, first, *args, **kwargs)

    return wrapper


def _guarded(fn):
    """Decorator: any client/network failure becomes a clean AppError (never a stack trace)."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except AppError:
            raise
        except (ApiException, urllib3.exceptions.HTTPError, OSError) as exc:
            raise _translate(exc) from exc

    return wrapper


# ---------- the service ----------
class KubernetesService:
    def __init__(self, apps: client.AppsV1Api, core: client.CoreV1Api, version: client.VersionApi, settings: Settings):
        self.apps, self.core, self.version = apps, core, version
        self.timeout = settings.kube_timeout_seconds
        self.settings = settings

    @classmethod
    def from_env(cls, settings: Settings) -> "KubernetesService":
        try:
            config.load_incluster_config()  # running inside a pod
        except config.ConfigException:
            config.load_kube_config()  # local development (kubeconfig)
        # Readiness pings use their own client: short timeout and NO retries, so a dead API server
        # makes /readyz fail in ~2s instead of stalling a probe for 40s of retries.
        probe_cfg = client.Configuration.get_default_copy()
        probe_cfg.retries = 0
        return cls(client.AppsV1Api(), client.CoreV1Api(), client.VersionApi(client.ApiClient(probe_cfg)), settings)

    def _summary(self, d) -> DeploymentSummary:
        return to_summary(d, self.settings.denied_namespaces)

    @property
    def _t(self) -> dict:
        return {"_request_timeout": self.timeout}

    @_guarded
    def ping(self) -> None:
        self.version.get_code(_request_timeout=2)

    @_guarded
    def validate_create(self, spec: DeploymentCreate) -> None:
        """Read-only pre-flight against the LIVE cluster, run before anything is created.

        Rejects (409) when the namespace is being deleted, or when ANY deployment with this name
        already exists there, including ones this API did not create (which list_deployments
        cannot see). Runs before ensure_namespace so a rejected request never leaves a new
        namespace behind. Check-then-create is racy, so create_deployment still maps the
        cluster's own 409 as the final backstop.
        """
        try:
            ns = self.core.read_namespace(spec.namespace, **self._t)
        except ApiException as e:
            if e.status == 404:
                return  # namespace does not exist yet, so no deployment in it can clash
            raise
        if ns.status is not None and ns.status.phase == "Terminating":
            raise ConflictError(f"Namespace '{spec.namespace}' is being deleted; try again later")
        try:
            self.apps.read_namespaced_deployment(spec.name, spec.namespace, **self._t)
        except ApiException as e:
            if e.status == 404:
                return
            raise
        raise ConflictError(f"Deployment '{spec.name}' already exists in namespace '{spec.namespace}'")

    @_guarded
    @_mutating
    def ensure_namespace(self, namespace: str) -> None:
        try:
            self.core.read_namespace(namespace, **self._t)
            return
        except ApiException as e:
            if e.status != 404:
                raise
        body = {"metadata": {"name": namespace, "labels": {MANAGED_BY_LABEL: MANAGED_BY_VALUE}}}
        try:
            self.core.create_namespace(body, **self._t)
        except ApiException as e:
            if e.status != 409:  # 409 = someone created it between our check and create: fine
                raise

    @_guarded
    @_mutating
    def create_deployment(self, spec: DeploymentCreate) -> DeploymentSummary:
        try:
            d = self.apps.create_namespaced_deployment(spec.namespace, build_manifest(spec, self.settings), **self._t)
        except ApiException as e:
            if e.status == 409:
                raise ConflictError(f"Deployment '{spec.name}' already exists in namespace '{spec.namespace}'")
            raise
        return self._summary(d)

    @_guarded
    def list_deployments(self, namespace: str | None) -> list[DeploymentSummary]:
        if namespace:
            items = self.apps.list_namespaced_deployment(namespace, **self._t).items
        else:
            items = self.apps.list_deployment_for_all_namespaces(**self._t).items
        return [self._summary(d) for d in items]

    @_guarded
    def list_namespaces(self) -> list[str]:
        """Every namespace in the cluster (read-only view; the UI filter lists them all)."""
        return sorted(n.metadata.name for n in self.core.list_namespace(**self._t).items)

    @_guarded
    def locate(self, uid: str) -> DeploymentSummary:
        """Find a managed deployment by UID (the API can't query by UID, so list + match)."""
        for d in self.list_deployments(None):
            if d.id == uid:
                return d
        raise NotFoundError(f"Deployment '{uid}' not found")

    @_guarded
    def get_detail(self, namespace: str, name: str) -> DeploymentDetail:
        d = self.apps.read_namespaced_deployment(name, namespace, **self._t)
        selector = ",".join(f"{k}={v}" for k, v in d.spec.selector.match_labels.items())
        pods = self.core.list_namespaced_pod(namespace, label_selector=selector, **self._t).items
        conditions = [
            Condition(type=c.type, status=c.status, reason=c.reason, message=c.message,
                      last_transition_time=c.last_transition_time)
            for c in (d.status.conditions or [])
        ]
        return DeploymentDetail(**self._summary(d).model_dump(), pods=[to_pod_status(p) for p in pods], conditions=conditions)

    @_guarded
    @_mutating
    def patch_deployment(self, namespace: str, name: str, replicas: int | None, image: str | None) -> DeploymentSummary:
        spec: dict = {}
        if replicas is not None:
            spec["replicas"] = replicas
        if image is not None:
            # strategic merge patch merges the containers list by "name" (container name == deployment name)
            spec["template"] = {"spec": {"containers": [{"name": name, "image": image}]}}
        return self._summary(self.apps.patch_namespaced_deployment(name, namespace, {"spec": spec}, **self._t))

    @_guarded
    @_mutating
    def restart_deployment(self, namespace: str, name: str) -> DeploymentSummary:
        stamp = datetime.now(timezone.utc).isoformat()
        body = {"spec": {"template": {"metadata": {"annotations": {RESTART_ANNOTATION: stamp}}}}}
        return self._summary(self.apps.patch_namespaced_deployment(name, namespace, body, **self._t))

    @_guarded
    @_mutating
    def delete_deployment(self, namespace: str, name: str, uid: str) -> None:
        opts = client.V1DeleteOptions(
            propagation_policy="Foreground",  # delete ReplicaSets/Pods first, then the Deployment
            preconditions=client.V1Preconditions(uid=uid),  # refuse if the name now points at a different object
        )
        self.apps.delete_namespaced_deployment(name, namespace, body=opts, **self._t)

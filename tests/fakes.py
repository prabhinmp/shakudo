"""In-memory stand-in for KubernetesService (same method names)."""
import uuid

from app.errors import ConflictError, KubernetesError, NotFoundError
from app.schemas import DeploymentCreate, DeploymentDetail, DeploymentSummary, Replicas


class FakeKube:
    def __init__(self):
        self.items: dict[str, DeploymentSummary] = {}
        self.namespaces: set[str] = set()
        self.restarts: list[str] = []
        self.fail_with: str | None = None  # set to simulate a Kubernetes outage
        self.calls: list[str] = []  # order of service calls, to prove pre-flight runs first
        self.foreign: set[tuple[str, str]] = set()  # (namespace, name) deployments NOT created by this API
        self.terminating: set[str] = set()
        self.reads = 0  # counts live reads, to prove GETs are never served from a cache

    def _maybe_fail(self):
        if self.fail_with:
            raise KubernetesError(self.fail_with)

    def ping(self):
        self._maybe_fail()

    def validate_create(self, spec):
        self.calls.append("validate_create")
        self._maybe_fail()
        if spec.namespace in self.terminating:
            raise ConflictError(f"Namespace '{spec.namespace}' is being deleted; try again later")
        exists = (spec.namespace, spec.name) in self.foreign or any(
            d.name == spec.name and d.namespace == spec.namespace for d in self.items.values())
        if exists:
            raise ConflictError(f"Deployment '{spec.name}' already exists in namespace '{spec.namespace}'")

    def ensure_namespace(self, ns):
        self.calls.append("ensure_namespace")
        self._maybe_fail()
        self.namespaces.add(ns)

    def create_deployment(self, spec: DeploymentCreate):
        self.calls.append("create_deployment")
        self._maybe_fail()
        if any(d.name == spec.name and d.namespace == spec.namespace for d in self.items.values()):
            raise ConflictError(f"Deployment '{spec.name}' already exists in namespace '{spec.namespace}'")
        d = DeploymentSummary(
            id=str(uuid.uuid4()), name=spec.name, namespace=spec.namespace, image=spec.image,
            labels=spec.labels, replicas=Replicas(desired=spec.replicas, ready=0, available=0, unavailable=0),
        )
        self.items[d.id] = d
        return d

    def list_deployments(self, namespace):
        self.reads += 1
        self._maybe_fail()
        return [d for d in self.items.values() if namespace is None or d.namespace == namespace]

    def locate(self, uid):
        self.reads += 1
        self._maybe_fail()
        if uid not in self.items:
            raise NotFoundError(f"Deployment '{uid}' not found")
        return self.items[uid]

    def get_detail(self, namespace, name):
        self.reads += 1
        d = next(x for x in self.items.values() if x.name == name and x.namespace == namespace)
        return DeploymentDetail(**d.model_dump(), pods=[], conditions=[])

    def patch_deployment(self, namespace, name, replicas, image):
        d = next(x for x in self.items.values() if x.name == name and x.namespace == namespace)
        if replicas is not None:
            d.replicas.desired = replicas
        if image is not None:
            d.image = image
        return d

    def restart_deployment(self, namespace, name):
        self.restarts.append(name)
        return next(x for x in self.items.values() if x.name == name and x.namespace == namespace)

    def delete_deployment(self, namespace, name, uid):
        del self.items[uid]

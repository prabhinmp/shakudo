"""Integration tests: NO fakes, NO mocks.

The real FastAPI app + the real KubernetesService run against the real cluster in your current
kubeconfig context. Results are cross-checked with an INDEPENDENT raw Kubernetes client, so we
verify what is really in the cluster rather than what the API claims.

Run:  .venv/bin/python -m pytest -m integration -v      (skipped automatically if no cluster)
Each run uses its own namespace (demo-it-<random>) and deletes it afterwards.
"""
import time
import uuid

import jwt
import pytest
from fastapi.testclient import TestClient
from kubernetes import client as k, config

from app.config import Settings
from app.main import create_app

pytestmark = pytest.mark.integration
SECRET = "integration-secret-that-is-long-enough-123456"
PROTECTED = ["kube-system", "kube-public", "kube-node-lease", "default"]


def _wait(fn, timeout=90, every=2, what="condition"):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(every)
    raise AssertionError(f"timed out waiting for {what}")


@pytest.fixture(scope="module")
def raw():
    """Independent client straight to the cluster (not through our service)."""
    try:
        config.load_kube_config()
        k.VersionApi().get_code(_request_timeout=3)
    except Exception as e:  # no cluster available
        pytest.skip(f"no reachable cluster: {type(e).__name__}")
    return k.AppsV1Api(), k.CoreV1Api()


@pytest.fixture(scope="module")
def ns(raw):
    name = f"demo-it-{uuid.uuid4().hex[:8]}"
    yield name
    raw[1].delete_namespace(name, _request_timeout=10)  # cleanup (cascades everything inside)


@pytest.fixture(scope="module")
def api():
    settings = Settings(jwt_secret=SECRET)
    return TestClient(create_app(settings), raise_server_exceptions=False)  # real KubernetesService, no fake


def tok(role="admin", ns_allow=("demo-it-*",), sub="it"):
    return {"Authorization": "Bearer " + jwt.encode(
        {"sub": sub, "iss": "deploy-api", "aud": "deploy-api", "exp": int(time.time()) + 600,
         "role": role, "namespaces": list(ns_allow)}, SECRET, algorithm="HS256")}


def body(ns, name="echo", **over):
    b = {"name": name, "namespace": ns, "image": "hashicorp/http-echo:latest", "port": 5678,
         "args": ["-listen=:5678", "-text=it"], "env": [{"name": "MODE", "value": "it"}],
         "resources": {"requests": {"cpu": "50m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
         "labels": {"app": name, "suite": "integration"}}
    b.update(over)
    return b


def test_readyz_reaches_the_real_cluster(api):
    assert api.get("/readyz").status_code == 200


def test_full_lifecycle_against_real_cluster(api, raw, ns):
    apps, core = raw

    # ---- create: verify the REAL object, not the API's response ----
    r = api.post("/api/deployments", json=body(ns), headers=tok())
    assert r.status_code == 201, r.text
    uid = r.json()["id"]
    real = apps.read_namespaced_deployment("echo", ns)
    assert real.metadata.uid == uid
    assert real.metadata.labels["app.kubernetes.io/managed-by"] == "deploy-api"
    assert real.metadata.labels["suite"] == "integration"
    c = real.spec.template.spec.containers[0]
    assert c.security_context.run_as_non_root is True and c.security_context.allow_privilege_escalation is False
    assert real.spec.template.spec.automount_service_account_token is False
    assert core.read_namespace(ns).metadata.labels["app.kubernetes.io/managed-by"] == "deploy-api"

    # ---- duplicate -> 409 straight from the live cluster ----
    assert api.post("/api/deployments", json=body(ns), headers=tok()).status_code == 409

    # ---- detail: wait for real pods, compare with the raw pod list ----
    def ready():
        d = api.get(f"/api/deployments/{uid}", headers=tok(role="viewer")).json()
        return d if d["replicas"]["ready"] == 1 and d["pods"] else None
    detail = _wait(ready, what="pod ready")
    real_pods = [p.metadata.name for p in core.list_namespaced_pod(ns).items]
    assert [p["name"] for p in detail["pods"]] == real_pods
    assert detail["pods"][0]["phase"] == "Running" and detail["pods"][0]["container_statuses"][0]["state"] == "running"
    assert any(c["type"] == "Available" and c["status"] == "True" for c in detail["conditions"])

    # ---- live reads: change the cluster behind the API's back, read immediately ----
    apps.patch_namespaced_deployment_scale("echo", ns, {"spec": {"replicas": 3}})
    assert api.get(f"/api/deployments/{uid}", headers=tok(role="viewer")).json()["replicas"]["desired"] == 3
    listed = api.get(f"/api/deployments?namespace={ns}", headers=tok(role="viewer")).json()
    assert [d["replicas"]["desired"] for d in listed] == [3]

    # ---- PATCH: scale + image, verified on the real object ----
    r = api.patch(f"/api/deployments/{uid}", json={"replicas": 1, "image": "hashicorp/http-echo:1.0"}, headers=tok("operator"))
    assert r.status_code == 200
    real = apps.read_namespaced_deployment("echo", ns)
    assert real.spec.replicas == 1 and real.spec.template.spec.containers[0].image == "hashicorp/http-echo:1.0"

    # ---- restart: annotation really set ----
    assert api.post(f"/api/deployments/{uid}/restart", headers=tok("operator")).status_code == 200
    ann = apps.read_namespaced_deployment("echo", ns).spec.template.metadata.annotations
    assert "kubectl.kubernetes.io/restartedAt" in ann

    # ---- delete: cascade really removes ReplicaSets and Pods ----
    assert api.delete(f"/api/deployments/{uid}", headers=tok()).status_code == 204
    _wait(lambda: not apps.list_namespaced_deployment(ns).items, what="deployment gone")
    _wait(lambda: not apps.list_namespaced_replica_set(ns).items, what="replicasets gone")
    _wait(lambda: not core.list_namespaced_pod(ns).items, what="pods gone")
    assert api.get(f"/api/deployments/{uid}", headers=tok(role="viewer")).status_code == 404


def test_deployment_made_outside_the_api_is_rejected_and_untouched(api, raw, ns):
    apps, _ = raw
    api.post("/api/deployments", json=body(ns, name="seed"), headers=tok())  # creates the namespace if absent
    foreign = k.V1Deployment(
        metadata=k.V1ObjectMeta(name="foreign"),
        spec=k.V1DeploymentSpec(
            selector=k.V1LabelSelector(match_labels={"x": "y"}),
            template=k.V1PodTemplateSpec(metadata=k.V1ObjectMeta(labels={"x": "y"}),
                                         spec=k.V1PodSpec(containers=[k.V1Container(name="c", image="hashicorp/http-echo:latest")]))))
    apps.create_namespaced_deployment(ns, foreign)
    r = api.post("/api/deployments", json=body(ns, name="foreign"), headers=tok())
    assert r.status_code == 409 and "already exists" in r.json()["error"]["message"]
    untouched = apps.read_namespaced_deployment("foreign", ns)
    assert "app.kubernetes.io/managed-by" not in (untouched.metadata.labels or {})
    items = {d["name"]: d for d in api.get(f"/api/deployments?namespace={ns}", headers=tok("viewer")).json()}
    assert items["foreign"]["read_only"] is True and items["foreign"]["managed"] is False   # visible, flagged read-only
    fid = items["foreign"]["id"]
    assert api.patch(f"/api/deployments/{fid}", json={"replicas": 5}, headers=tok("operator")).status_code == 403
    assert api.delete(f"/api/deployments/{fid}", headers=tok()).status_code == 403
    assert apps.read_namespaced_deployment("foreign", ns).spec.replicas == (untouched.spec.replicas)  # never modified


@pytest.mark.parametrize("protected", PROTECTED)
def test_protected_namespaces_are_never_modified_in_the_real_cluster(api, raw, protected):
    apps, core = raw
    snap = lambda: sorted((d.metadata.name, d.metadata.resource_version) for d in apps.list_namespaced_deployment(protected).items)
    before, ns_rv = snap(), core.read_namespace(protected).metadata.resource_version
    god = tok(ns_allow=("*",), sub="god")
    assert api.post("/api/deployments", json=body(protected, name="evil"), headers=god).status_code == 403
    listed = api.get(f"/api/deployments?namespace={protected}", headers=god)
    assert listed.status_code == 200 and all(d["read_only"] for d in listed.json())   # visible, never writable
    for d in listed.json():
        assert api.patch(f"/api/deployments/{d['id']}", json={"replicas": 9}, headers=tok("operator", ns_allow=("*",))).status_code == 403
        assert api.delete(f"/api/deployments/{d['id']}", headers=god).status_code == 403
    assert snap() == before
    assert core.read_namespace(protected).metadata.resource_version == ns_rv

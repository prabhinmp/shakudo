"""(1) GETs always reflect the live cluster, never a cache.
   (2) POST pre-validates against what is actually in the cluster before creating anything."""
import pathlib
import re
from unittest.mock import MagicMock

import pytest
from kubernetes import client as k
from kubernetes.client.rest import ApiException

from app.config import Settings
from app.errors import ConflictError, KubernetesError
from app.schemas import DeploymentCreate
from app.services.k8s import KubernetesService

BAD_ID = "11111111-1111-1111-1111-111111111111"


def create(client, auth, body, **kw):
    return client.post("/api/deployments", json=body, headers=auth(**kw))


# =====================================================================
# (1) GET = live data
# =====================================================================
def test_every_response_is_no_store(client, auth, valid_body):
    uid = create(client, auth, valid_body).json()["id"]
    responses = [
        client.get("/healthz"),
        client.get("/api/deployments", headers=auth()),
        client.get(f"/api/deployments/{uid}", headers=auth()),
        client.get(f"/api/deployments/{BAD_ID}", headers=auth()),     # 404
        client.get("/api/deployments"),                               # 401
        client.get("/api/deployments/not-a-uuid", headers=auth()),    # 400
        client.post("/api/deployments", content=b"x" * 70_000,        # 413
                    headers={**auth(), "content-type": "application/json"}),
    ]
    for r in responses:
        assert r.headers["cache-control"] == "no-store", r.status_code
        assert r.headers["pragma"] == "no-cache"


def test_each_get_hits_the_cluster(client, auth, valid_body, kube):
    uid = create(client, auth, valid_body).json()["id"]
    before = kube.reads
    for _ in range(3):
        client.get("/api/deployments", headers=auth())
    assert kube.reads - before == 3                      # one live read per request, none served from memory
    before = kube.reads
    for _ in range(3):
        client.get(f"/api/deployments/{uid}", headers=auth())
    assert kube.reads - before >= 6                      # locate + detail, every time


def test_list_reflects_out_of_band_changes_immediately(client, auth, valid_body, kube):
    create(client, auth, valid_body)
    assert len(client.get("/api/deployments", headers=auth()).json()) == 1
    # someone changes the cluster behind the API's back (like `kubectl delete`)
    kube.items.clear()
    assert client.get("/api/deployments", headers=auth()).json() == []


def test_detail_reflects_out_of_band_changes_immediately(client, auth, valid_body, kube):
    uid = create(client, auth, valid_body).json()["id"]
    assert client.get(f"/api/deployments/{uid}", headers=auth()).json()["replicas"]["desired"] == 1
    kube.items[uid].replicas.desired = 7                 # like `kubectl scale --replicas=7`
    kube.items[uid].replicas.ready = 5
    body = client.get(f"/api/deployments/{uid}", headers=auth()).json()
    assert (body["replicas"]["desired"], body["replicas"]["ready"]) == (7, 5)


def test_out_of_band_delete_gives_404_not_stale_200(client, auth, valid_body, kube):
    uid = create(client, auth, valid_body).json()["id"]
    assert client.get(f"/api/deployments/{uid}", headers=auth()).status_code == 200
    del kube.items[uid]
    assert client.get(f"/api/deployments/{uid}", headers=auth()).status_code == 404


def test_cluster_outage_is_reported_not_masked_by_a_cache(client, auth, valid_body, kube):
    create(client, auth, valid_body)
    assert client.get("/api/deployments", headers=auth()).status_code == 200
    kube.fail_with = "Kubernetes API unreachable or timed out"
    r = client.get("/api/deployments", headers=auth())   # a cache would have happily returned the old list
    assert r.status_code == 500 and "unreachable" in r.json()["error"]["message"]


def test_no_caching_machinery_in_the_codebase():
    """Tripwire: fails if someone adds a cache layer in front of the cluster."""
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    for f in root.rglob("*.py"):
        text = f.read_text()
        if f.name == "config.py":
            continue  # lru_cache on get_settings() caches configuration, not cluster data
        assert not re.search(r"lru_cache|@cache\b|cachetools|TTLCache|functools\.cache", text), f"cache found in {f}"


# --- the real service class: every call goes to the (mocked) cluster client ---
def _deployment(uid="uid-1", replicas=1, ready=0):
    return k.V1Deployment(
        metadata=k.V1ObjectMeta(name="web", namespace="team-a", uid=uid, labels={"a": "b"}),
        spec=k.V1DeploymentSpec(
            replicas=replicas,
            selector=k.V1LabelSelector(match_labels={"app.kubernetes.io/name": "web"}),
            template=k.V1PodTemplateSpec(spec=k.V1PodSpec(containers=[k.V1Container(name="web", image="nginx:1")])),
        ),
        status=k.V1DeploymentStatus(ready_replicas=ready, available_replicas=ready),
    )


def _svc():
    apps, core = MagicMock(), MagicMock()
    return KubernetesService(apps, core, MagicMock(), Settings(jwt_secret="x" * 32)), apps, core


def test_service_list_and_locate_query_cluster_every_call():
    svc, apps, _ = _svc()
    apps.list_deployment_for_all_namespaces.return_value = k.V1DeploymentList(items=[_deployment(replicas=1)])
    assert svc.locate("uid-1").replicas.desired == 1
    apps.list_deployment_for_all_namespaces.return_value = k.V1DeploymentList(items=[_deployment(replicas=9, ready=9)])
    fresh = svc.locate("uid-1")                                  # same service object, cluster changed
    assert (fresh.replicas.desired, fresh.replicas.ready) == (9, 9)
    assert apps.list_deployment_for_all_namespaces.call_count == 2


def test_service_detail_queries_deployment_and_pods_every_call():
    svc, apps, core = _svc()
    apps.read_namespaced_deployment.return_value = _deployment()
    core.list_namespaced_pod.return_value = k.V1PodList(items=[])
    svc.get_detail("team-a", "web")
    svc.get_detail("team-a", "web")
    assert apps.read_namespaced_deployment.call_count == 2
    assert core.list_namespaced_pod.call_count == 2


# =====================================================================
# (2) POST pre-validation against the live cluster
# =====================================================================
def test_successful_create_runs_preflight_first(client, auth, valid_body, kube):
    assert create(client, auth, valid_body).status_code == 201
    assert kube.calls == ["validate_create", "ensure_namespace", "create_deployment"]


def test_duplicate_managed_deployment_rejected_before_anything_is_created(client, auth, valid_body, kube):
    create(client, auth, valid_body)
    kube.calls.clear()
    r = create(client, auth, valid_body)
    assert r.status_code == 409 and "already exists" in r.json()["error"]["message"]
    assert kube.calls == ["validate_create"]            # stopped at the pre-flight, nothing else touched


def test_deployment_not_created_by_this_api_is_also_rejected(client, auth, valid_body, kube):
    kube.foreign.add(("team-a", "web"))                  # exists in the cluster, but not managed by us
    r = create(client, auth, valid_body)
    assert r.status_code == 409 and "already exists" in r.json()["error"]["message"]
    assert kube.items == {} and kube.namespaces == set()  # and no stray namespace was created
    # ...yet it stays invisible to listing (we only expose what we manage)
    assert client.get("/api/deployments", headers=auth()).json() == []


def test_terminating_namespace_is_rejected(client, auth, valid_body, kube):
    kube.terminating.add("team-a")
    r = create(client, auth, valid_body)
    assert r.status_code == 409 and "being deleted" in r.json()["error"]["message"]
    assert "ensure_namespace" not in kube.calls and kube.items == {}


def test_same_name_in_a_different_namespace_is_fine(client, auth, valid_body, kube):
    kube.foreign.add(("team-b", "web"))
    assert create(client, auth, valid_body).status_code == 201


def test_preflight_sees_live_state_name_is_free_again_after_out_of_band_delete(client, auth, valid_body, kube):
    uid = create(client, auth, valid_body).json()["id"]
    assert create(client, auth, valid_body).status_code == 409
    del kube.items[uid]                                  # deleted directly in the cluster
    assert create(client, auth, valid_body).status_code == 201


def test_cluster_failure_during_preflight_is_500_and_creates_nothing(client, auth, valid_body, kube):
    kube.fail_with = "Kubernetes API unreachable or timed out"
    r = create(client, auth, valid_body)
    assert r.status_code == 500 and kube.items == {} and kube.namespaces == set()


def test_invalid_or_forbidden_requests_never_reach_the_cluster(client, auth, valid_body, kube):
    bad = {**valid_body, "replicas": -1}
    assert create(client, auth, bad).status_code == 400
    assert create(client, auth, valid_body, role="viewer").status_code == 403
    assert create(client, auth, {**valid_body, "namespace": "other-team"}).status_code == 403
    assert client.post("/api/deployments", json=valid_body).status_code == 401
    assert kube.calls == []                              # no probing the cluster on behalf of rejected callers


# --- the real service class's pre-flight ---
@pytest.fixture
def spec(valid_body):
    return DeploymentCreate(**valid_body)


def test_service_preflight_namespace_missing_means_free(spec):
    svc, apps, core = _svc()
    core.read_namespace.side_effect = ApiException(status=404)
    svc.validate_create(spec)
    apps.read_namespaced_deployment.assert_not_called()  # nothing can exist in a namespace that does not


def test_service_preflight_free_name_passes(spec):
    svc, apps, core = _svc()
    core.read_namespace.return_value = k.V1Namespace(status=k.V1NamespaceStatus(phase="Active"))
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    svc.validate_create(spec)
    apps.read_namespaced_deployment.assert_called_once()


def test_service_preflight_existing_deployment_conflicts(spec):
    svc, apps, core = _svc()
    core.read_namespace.return_value = k.V1Namespace(status=k.V1NamespaceStatus(phase="Active"))
    apps.read_namespaced_deployment.return_value = _deployment()   # note: no managed-by label needed
    with pytest.raises(ConflictError, match="already exists in namespace 'team-a'"):
        svc.validate_create(spec)


def test_service_preflight_terminating_namespace_conflicts(spec):
    svc, apps, core = _svc()
    core.read_namespace.return_value = k.V1Namespace(status=k.V1NamespaceStatus(phase="Terminating"))
    with pytest.raises(ConflictError, match="being deleted"):
        svc.validate_create(spec)
    apps.read_namespaced_deployment.assert_not_called()


@pytest.mark.parametrize("where", ["namespace", "deployment"])
def test_service_preflight_api_errors_become_500(spec, where):
    svc, apps, core = _svc()
    core.read_namespace.return_value = k.V1Namespace(status=k.V1NamespaceStatus(phase="Active"))
    err = ApiException(status=500, reason="boom")
    if where == "namespace":
        core.read_namespace.side_effect = err
    else:
        apps.read_namespaced_deployment.side_effect = err
    with pytest.raises(KubernetesError):
        svc.validate_create(spec)


def test_service_preflight_is_read_only(spec):
    svc, apps, core = _svc()
    core.read_namespace.return_value = k.V1Namespace(status=k.V1NamespaceStatus(phase="Active"))
    apps.read_namespaced_deployment.side_effect = ApiException(status=404)
    svc.validate_create(spec)
    core.create_namespace.assert_not_called()
    apps.create_namespaced_deployment.assert_not_called()
    apps.patch_namespaced_deployment.assert_not_called()


def test_service_create_still_maps_a_race_409(spec):
    """Backstop: the name was free at pre-flight but taken a moment later."""
    svc, apps, _ = _svc()
    apps.create_namespaced_deployment.side_effect = ApiException(status=409)
    with pytest.raises(ConflictError, match="already exists"):
        svc.create_deployment(spec)

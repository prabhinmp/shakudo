"""Endpoint behaviour and the 400/404/409/500 contract."""
import copy

import pytest

BAD_ID = "11111111-1111-1111-1111-111111111111"


def create(client, auth, body, **kw):
    return client.post("/api/deployments", json=body, headers=auth(**kw))


# ---------- create ----------
def test_create_returns_201_with_uid_and_status(client, auth, valid_body, kube):
    r = create(client, auth, valid_body)
    assert r.status_code == 201
    data = r.json()
    assert data["id"] and data["replicas"]["desired"] == 1  # replicas defaults to 1
    assert "team-a" in kube.namespaces  # namespace was ensured


def test_duplicate_is_409(client, auth, valid_body):
    assert create(client, auth, valid_body).status_code == 201
    r = create(client, auth, valid_body)
    assert r.status_code == 409
    assert "already exists" in r.json()["error"]["message"]


def test_same_name_other_namespace_is_ok(client, auth, valid_body):
    create(client, auth, valid_body)
    valid_body["namespace"] = "team-b"
    assert create(client, auth, valid_body).status_code == 201


def _mut(path, value):
    def apply(body):
        cur = body
        keys = path.split(".")
        for k in keys[:-1]:
            cur = cur[k]
        if value is DELETE:
            del cur[keys[-1]]
        else:
            cur[keys[-1]] = value
    return apply


DELETE = object()


@pytest.mark.parametrize("mutation", [
    _mut("name", "Bad_Name"), _mut("name", "-x"), _mut("name", "a" * 64),   # DNS-incompatible
    _mut("namespace", "Bad NS"),
    _mut("image", "NGINX:latest"), _mut("image", "ng inx"), _mut("image", ""),  # bad image format
    _mut("replicas", -1),                                                    # negative replicas
    _mut("port", 0), _mut("port", 70000),
    _mut("resources.requests.cpu", "abc"), _mut("resources.requests.cpu", "-1"),
    _mut("resources.limits.memory", "12XB"), _mut("resources.limits.memory", "0Mi"),
    _mut("resources.requests.cpu", "500m"),                                  # request > limit
    _mut("resources.limits.cpu", "64"),                                      # above cap
    _mut("labels", {"bad key!": "v"}), _mut("labels", {"app.kubernetes.io/managed-by": "x"}),
    _mut("env", [{"name": "1BAD", "value": "v"}]),
    _mut("env", [{"name": "A", "value": "1"}, {"name": "A", "value": "2"}]),
    _mut("name", DELETE), _mut("namespace", DELETE), _mut("image", DELETE), _mut("port", DELETE),
    _mut("resources", DELETE),                                              # mandatory fields
    _mut("privileged", True), _mut("hostNetwork", True),                     # unknown fields rejected
])
def test_invalid_input_is_400(client, auth, valid_body, mutation):
    body = copy.deepcopy(valid_body)
    mutation(body)
    r = create(client, auth, body)
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "invalid_input"


def test_validation_error_does_not_echo_values(client, auth, valid_body):
    valid_body["env"] = [{"name": "1BAD", "value": "super-secret-value"}]
    assert "super-secret-value" not in create(client, auth, valid_body).text


def test_optional_fields_accepted(client, auth, valid_body):
    valid_body.update(env=[{"name": "MODE", "value": "x"}], command=["/bin/app"], args=["--v"], replicas=3)
    r = create(client, auth, valid_body)
    assert r.status_code == 201 and r.json()["replicas"]["desired"] == 3


def test_registry_not_allowed_is_400(client, auth, valid_body):
    valid_body["image"] = "evil.example.com/miner:1"
    assert create(client, auth, valid_body).status_code == 400


def test_replicas_above_cap_is_400(client, auth, valid_body):
    valid_body["replicas"] = 999
    assert create(client, auth, valid_body).status_code == 400


# ---------- kubernetes failure => 500 with message ----------
def test_kubernetes_failure_is_500_with_message(client, auth, valid_body, kube):
    kube.fail_with = "Kubernetes API error (403): exceeded quota: compute"
    r = create(client, auth, valid_body)
    assert r.status_code == 500
    assert "exceeded quota" in r.json()["error"]["message"]


def test_unexpected_exception_is_500_without_leak(client, auth, kube, monkeypatch):
    def boom(ns):
        raise ValueError("secret internal detail")
    monkeypatch.setattr(kube, "list_deployments", boom)
    r = client.get("/api/deployments", headers=auth())
    assert r.status_code == 500 and "secret internal detail" not in r.text


def test_readyz_503_when_k8s_down(client, kube):
    assert client.get("/readyz").status_code == 200
    kube.fail_with = "down"
    assert client.get("/readyz").status_code == 503


# ---------- list ----------
def test_list_filters_by_namespace_and_scopes_to_caller(client, auth, valid_body):
    create(client, auth, valid_body)
    valid_body.update(name="api", namespace="team-b")
    create(client, auth, valid_body)
    valid_body.update(name="other", namespace="elsewhere")
    create(client, auth, valid_body, namespaces=["*"])

    names = lambda r: sorted(d["name"] for d in r.json())
    assert names(client.get("/api/deployments", headers=auth())) == ["api", "web"]       # 'elsewhere' hidden
    assert names(client.get("/api/deployments?namespace=team-a", headers=auth())) == ["web"]
    assert client.get("/api/deployments?namespace=elsewhere", headers=auth()).status_code == 403
    assert client.get("/api/deployments?namespace=BAD!", headers=auth()).status_code == 400


# ---------- get / patch / restart / delete ----------
def test_get_unknown_id_is_404(client, auth):
    assert client.get(f"/api/deployments/{BAD_ID}", headers=auth()).status_code == 404


def test_malformed_id_is_400(client, auth):
    assert client.get("/api/deployments/not-a-uuid", headers=auth()).status_code == 400


def test_get_returns_detail(client, auth, valid_body):
    uid = create(client, auth, valid_body).json()["id"]
    r = client.get(f"/api/deployments/{uid}", headers=auth(role="viewer"))
    assert r.status_code == 200 and {"replicas", "pods", "conditions"} <= r.json().keys()


def test_other_teams_deployment_is_404_not_403(client, auth, valid_body):
    uid = create(client, auth, valid_body).json()["id"]
    for call in (lambda h: client.get(f"/api/deployments/{uid}", headers=h),
                 lambda h: client.post(f"/api/deployments/{uid}/restart", headers=h),
                 lambda h: client.delete(f"/api/deployments/{uid}", headers=h)):
        assert call(auth(namespaces=["team-z"])).status_code == 404


def test_patch_scale_and_image(client, auth, valid_body):
    uid = create(client, auth, valid_body).json()["id"]
    r = client.patch(f"/api/deployments/{uid}", json={"replicas": 4, "image": "nginx:1.27"}, headers=auth(role="operator"))
    assert r.status_code == 200
    assert r.json()["replicas"]["desired"] == 4 and r.json()["image"] == "nginx:1.27"


@pytest.mark.parametrize("body", [{}, {"replicas": -1}, {"image": "BAD IMAGE"}, {"replicas": 1, "name": "x"},
                                  {"replicas": 1000}, {"image": "evil.io/x:1"}])
def test_patch_invalid_is_400(client, auth, valid_body, body):
    uid = create(client, auth, valid_body).json()["id"]
    assert client.patch(f"/api/deployments/{uid}", json=body, headers=auth(role="operator")).status_code == 400


def test_patch_missing_is_404(client, auth):
    assert client.patch(f"/api/deployments/{BAD_ID}", json={"replicas": 2}, headers=auth()).status_code == 404


def test_restart(client, auth, valid_body, kube):
    uid = create(client, auth, valid_body).json()["id"]
    assert client.post(f"/api/deployments/{uid}/restart", headers=auth(role="operator")).status_code == 200
    assert kube.restarts == ["web"]
    assert client.post(f"/api/deployments/{BAD_ID}/restart", headers=auth()).status_code == 404


def test_delete(client, auth, valid_body):
    uid = create(client, auth, valid_body).json()["id"]
    assert client.delete(f"/api/deployments/{uid}", headers=auth()).status_code == 204
    assert client.get(f"/api/deployments/{uid}", headers=auth()).status_code == 404
    assert client.delete(f"/api/deployments/{uid}", headers=auth()).status_code == 404

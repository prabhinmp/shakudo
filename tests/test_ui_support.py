"""API features the web UI relies on: health field, namespaces list, static UI hosting, security headers."""
import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.main import create_app
from app.schemas import ContainerStatus, DeploymentDetail, PodStatus, Replicas, replica_health


@pytest.mark.parametrize("desired,ready,unavail,expected", [
    (0, 0, 0, "idle"),
    (3, 3, 0, "healthy"),
    (3, 2, 1, "degraded"),
    (3, 3, 1, "degraded"),   # rolling update: all ready but a surge pod is still starting
    (3, 0, 3, "down"),
])
def test_replica_health(desired, ready, unavail, expected):
    assert replica_health(Replicas(desired=desired, ready=ready, available=ready, unavailable=unavail)) == expected


def _detail(state):
    pod = PodStatus(name="p", phase="Running", restart_count=5,
                    container_statuses=[ContainerStatus(name="c", ready=True, restart_count=5, state=state)])
    return DeploymentDetail(id="i", name="n", namespace="team-a", image="x", labels={},
                            replicas=Replicas(desired=1, ready=1, available=1, unavailable=0), pods=[pod], conditions=[])


def test_detail_health_downgraded_by_broken_pod():
    assert _detail("running").health == "healthy"
    assert _detail("waiting: CrashLoopBackOff").health == "degraded"


def _create(client, auth, valid_body, **over):
    r = client.post("/api/deployments", json={**valid_body, **over}, headers=auth(role="admin", namespaces=["*"]))
    assert r.status_code == 201, r.text
    return r.json()


def test_list_and_detail_include_health(client, auth, valid_body):
    created = _create(client, auth, valid_body)
    assert created["health"] == "down"  # fake cluster: wanted 1, 0 ready
    items = client.get("/api/deployments", headers=auth(role="viewer")).json()
    assert items[0]["health"] == "down"
    assert client.get(f"/api/deployments/{created['id']}", headers=auth(role="viewer")).json()["health"] == "down"


def test_namespaces_only_those_visible_to_caller(client, auth, valid_body):
    _create(client, auth, valid_body, name="a", namespace="team-a")
    _create(client, auth, valid_body, name="b", namespace="team-b")
    _create(client, auth, valid_body, name="c", namespace="other")
    r = client.get("/api/namespaces", headers=auth(role="viewer", namespaces=["team-*"]))
    assert r.status_code == 200
    assert r.json() == {"namespaces": ["team-a", "team-b"]}


def test_namespaces_requires_auth(client):
    assert client.get("/api/namespaces").status_code == 401


def test_security_headers_on_every_response(client):
    r = client.get("/healthz")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"


def test_ui_not_mounted_when_not_built(client):
    assert client.get("/ui/", follow_redirects=False).status_code == 404


def test_ui_served_when_built(tmp_path, monkeypatch, settings, kube):
    (tmp_path / "index.html").write_text("<html>hello ui</html>")
    monkeypatch.setattr(main_mod, "UI_DIR", tmp_path)
    c = TestClient(create_app(settings, kube))
    assert "hello ui" in c.get("/ui/").text
    r = c.get("/", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/ui/"

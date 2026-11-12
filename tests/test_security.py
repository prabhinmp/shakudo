"""Every rejection path of the security core, through the real HTTP stack."""
import time

import jwt
import pytest

from tests.conftest import SECRET


def test_healthz_needs_no_auth(client):
    assert client.get("/healthz").status_code == 200


def test_no_token_is_401(client):
    r = client.get("/api/deployments")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_garbage_token_is_401(client):
    assert client.get("/api/deployments", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_expired_token_is_401(client, auth):
    assert client.get("/api/deployments", headers=auth(exp=int(time.time()) - 10)).status_code == 401


def test_wrong_signature_is_401(client, settings):
    bad = jwt.encode({"sub": "x", "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
                      "exp": int(time.time()) + 60, "role": "admin", "namespaces": ["*"]},
                     "a-different-secret-that-is-32-bytes!!", algorithm="HS256")
    assert client.get("/api/deployments", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_alg_none_token_is_401(client, settings):
    forged = jwt.encode({"sub": "x", "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
                         "exp": int(time.time()) + 60, "role": "admin", "namespaces": ["*"]}, None, algorithm="none")
    assert client.get("/api/deployments", headers={"Authorization": f"Bearer {forged}"}).status_code == 401


@pytest.mark.parametrize("override", [{"aud": "other"}, {"iss": "other"}, {"role": "root"}, {"role": None},
                                      {"namespaces": None}, {"namespaces": "team-a"}, {"sub": None}])
def test_bad_claims_are_401(client, auth, override):
    assert client.get("/api/deployments", headers=auth(**override)).status_code == 401


def test_viewer_cannot_create(client, auth, valid_body):
    assert client.post("/api/deployments", json=valid_body, headers=auth(role="viewer")).status_code == 403


def test_operator_cannot_create_or_delete(client, auth, valid_body):
    assert client.post("/api/deployments", json=valid_body, headers=auth(role="operator")).status_code == 403
    assert client.delete("/api/deployments/" + "0" * 8 + "-0000-0000-0000-" + "0" * 12,
                         headers=auth(role="operator")).status_code == 403


def test_namespace_outside_allowlist_is_403(client, auth, valid_body):
    valid_body["namespace"] = "other-team"
    assert client.post("/api/deployments", json=valid_body, headers=auth()).status_code == 403


@pytest.mark.parametrize("ns", ["kube-system", "kube-anything", "default"])
def test_system_namespaces_denied_even_with_wildcard(client, auth, valid_body, ns):
    valid_body["namespace"] = ns
    assert client.post("/api/deployments", json=valid_body, headers=auth(namespaces=["*"])).status_code == 403


def test_auth_checked_before_body_validation(client):
    # an unauthenticated caller must not learn anything about the validation rules
    assert client.post("/api/deployments", json={"junk": 1}).status_code == 401


def test_secret_too_short_refuses_to_start():
    from app.config import Settings
    from app.main import create_app
    with pytest.raises(RuntimeError):
        create_app(Settings(jwt_secret="short"), object())


def test_oversized_body_is_413(client, auth):
    r = client.post("/api/deployments", content=b"x" * 70_000, headers={**auth(), "content-type": "application/json"})
    assert r.status_code == 413


@pytest.mark.parametrize("ns", ["kube-system", "kube-public", "kube-node-lease", "default"])
def test_protected_namespaces_blocked_on_every_endpoint_even_with_empty_denylist(ns, kube, make_token, valid_body):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app
    from tests.conftest import SECRET
    weak = Settings(jwt_secret=SECRET, denied_namespaces=[])  # deny-list misconfigured away
    c = TestClient(create_app(weak, kube), raise_server_exceptions=False)
    h = {"Authorization": f"Bearer {make_token(namespaces=['*'])}"}
    valid_body["namespace"] = ns
    assert c.post("/api/deployments", json=valid_body, headers=h).status_code == 403
    assert c.get(f"/api/deployments?namespace={ns}", headers=h).status_code == 403
    assert kube.namespaces == set() and kube.items == {}

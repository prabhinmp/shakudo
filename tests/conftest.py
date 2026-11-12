import time

import jwt
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from tests.fakes import FakeKube

SECRET = "test-secret-that-is-at-least-32-bytes-long"


@pytest.fixture
def settings():
    return Settings(jwt_secret=SECRET)


@pytest.fixture
def kube():
    return FakeKube()


@pytest.fixture
def client(settings, kube):
    return TestClient(create_app(settings, kube), raise_server_exceptions=False)


@pytest.fixture
def make_token(settings):
    def _make(role="admin", namespaces=["team-*"], **overrides):  # passed through as-is so tests can send bad values
        claims = {"sub": "alice", "iss": settings.jwt_issuer, "aud": settings.jwt_audience,
                  "exp": int(time.time()) + 300, "role": role, "namespaces": namespaces}
        claims.update(overrides)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, SECRET, algorithm="HS256")
    return _make


@pytest.fixture
def auth(make_token):
    def _auth(**kw):
        return {"Authorization": f"Bearer {make_token(**kw)}"}
    return _auth


@pytest.fixture
def valid_body():
    return {
        "name": "web", "namespace": "team-a", "image": "nginx:latest", "port": 80,
        "resources": {"requests": {"cpu": "100m", "memory": "64Mi"}, "limits": {"cpu": "200m", "memory": "128Mi"}},
        "labels": {"app": "web"},
    }

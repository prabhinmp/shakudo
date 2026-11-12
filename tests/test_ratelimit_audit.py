import json
import logging

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.ratelimit import Limiters, SlidingWindowLimiter
from app.main import create_app
from tests.conftest import SECRET


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------- limiter unit tests ----------------
def test_limiter_allows_then_blocks_then_recovers():
    clock = Clock()
    lim = SlidingWindowLimiter(3, 60, clock)
    assert [lim.hit("a") for _ in range(3)] == [None, None, None]
    assert lim.hit("a") == 60            # 4th is blocked, retry in a full window
    clock.t += 30
    assert lim.hit("a") == 30            # still blocked, retry time shrinks
    clock.t += 31
    assert lim.hit("a") is None          # window slid past the first hits


def test_limiter_keys_are_independent():
    lim = SlidingWindowLimiter(1, 60, Clock())
    assert lim.hit("a") is None and lim.hit("b") is None
    assert lim.hit("a") is not None


def test_limiter_memory_is_bounded():
    lim = SlidingWindowLimiter(5, 60, Clock(), max_keys=100)
    for i in range(1000):
        lim.hit(f"ip-{i}")
    assert len(lim._hits) <= 100


# ---------------- HTTP behaviour ----------------
@pytest.fixture
def small(kube, make_token):
    """App with tiny limits: 3 reads, 2 writes, 3 auth failures per window."""
    clock = Clock()
    s = Settings(jwt_secret=SECRET)
    app = create_app(s, kube, Limiters(3, 2, 3, clock=clock))
    c = TestClient(app, raise_server_exceptions=False)
    hdr = lambda **kw: {"Authorization": f"Bearer {make_token(**kw)}"}
    return c, hdr, clock


def test_reads_limited_with_retry_after(small):
    c, hdr, _ = small
    codes = [c.get("/api/deployments", headers=hdr()).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]
    r = c.get("/api/deployments", headers=hdr())
    assert r.headers["retry-after"].isdigit() and r.json()["error"]["code"] == "rate_limited"


def test_writes_have_their_own_tighter_budget(small, valid_body):
    c, hdr, _ = small
    codes = []
    for i in range(3):
        valid_body["name"] = f"w{i}"
        codes.append(c.post("/api/deployments", json=valid_body, headers=hdr()).status_code)
    assert codes == [201, 201, 429]
    assert c.get("/api/deployments", headers=hdr()).status_code == 200  # reads unaffected


def test_limits_are_per_subject(small):
    c, hdr, _ = small
    for _ in range(4):
        c.get("/api/deployments", headers=hdr(sub="alice"))
    assert c.get("/api/deployments", headers=hdr(sub="alice")).status_code == 429
    assert c.get("/api/deployments", headers=hdr(sub="bob")).status_code == 200


def test_limit_recovers_after_window(small):
    c, hdr, clock = small
    for _ in range(4):
        c.get("/api/deployments", headers=hdr())
    clock.t += 61
    assert c.get("/api/deployments", headers=hdr()).status_code == 200


def test_forbidden_attempts_consume_budget(small, valid_body):
    c, hdr, _ = small
    codes = [c.post("/api/deployments", json=valid_body, headers=hdr(role="viewer")).status_code for _ in range(3)]
    assert codes == [403, 403, 429]


def test_failed_auth_locks_out_ip_even_for_good_tokens(small):
    c, hdr, clock = small
    assert [c.get("/api/deployments").status_code for _ in range(3)] == [401, 401, 401]
    r = c.get("/api/deployments", headers=hdr())  # valid token, but this IP is cooling down
    assert r.status_code == 429 and "retry-after" in r.headers
    clock.t += 61
    assert c.get("/api/deployments", headers=hdr()).status_code == 200


def test_health_is_never_rate_limited(small):
    c, _, _ = small
    assert all(c.get("/healthz").status_code == 200 for _ in range(50))


# ---------------- audit of denied attempts ----------------
def _audit_records(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "audit"]


def test_denied_attempts_are_audited(client, auth, valid_body, caplog):
    caplog.set_level(logging.INFO, logger="audit")
    client.get("/api/deployments")                                                        # 401
    client.post("/api/deployments", json=valid_body, headers=auth(role="viewer"))        # 403 role
    valid_body["namespace"] = "kube-system"
    client.post("/api/deployments", json=valid_body, headers=auth(namespaces=["*"]))     # 403 protected ns
    recs = _audit_records(caplog)
    assert [r["reason"] for r in recs] == ["unauthorized", "forbidden", "forbidden"]
    assert recs[0]["actor"] == "anonymous"
    assert recs[1]["actor"] == "alice" and recs[1]["role"] == "viewer"
    assert [r["action"] for r in recs] == ["GET /api/deployments", "POST /api/deployments", "POST /api/deployments"]
    assert all(r["outcome"] == "denied" for r in recs)


def test_rate_limit_is_audited(small, caplog):
    c, hdr, _ = small
    caplog.set_level(logging.INFO, logger="audit")
    for _ in range(4):
        c.get("/api/deployments", headers=hdr())
    assert _audit_records(caplog)[-1]["reason"] == "rate_limited"


def test_cross_namespace_probe_is_audited_but_returns_404(client, auth, valid_body, caplog):
    uid = client.post("/api/deployments", json=valid_body, headers=auth()).json()["id"]
    caplog.set_level(logging.INFO, logger="audit")
    assert client.get(f"/api/deployments/{uid}", headers=auth(sub="mallory", namespaces=["team-z"])).status_code == 404
    rec = [r for r in _audit_records(caplog) if r["action"] == "access-denied"]
    assert rec and rec[0]["actor"] == "mallory" and rec[0]["outcome"] == "hidden-as-404"


def test_audit_never_contains_token_or_body(client, auth, valid_body, caplog):
    caplog.set_level(logging.INFO, logger="audit")
    token = auth(role="viewer")["Authorization"].split()[1]
    valid_body["env"] = [{"name": "PASSWORD", "value": "hunter2"}]
    client.post("/api/deployments", json=valid_body, headers={"Authorization": f"Bearer {token}"})
    text = " ".join(r.getMessage() for r in caplog.records if r.name == "audit")
    assert token not in text and "hunter2" not in text

"""Which namespaces a caller may use, and that the 403 says WHY."""
import pytest

PROTECTED = ["kube-system", "kube-public", "kube-node-lease", "default"]


def create(client, auth, body, **kw):
    return client.post("/api/deployments", json=body, headers=auth(**kw))


def test_wildcard_token_creates_a_brand_new_namespace(client, auth, valid_body, kube):
    valid_body["namespace"] = "testuser-api"                        # does not exist yet
    r = create(client, auth, valid_body, namespaces=["*"])
    assert r.status_code == 201, r.text
    assert "testuser-api" in kube.namespaces                         # the API created it


def test_narrow_token_gets_a_message_that_explains_the_allowlist(client, auth, valid_body, kube):
    valid_body["namespace"] = "testuser-api"
    r = create(client, auth, valid_body, namespaces=["demo-*"])
    assert r.status_code == 403
    msg = r.json()["error"]["message"]
    assert "testuser-api" in msg and "token" in msg and "demo-*" in msg and "'*'" in msg
    assert "built-in" not in msg                                     # NOT confused with the protected built-in rule
    assert kube.namespaces == set() and kube.calls == []              # nothing created, cluster never touched


@pytest.mark.parametrize("ns", PROTECTED + ["kube-anything"])
def test_built_in_namespaces_get_a_message_that_says_protected_even_with_a_wildcard_token(client, auth, valid_body, kube, ns):
    valid_body["namespace"] = ns
    r = create(client, auth, valid_body, namespaces=["*"])
    assert r.status_code == 403
    assert "built-in" in r.json()["error"]["message"] and "token" not in r.json()["error"]["message"]
    assert kube.namespaces == set() and kube.calls == []


@pytest.mark.parametrize("ns", PROTECTED)
def test_protected_namespaces_can_be_listed_read_only(client, auth, ns):
    r = client.get(f"/api/deployments?namespace={ns}", headers=auth(namespaces=["*"]))
    assert r.status_code == 200


def test_list_filter_outside_allowlist_explains_the_token(client, auth):
    r = client.get("/api/deployments?namespace=team-z", headers=auth(namespaces=["team-a"]))
    assert r.status_code == 403 and "team-a" in r.json()["error"]["message"]


def test_wildcard_token_sees_every_non_protected_namespace_in_listings(client, auth, valid_body):
    for ns in ("alpha-ns", "beta-ns"):
        valid_body.update(name=f"app-{ns}", namespace=ns)
        assert create(client, auth, valid_body, namespaces=["*"]).status_code == 201
    names = sorted(d["namespace"] for d in client.get("/api/deployments", headers=auth(namespaces=["*"])).json())
    assert names == ["alpha-ns", "beta-ns"]


def test_tenant_isolation_still_holds_for_narrow_tokens(client, auth, valid_body):
    valid_body.update(namespace="alpha-ns")
    uid = create(client, auth, valid_body, namespaces=["*"]).json()["id"]
    assert client.get(f"/api/deployments/{uid}", headers=auth(namespaces=["team-z"])).status_code == 404
    assert client.get("/api/deployments", headers=auth(namespaces=["team-z"])).json() == []

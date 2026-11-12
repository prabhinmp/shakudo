"""Read-everything / write-only-what-we-own: built-in namespaces and foreign deployments are visible but immutable."""
import pytest

PROTECTED = ["default", "kube-system", "kube-public", "kube-node-lease"]


def test_namespace_list_contains_every_cluster_namespace_even_empty_ones(client, auth, kube):
    kube.cluster_namespaces.add("empty-ns")
    r = client.get("/api/namespaces", headers=auth(role="viewer", namespaces=["*"]))
    assert r.status_code == 200
    assert set(PROTECTED + ["empty-ns"]) <= set(r.json()["namespaces"])


def test_namespace_list_still_respects_the_token_scope(client, auth, kube):
    kube.cluster_namespaces.update({"team-a", "team-b"})
    r = client.get("/api/namespaces", headers=auth(role="viewer", namespaces=["team-a"]))
    assert r.json()["namespaces"] == ["team-a"]


def test_list_includes_builtin_and_foreign_deployments_flagged_read_only(client, auth, kube, valid_body):
    kube.add_foreign("coredns", "kube-system")
    kube.add_foreign("someone-elses", "team-a")
    mine = client.post("/api/deployments", json=valid_body, headers=auth()).json()
    items = {d["name"]: d for d in client.get("/api/deployments", headers=auth(role="viewer", namespaces=["*"])).json()}
    assert items["coredns"]["read_only"] is True and items["coredns"]["managed"] is False
    assert items["someone-elses"]["read_only"] is True
    assert items[mine["name"]]["read_only"] is False and items[mine["name"]]["managed"] is True


@pytest.mark.parametrize("ns", PROTECTED)
def test_builtin_namespace_deployments_cannot_be_scaled_restarted_or_deleted(client, auth, kube, ns):
    uid = kube.add_foreign("sys-app", ns).id
    h = auth(role="admin", namespaces=["*"])
    assert client.patch(f"/api/deployments/{uid}", json={"replicas": 5}, headers=h).status_code == 403
    assert client.post(f"/api/deployments/{uid}/restart", headers=h).status_code == 403
    assert client.delete(f"/api/deployments/{uid}", headers=h).status_code == 403
    assert kube.items[uid].replicas.desired == 1 and kube.restarts == []


def test_foreign_deployment_in_a_normal_namespace_is_also_untouchable(client, auth, kube):
    uid = kube.add_foreign("legacy", "team-a").id
    h = auth(role="admin", namespaces=["*"])
    r = client.patch(f"/api/deployments/{uid}", json={"replicas": 5}, headers=h)
    assert r.status_code == 403 and "read-only" in r.json()["error"]["message"]
    assert client.delete(f"/api/deployments/{uid}", headers=h).status_code == 403 and uid in kube.items


def test_detail_of_a_builtin_deployment_is_readable_and_flagged(client, auth, kube):
    uid = kube.add_foreign("coredns", "kube-system").id
    d = client.get(f"/api/deployments/{uid}", headers=auth(role="viewer", namespaces=["*"])).json()
    assert d["read_only"] is True


def test_builtin_namespaces_still_cannot_be_created_into(client, auth, valid_body):
    valid_body["namespace"] = "kube-system"
    assert client.post("/api/deployments", json=valid_body, headers=auth(namespaces=["*"])).status_code == 403

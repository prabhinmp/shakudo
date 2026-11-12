"""writable_paths + run_as_user: make more images run WITHOUT weakening the lock-down."""
import copy

import pytest

from app.config import Settings
from app.schemas import DeploymentCreate
from app.services.k8s import build_manifest

SETTINGS = Settings(jwt_secret="x" * 32, pod_hardening=True)   # these tests cover the hardened mode


def manifest(valid_body, settings=SETTINGS, **extra):
    return build_manifest(DeploymentCreate(**{**valid_body, **extra}), settings)


def pod(m):
    return m["spec"]["template"]["spec"]


# ---------------- manifest ----------------
def test_custom_paths_become_size_capped_emptydir_mounts(valid_body):
    m = manifest(valid_body, writable_paths=["/var/cache/nginx", "/var/run"])
    c = pod(m)["containers"][0]
    assert [x["mountPath"] for x in c["volumeMounts"]] == ["/tmp", "/var/cache/nginx", "/var/run"]
    vols = pod(m)["volumes"]
    assert [v["name"] for v in vols] == ["tmp", "scratch-0", "scratch-1"]                     # unique names
    assert all(v["emptyDir"] == {"sizeLimit": "64Mi"} for v in vols)                           # disk-backed, capped
    assert {x["name"] for x in c["volumeMounts"]} == {v["name"] for v in vols}


def test_lockdown_is_unchanged_by_the_new_options(valid_body):
    c = pod(manifest(valid_body, writable_paths=["/data"], run_as_user=999))["containers"][0]
    sc = c["securityContext"]
    assert sc["readOnlyRootFilesystem"] is True and sc["runAsNonRoot"] is True
    assert sc["allowPrivilegeEscalation"] is False and sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"} and sc["runAsUser"] == 999
    assert pod(manifest(valid_body))["automountServiceAccountToken"] is False


def test_default_user_is_unchanged_when_run_as_user_is_omitted(valid_body):
    assert pod(manifest(valid_body))["containers"][0]["securityContext"]["runAsUser"] == 10001


def test_tmp_requested_explicitly_is_not_mounted_twice(valid_body):
    c = pod(manifest(valid_body, writable_paths=["/tmp", "/data"]))["containers"][0]
    assert [x["mountPath"] for x in c["volumeMounts"]] == ["/tmp", "/data"]


def test_tmp_can_be_requested_when_the_default_tmp_is_off(valid_body):
    off = Settings(jwt_secret="x" * 32, pod_hardening=True, pod_writable_tmp=False)
    assert "volumes" not in pod(manifest(valid_body, off))
    c = pod(manifest(valid_body, off, writable_paths=["/tmp"]))["containers"][0]
    assert [x["mountPath"] for x in c["volumeMounts"]] == ["/tmp"]


# ---------------- validation (all must be 400 through the API) ----------------
BAD_PATHS = [["relative/path"], [""], ["/"], ["/data/"], ["/a//b"], ["/a/../etc"], ["/a/./b"], ["/.."], ["/da ta"],
             ["/data;rm"], ["/dat\na"], ["/\u540d\u524d"], ["/proc"], ["/proc/1"], ["/sys"], ["/sys/fs"], ["/dev"], ["/dev/shm"],
             ["/data", "/data"], [f"/p{i}" for i in range(9)], ["/" + "a" * 200], "/data", [1], [None], {"a": "/data"}]


@pytest.mark.parametrize("paths", BAD_PATHS, ids=lambda p: repr(p)[:30])
def test_bad_writable_paths_are_400(client, auth, valid_body, kube, paths):
    r = client.post("/api/deployments", json={**valid_body, "writable_paths": paths}, headers=auth())
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_input", paths
    assert kube.calls == []


@pytest.mark.parametrize("uid", [0, -1, 65536, "1000", 999.5, True, [1000]], ids=repr)
def test_bad_run_as_user_is_400_and_root_is_never_accepted(client, auth, valid_body, kube, uid):
    r = client.post("/api/deployments", json={**valid_body, "run_as_user": uid}, headers=auth())
    assert r.status_code == 400, uid
    assert kube.calls == []


@pytest.mark.parametrize("uid", [1, 101, 999, 1000, 10001, 65534, 65535])
def test_valid_users_are_accepted(client, auth, valid_body, uid):
    assert client.post("/api/deployments", json={**valid_body, "run_as_user": uid, "name": f"u{uid}"}, headers=auth()).status_code == 201


def test_realistic_stock_nginx_request_is_accepted(client, auth, valid_body):
    b = {**valid_body, "image": "nginx:latest", "port": 80, "writable_paths": ["/var/cache/nginx", "/var/run"]}
    assert client.post("/api/deployments", json=b, headers=auth()).status_code == 201


def test_paths_boundary_8_ok_9_rejected(client, auth, valid_body):
    ok = [f"/p{i}" for i in range(8)]
    assert client.post("/api/deployments", json={**valid_body, "writable_paths": ok}, headers=auth()).status_code == 201
    assert client.post("/api/deployments", json={**valid_body, "name": "n2", "writable_paths": ok + ["/p9"]}, headers=auth()).status_code == 400


def test_null_run_as_user_means_default_never_root(valid_body):
    m = manifest(valid_body, run_as_user=None)
    assert pod(m)["containers"][0]["securityContext"]["runAsUser"] == 10001

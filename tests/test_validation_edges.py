"""Edge cases for input validation. Every case must be a clean 400 (never 422, never 500) and must
never reach the cluster."""
import copy

import pytest

BAD_JSON = {"content-type": "application/json"}


def post(client, auth, body):
    return client.post("/api/deployments", json=body, headers=auth())


def raw_post(client, auth, raw, ctype="application/json"):
    return client.post("/api/deployments", content=raw, headers={**auth(), "content-type": ctype})


def assert_clean_400(r, kube):
    assert r.status_code == 400, f"{r.status_code}: {r.text[:200]}"
    assert r.json()["error"]["code"] == "invalid_input"
    assert kube.calls == []            # rejected before touching the cluster


def mutated(valid_body, **changes):
    b = copy.deepcopy(valid_body)
    b.update(changes)
    return b


# ---------------- images ----------------
BAD_IMAGES = [
    "Docker.IO/library/nginx:1", "GHCR.io/org/app:1", "ghcr.io/Org/app:1", "library/NGINX:1",  # uppercase host / repo
    "a" * 256, "docker.io/" + "a" * 250 + ":1",                                                 # > 255 chars
    "", " ", "nginx:1\n", "nginx :1", "nginx:bad tag", "nginx::1", "nginx:", ":1", "/nginx",
    "http://nginx", "nginx;rm -rf /", "nginx$(id)", "nginx@sha256:short", "ngïnx:1", "名前:1",
]


@pytest.mark.parametrize("image", BAD_IMAGES, ids=lambda i: repr(i)[:40])
def test_bad_image_strings_are_400(client, auth, valid_body, kube, image):
    assert_clean_400(post(client, auth, mutated(valid_body, image=image)), kube)


def test_image_length_boundary(client, auth, valid_body):
    assert post(client, auth, mutated(valid_body, image="a" * 255)).status_code == 201     # exactly 255 ok
    assert post(client, auth, mutated(valid_body, name="web2", image="a" * 256)).status_code == 400


# ---------------- names and namespaces ----------------
BAD_NAMES = ["", "web-", "-web", "my.app", "web name", "web_x", "WEB", "nämé", "名前", "web\n", "web\t", "a" * 64, "a/b", "a..b"]


@pytest.mark.parametrize("field", ["name", "namespace"])
@pytest.mark.parametrize("value", BAD_NAMES, ids=lambda v: repr(v)[:30])
def test_invalid_name_or_namespace_is_400(client, auth, valid_body, kube, field, value):
    assert_clean_400(post(client, auth, mutated(valid_body, **{field: value})), kube)


@pytest.mark.parametrize("name", ["a", "a" * 63, "1abc", "a-b", "a1-b2-c3"])
def test_valid_dns_label_boundaries_are_accepted(client, auth, valid_body, name):
    assert post(client, auth, mutated(valid_body, name=name)).status_code == 201


# ---------------- wrong types ----------------
@pytest.mark.parametrize("value", ["abc", 1.5, "3", 2.0, True, False, None, [1], {"a": 1}], ids=repr)
def test_replicas_wrong_type_is_400(client, auth, valid_body, kube, value):
    assert_clean_400(post(client, auth, mutated(valid_body, replicas=value)), kube)


@pytest.mark.parametrize("value", ["80", "abc", 80.5, 80.0, True, None, 0, -1, 65536, [80]], ids=repr)
def test_port_wrong_type_or_range_is_400(client, auth, valid_body, kube, value):
    assert_clean_400(post(client, auth, mutated(valid_body, port=value)), kube)


@pytest.mark.parametrize("field,value", [
    ("resources", "big"), ("resources", []), ("labels", ["a"]), ("labels", "a=b"), ("env", {"A": "1"}),
    ("env", "A=1"), ("env", [{"name": "A", "value": 1}]), ("env", [{"name": "A"}]), ("env", ["A"]),
    ("command", "run"), ("command", [1, 2]), ("args", "--x"), ("args", [None]),
    ("labels", {"k": 1}), ("labels", {"k": None}), ("image", 123), ("name", 5), ("namespace", None),
], ids=lambda x: repr(x)[:25])
def test_other_wrong_types_are_400(client, auth, valid_body, kube, field, value):
    assert_clean_400(post(client, auth, mutated(valid_body, **{field: value})), kube)


@pytest.mark.parametrize("cpu", [100, 0.5, None, True, ["100m"]], ids=repr)
def test_resource_quantities_must_be_strings(client, auth, valid_body, kube, cpu):
    b = copy.deepcopy(valid_body)
    b["resources"]["limits"]["cpu"] = cpu
    assert_clean_400(post(client, auth, b), kube)


# ---------------- malformed bodies: 400, not FastAPI's default 422 ----------------
@pytest.mark.parametrize("raw", [b'{"name": ', b"{", b"", b"null", b"[]", b'"just a string"', b"123", b"true",
                                 b"{'name': 'single-quotes'}", b'{"name": "a",}', b"\xff\xfe\x00", b"<xml/>"],
                         ids=lambda b: repr(b)[:25])
def test_malformed_json_body_is_400_not_422(client, auth, kube, raw):
    assert_clean_400(raw_post(client, auth, raw), kube)


def test_wrong_content_type_is_400(client, auth, valid_body, kube):
    import json
    assert_clean_400(raw_post(client, auth, json.dumps(valid_body).encode(), "text/plain"), kube)


def test_duplicate_keys_do_not_bypass_validation(client, auth, kube):
    # last key wins in JSON parsing; the winning value is still validated
    raw = b'{"name":"ok","name":"Bad_Name","namespace":"team-a","image":"nginx:1","port":80}'
    assert raw_post(client, auth, raw).status_code == 400


def test_errors_are_never_422_or_500_for_bad_input(client, auth, valid_body):
    for body in ({}, {"name": 1}, [], "x", {"replicas": "x"}):
        assert client.post("/api/deployments", json=body, headers=auth()).status_code == 400


# ---------------- PATCH ----------------
@pytest.mark.parametrize("body", [{"replicas": "abc"}, {"replicas": 1.5}, {"replicas": "3"}, {"replicas": 2.0},
                                  {"replicas": True}, {"replicas": [1]}, {"image": 5}, {"image": "BAD IMAGE"},
                                  {"image": "a" * 256}, {"image": "Docker.IO/x:1"}, {"replicas": None, "image": None},
                                  {}, [], "x", None], ids=lambda b: repr(b)[:30])
def test_patch_bad_bodies_are_400(client, auth, valid_body, body):
    uid = post(client, auth, valid_body).json()["id"]
    r = client.patch(f"/api/deployments/{uid}", json=body, headers=auth(role="operator"))
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("raw", [b"{", b"", b"\xff\xfe"], ids=repr)
def test_patch_malformed_json_is_400(client, auth, valid_body, raw):
    uid = post(client, auth, valid_body).json()["id"]
    r = client.patch(f"/api/deployments/{uid}", content=raw, headers={**auth(role="operator"), **BAD_JSON})
    assert r.status_code == 400


# ---------------- list ?namespace= ----------------
@pytest.mark.parametrize("ns", ["BAD!", "UPPER", "a.b", "a-", "-a", "a" * 64, "", "名前", "x%0Ay", "a b", "a%20b", "a/b", "../x"])
def test_list_invalid_namespace_param_is_400(client, auth, kube, ns):
    r = client.get(f"/api/deployments?namespace={ns}", headers=auth())
    assert r.status_code == 400, f"{ns!r}: {r.status_code}"
    assert r.json()["error"]["code"] == "invalid_input" and kube.calls == []


@pytest.mark.parametrize("ns", ["team-a", "a", "a" * 63])
def test_list_valid_namespace_param_is_ok(client, auth, ns):
    assert client.get(f"/api/deployments?namespace={ns}", headers=auth(namespaces=["*"])).status_code == 200


# ---------------- path id ----------------
@pytest.mark.parametrize("uid", ["ABCDEF12-3456-7890-ABCD-EF1234567890", "1234", "x" * 36, "1111-1111", "%20", "..%2F..",
                                 "11111111-1111-1111-1111-11111111111g"])
def test_malformed_ids_are_400_on_every_verb(client, auth, uid):
    for call in (lambda: client.get(f"/api/deployments/{uid}", headers=auth()),
                 lambda: client.patch(f"/api/deployments/{uid}", json={"replicas": 1}, headers=auth()),
                 lambda: client.delete(f"/api/deployments/{uid}", headers=auth()),
                 lambda: client.post(f"/api/deployments/{uid}/restart", headers=auth())):
        assert call().status_code in (400, 404), uid            # 404 = path did not route at all (e.g. "../")


# ---------------- framework-level errors use the documented error shape ----------------
def test_unknown_route_and_wrong_method_use_the_error_shape(client, auth):
    r = client.get("/api/nope", headers=auth())
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    r = client.put("/api/deployments", json={}, headers=auth())
    assert r.status_code == 405 and r.json()["error"]["code"] == "method_not_allowed"


@pytest.mark.parametrize("value", ["100m\n", "0.5\n", "1 "])
def test_trailing_whitespace_in_quantities_is_400(client, auth, valid_body, kube, value):
    b = copy.deepcopy(valid_body)
    b["resources"]["limits"]["cpu"] = value
    assert_clean_400(post(client, auth, b), kube)


@pytest.mark.parametrize("field,value", [("labels", {"app": "web\n"}), ("labels", {"app\n": "web"}),
                                         ("env", [{"name": "A\n", "value": "1"}])])
def test_trailing_newline_in_labels_and_env_is_400(client, auth, valid_body, kube, field, value):
    assert_clean_400(post(client, auth, mutated(valid_body, **{field: value})), kube)


# ---------------- helpful JSON syntax errors (no content echoed) ----------------
USER_BODY_TRAILING_COMMA = '''{
    "name": "echo", "namespace": "team-a",
    "image": "nginx:latest", "port": 80, "replicas": 2,
    "resources": {"requests": {"cpu": "50m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
  }'''


def test_json_syntax_error_reports_line_and_column_not_a_fake_field(client, auth, kube):
    r = raw_post(client, auth, USER_BODY_TRAILING_COMMA.encode())
    assert_clean_400(r, kube)
    d = r.json()["error"]["details"][0]
    assert d["field"] == "(body)"                                   # not "215"
    assert d["message"].startswith("Malformed JSON:") and "trailing comma" in d["message"] and "line 4" in d["message"]


def test_json_error_never_echoes_body_content(client, auth, kube):
    r = raw_post(client, auth, b'{"env": [{"name": "PASSWORD", "value": "hunter2"},]}')
    assert r.status_code == 400 and "hunter2" not in r.text and "PASSWORD" not in r.text


def test_after_fixing_the_comma_the_request_is_valid_because_labels_are_optional(client, auth, kube):
    fixed = USER_BODY_TRAILING_COMMA.replace("}},\n  }", "}}\n  }")
    r = raw_post(client, auth, fixed.encode())
    assert r.status_code == 201, r.text                      # no labels given, and that is fine


# ---------------- labels are optional ----------------
@pytest.mark.parametrize("how", ["omitted", "empty"])
def test_labels_are_optional(client, auth, valid_body, how):
    b = copy.deepcopy(valid_body)
    b.pop("labels") if how == "omitted" else b.update(labels={})
    r = post(client, auth, b)
    assert r.status_code == 201, r.text      # (the API's own labels are added by the manifest builder: see test_k8s_service.py)


def test_supplied_labels_are_still_validated_and_reserved_keys_still_blocked(client, auth, valid_body, kube):
    for bad in ({"bad key!": "v"}, {"app.kubernetes.io/managed-by": "x"}, {"k": "v" * 64}, {f"k{i}": "v" for i in range(21)}):
        assert_clean_400(post(client, auth, mutated(valid_body, labels=bad)), kube)

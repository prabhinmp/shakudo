"""The POST /api/deployments field contract, exactly as specified:

  mandatory: name, namespace, image, port, resources.requests.{cpu,memory}, resources.limits.{cpu,memory}
  optional : replicas (default 1), env, labels, command, args
"""
import copy

import pytest

MANDATORY = ["name", "namespace", "image", "port",
             "resources.requests.cpu", "resources.requests.memory",
             "resources.limits.cpu", "resources.limits.memory"]
OPTIONAL = ["replicas", "env", "labels", "command", "args", "writable_paths", "run_as_user"]


def drop(body, dotted):
    node = body
    *parents, leaf = dotted.split(".")
    for p in parents:
        node = node[p]
    del node[leaf]


@pytest.fixture
def full_body(valid_body):
    """A body that sets EVERY field, so each test can remove exactly one."""
    b = copy.deepcopy(valid_body)
    b.update(replicas=3, env=[{"name": "MODE", "value": "x"}], labels={"app": "web"}, command=["/bin/app"], args=["--v"],
             writable_paths=["/var/cache/nginx"], run_as_user=101)
    return b


def post(client, auth, body):
    return client.post("/api/deployments", json=body, headers=auth())


def test_the_full_body_is_accepted(client, auth, full_body):
    r = post(client, auth, full_body)
    assert r.status_code == 201 and r.json()["replicas"]["desired"] == 3


@pytest.mark.parametrize("field", MANDATORY)
def test_each_mandatory_field_is_required_and_named_in_the_error(client, auth, full_body, kube, field):
    drop(full_body, field)
    r = post(client, auth, full_body)
    assert r.status_code == 400, f"{field}: {r.status_code} {r.text}"
    reported = [d["field"] for d in r.json()["error"]["details"]]
    assert reported == [field], reported                      # exactly the missing field, nothing else
    assert kube.calls == []


@pytest.mark.parametrize("field", OPTIONAL)
def test_each_optional_field_may_be_omitted(client, auth, full_body, field):
    del full_body[field]
    r = post(client, auth, full_body)
    assert r.status_code == 201, f"{field}: {r.text}"


def test_all_optional_fields_omitted_together(client, auth, full_body):
    for f in OPTIONAL:
        del full_body[f]
    r = post(client, auth, full_body)
    assert r.status_code == 201 and r.json()["replicas"]["desired"] == 1      # replicas defaults to 1


def test_several_missing_mandatory_fields_are_all_reported_at_once(client, auth, full_body):
    for f in ("name", "image", "resources.limits.memory"):
        drop(full_body, f)
    r = post(client, auth, full_body)
    assert sorted(d["field"] for d in r.json()["error"]["details"]) == ["image", "name", "resources.limits.memory"]


def test_optional_fields_are_still_validated_when_present(client, auth, full_body):
    for field, bad in (("replicas", -1), ("env", [{"name": "1BAD", "value": "x"}]), ("labels", {"bad key!": "v"}),
                       ("command", "run"), ("args", [1])):
        b = copy.deepcopy(full_body)
        b[field] = bad
        assert post(client, auth, b).status_code == 400, field

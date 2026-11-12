"""Unit tests for the pure parts of the real Kubernetes service (no cluster needed)."""
import json

import pytest
from kubernetes.client.rest import ApiException

from app.config import Settings
from app.errors import ConflictError, KubernetesError, NotFoundError
from app.schemas import DeploymentCreate
from app.services.k8s import KubernetesService, _translate, build_manifest

SETTINGS = Settings(jwt_secret="x" * 32)


@pytest.fixture
def spec(valid_body):
    valid_body.update(env=[{"name": "A", "value": "1"}], command=["run"], args=["--x"])
    return DeploymentCreate(**valid_body)


def test_manifest_is_hardened(spec):
    m = build_manifest(spec, SETTINGS)
    pod = m["spec"]["template"]["spec"]
    c = pod["containers"][0]
    assert pod["automountServiceAccountToken"] is False
    assert c["securityContext"]["runAsNonRoot"] is True
    assert c["securityContext"]["allowPrivilegeEscalation"] is False
    assert c["securityContext"]["capabilities"] == {"drop": ["ALL"]}
    assert c["securityContext"]["readOnlyRootFilesystem"] is True
    assert c["resources"]["limits"] == {"cpu": "200m", "memory": "128Mi"}
    assert c["command"] == ["run"] and c["args"] == ["--x"] and c["env"] == [{"name": "A", "value": "1"}]
    assert "privileged" not in json.dumps(m)


def test_manifest_labels_and_selector(spec):
    m = build_manifest(spec, SETTINGS)
    sel = m["spec"]["selector"]["matchLabels"]
    assert sel == {"app.kubernetes.io/name": "web", "app.kubernetes.io/managed-by": "deploy-api"}
    assert m["metadata"]["labels"]["app"] == "web"
    assert m["spec"]["template"]["metadata"]["labels"].items() >= sel.items()


def test_error_translation():
    body = json.dumps({"message": "exceeded quota: compute"})
    assert isinstance(_translate(ApiException(status=404)), NotFoundError)
    assert isinstance(_translate(ApiException(status=409, reason="Conflict")), ConflictError)
    err = _translate(ApiException(status=403, reason="Forbidden"))
    assert isinstance(err, KubernetesError) and "403" in err.message
    e = ApiException(status=403, reason="Forbidden")
    e.body = body
    assert "exceeded quota" in _translate(e).message
    assert "unreachable" in _translate(OSError("conn refused")).message


def test_network_failure_becomes_500_not_traceback():
    class Down:
        def get_code(self, **kw):
            raise OSError("connection refused")
    svc = KubernetesService(None, None, Down(), SETTINGS)
    with pytest.raises(KubernetesError):
        svc.ping()


# ---------- protected namespaces: nothing may be changed there ----------
PROTECTED = ["kube-system", "kube-public", "kube-node-lease", "default"]


class _Explode:
    """Any call on this object fails the test: proves the Kubernetes client was never touched."""
    def __getattr__(self, name):
        raise AssertionError(f"Kubernetes client was called: {name}")


@pytest.mark.parametrize("ns", PROTECTED + ["kube-anything"])
def test_service_refuses_to_modify_protected_namespaces(ns, valid_body):
    from app.errors import ForbiddenError
    svc = KubernetesService(_Explode(), _Explode(), _Explode(), SETTINGS)
    valid_body["namespace"] = ns
    with pytest.raises(ForbiddenError):
        svc.create_deployment(DeploymentCreate(**valid_body))
    with pytest.raises(ForbiddenError):
        svc.ensure_namespace(ns)
    with pytest.raises(ForbiddenError):
        svc.patch_deployment(ns, "x", 2, None)
    with pytest.raises(ForbiddenError):
        svc.restart_deployment(ns, "x")
    with pytest.raises(ForbiddenError):
        svc.delete_deployment(ns, "x", "uid")


@pytest.mark.parametrize("ns", PROTECTED)
def test_config_cannot_remove_protection(ns, valid_body):
    from app.errors import ForbiddenError
    weak = Settings(jwt_secret="x" * 32, denied_namespaces=[])  # operator "disables" the deny-list
    svc = KubernetesService(_Explode(), _Explode(), _Explode(), weak)
    with pytest.raises(ForbiddenError):
        svc.ensure_namespace(ns)


def test_manifest_without_user_labels_still_has_selector_and_ownership_labels(valid_body):
    """labels are optional, so the API's own labels must carry the selector and managed-by ownership alone."""
    valid_body.pop("labels")
    m = build_manifest(DeploymentCreate(**valid_body), SETTINGS)
    own = {"app.kubernetes.io/name": "web", "app.kubernetes.io/managed-by": "deploy-api"}
    assert m["metadata"]["labels"] == own
    assert m["spec"]["template"]["metadata"]["labels"] == own
    assert m["spec"]["selector"]["matchLabels"] == own      # selector matches the pod template, so pods are adopted

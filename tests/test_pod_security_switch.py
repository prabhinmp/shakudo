"""Pod security is a switch: OFF by default (pods run as their image intends), ON enforces the lock-down."""
import json

import pytest

from app.config import Settings
from app.schemas import DeploymentCreate
from app.services.k8s import build_manifest

OFF = Settings(jwt_secret="x" * 32)                           # the default
ON = Settings(jwt_secret="x" * 32, pod_hardening=True)


def build(valid_body, settings, **extra):
    m = build_manifest(DeploymentCreate(**{**valid_body, **extra}), settings)
    pod = m["spec"]["template"]["spec"]
    return m, pod, pod["containers"][0]


def test_default_is_off():
    assert Settings(jwt_secret="x" * 32).pod_hardening is False


def test_off_pods_have_no_security_context_and_no_forced_mounts(valid_body):
    m, pod, c = build(valid_body, OFF)
    assert "securityContext" not in c                       # image defaults apply (root, writable fs, default caps)
    assert "volumes" not in pod and "volumeMounts" not in c
    text = json.dumps(m)
    for word in ("runAsNonRoot", "readOnlyRootFilesystem", "capabilities", "seccompProfile", "allowPrivilegeEscalation"):
        assert word not in text, word


def test_off_still_keeps_the_service_account_token_unmounted_and_resources(valid_body):
    _, pod, c = build(valid_body, OFF)
    assert pod["automountServiceAccountToken"] is False     # not a runtime restriction; keeps cluster credentials out of workloads
    assert c["resources"]["limits"] == {"cpu": "200m", "memory": "128Mi"}


def test_off_applies_an_explicit_run_as_user_only(valid_body):
    _, _, c = build(valid_body, OFF, run_as_user=999)
    assert c["securityContext"] == {"runAsUser": 999}


def test_off_still_honours_writable_paths(valid_body):
    _, pod, c = build(valid_body, OFF, writable_paths=["/data"])
    assert c["volumeMounts"] == [{"name": "scratch-0", "mountPath": "/data"}]
    assert pod["volumes"][0]["emptyDir"] == {"sizeLimit": "64Mi"}


def test_off_leaves_selector_labels_and_ports_untouched(valid_body):
    m, _, c = build(valid_body, OFF)
    assert c["ports"] == [{"containerPort": 80}]
    assert m["spec"]["selector"]["matchLabels"]["app.kubernetes.io/managed-by"] == "deploy-api"


def test_on_enforces_the_full_lockdown(valid_body):
    _, pod, c = build(valid_body, ON)
    sc = c["securityContext"]
    assert sc["runAsNonRoot"] is True and sc["runAsUser"] == 10001 and sc["readOnlyRootFilesystem"] is True
    assert sc["allowPrivilegeEscalation"] is False and sc["capabilities"] == {"drop": ["ALL"]}
    assert sc["seccompProfile"] == {"type": "RuntimeDefault"}
    assert c["volumeMounts"] == [{"name": "tmp", "mountPath": "/tmp"}]


@pytest.mark.parametrize("flag", ["true", "1", "TRUE"])
def test_switch_is_controlled_by_the_environment_variable(monkeypatch, flag):
    monkeypatch.setenv("DEPLOY_API_POD_HARDENING", flag)
    assert Settings(jwt_secret="x" * 32).pod_hardening is True

"""Application settings, loaded from environment variables (prefix DEPLOY_API_).

List values are given as JSON, e.g. DEPLOY_API_ALLOWED_REGISTRIES='["docker.io"]'.
"""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_BYTES = 32

# Hard-coded: config can ADD to the deny-list but can never remove these.
PROTECTED_NAMESPACES = frozenset({"kube-system", "kube-public", "kube-node-lease", "default"})


def is_protected_namespace(namespace: str, extra_denied=()) -> bool:
    return namespace in PROTECTED_NAMESPACES or namespace.startswith("kube-") or namespace in extra_denied


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEPLOY_API_", env_file=None)

    # --- Auth ---
    jwt_secret: str = Field(default="", description="HS256 signing secret (>= 32 bytes)")
    jwt_issuer: str = "deploy-api"
    jwt_audience: str = "deploy-api"

    # --- Policy ---
    # Exact names that are always off-limits. Anything starting with "kube-" is too.
    denied_namespaces: list[str] = ["kube-system", "kube-public", "kube-node-lease", "default"]
    allowed_registries: list[str] = ["docker.io", "ghcr.io"]

    # --- Limits ---
    max_replicas: int = 20
    max_body_bytes: int = 64 * 1024

    # --- Rate limits (per minute, per process) ---
    rate_limit_read_per_min: int = 120    # per token subject
    rate_limit_write_per_min: int = 30    # per token subject (POST/PATCH/DELETE)
    rate_limit_auth_fail_per_min: int = 10  # failed-auth attempts per client IP

    # --- Workload hardening applied to every pod we create ---
    # Master switch for the securityContext on pods this API creates. OFF by default (per the operator's
    # decision): pods run as the image intends (often root, writable filesystem, default capabilities).
    # Set DEPLOY_API_POD_HARDENING=true to enforce: non-root, read-only root fs, no capabilities, seccomp.
    pod_hardening: bool = False
    pod_run_as_user: int = 10001
    pod_read_only_root_fs: bool = True
    # A small writable emptyDir mounted at /tmp. The root filesystem stays read-only; many images
    # (e.g. nginxinc/nginx-unprivileged) only need scratch space under /tmp to run.
    pod_writable_tmp: bool = True
    pod_tmp_size_limit: str = "64Mi"

    # --- Kubernetes client ---
    kube_timeout_seconds: int = 10

    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()

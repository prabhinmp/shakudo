"""Request and response models.

Request models use extra="forbid": unknown fields are rejected (400), so a caller can
never smuggle in settings we did not intend to support (privileged, hostPath, ...).
"""
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, computed_field, field_validator, model_validator

MANAGED_BY_LABEL = "app.kubernetes.io/managed-by"
MANAGED_BY_VALUE = "deploy-api"
NAME_LABEL = "app.kubernetes.io/name"
RESERVED_LABELS = {MANAGED_BY_LABEL, NAME_LABEL}

DNS_LABEL_RE = r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$"  # RFC 1123 label
UUID_RE = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
IMAGE_RE = re.compile(
    r"^(?:[a-z0-9.-]+(?::\d+)?/)?(?:[a-z0-9._-]+/)*[a-z0-9._-]+(?::[\w][\w.-]{0,127})?(?:@sha256:[a-f0-9]{64})?$"
)
LABEL_KEY_RE = re.compile(r"^([a-z0-9]([-a-z0-9.]*[a-z0-9])?/)?[A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?$")
LABEL_VALUE_RE = re.compile(r"^([A-Za-z0-9]([-A-Za-z0-9_.]*[A-Za-z0-9])?)?$")
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CPU_RE = re.compile(r"^(\d+(?:\.\d+)?|\d+m)$")
MEM_RE = re.compile(r"^(\d+(?:\.\d+)?)(Ki|Mi|Gi|Ti|K|M|G|T)?$")

MAX_CPU_MILLI = 4000  # 4 cores per container
MAX_MEM_BYTES = 8 * 1024**3  # 8 GiB per container
_MEM_UNITS = {None: 1, "K": 1000, "M": 1000**2, "G": 1000**3, "T": 1000**4,
              "Ki": 1024, "Mi": 1024**2, "Gi": 1024**3, "Ti": 1024**4}


def cpu_to_milli(value: str) -> int:
    if value.endswith("m"):
        return int(value[:-1])
    return int(float(value) * 1000)


def mem_to_bytes(value: str) -> int:
    m = MEM_RE.fullmatch(value)
    return int(float(m.group(1)) * _MEM_UNITS[m.group(2)])


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Quantities(Strict):
    cpu: str
    memory: str

    @field_validator("cpu")
    @classmethod
    def _cpu(cls, v: str) -> str:
        if not CPU_RE.fullmatch(v) or cpu_to_milli(v) <= 0:
            raise ValueError("cpu must be a positive quantity like '250m' or '0.5'")
        if cpu_to_milli(v) > MAX_CPU_MILLI:
            raise ValueError(f"cpu may not exceed {MAX_CPU_MILLI}m")
        return v

    @field_validator("memory")
    @classmethod
    def _mem(cls, v: str) -> str:
        if not MEM_RE.fullmatch(v) or mem_to_bytes(v) <= 0:
            raise ValueError("memory must be a positive quantity like '128Mi' or '1Gi'")
        if mem_to_bytes(v) > MAX_MEM_BYTES:
            raise ValueError("memory may not exceed 8Gi")
        return v


class Resources(Strict):
    requests: Quantities
    limits: Quantities

    @model_validator(mode="after")
    def _requests_not_above_limits(self):
        if cpu_to_milli(self.requests.cpu) > cpu_to_milli(self.limits.cpu):
            raise ValueError("requests.cpu must not exceed limits.cpu")
        if mem_to_bytes(self.requests.memory) > mem_to_bytes(self.limits.memory):
            raise ValueError("requests.memory must not exceed limits.memory")
        return self


class EnvVar(Strict):
    name: str = Field(max_length=253)
    value: str = Field(max_length=4096)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        if not ENV_NAME_RE.fullmatch(v):
            raise ValueError("env name must match [A-Za-z_][A-Za-z0-9_]*")
        return v


def _check_image(v: str) -> str:
    if len(v) > 255 or not IMAGE_RE.fullmatch(v):
        raise ValueError("image is not a valid container image reference")
    return v


class DeploymentCreate(Strict):
    name: str = Field(min_length=1, max_length=63, pattern=DNS_LABEL_RE)
    namespace: str = Field(min_length=1, max_length=63, pattern=DNS_LABEL_RE)
    image: str
    replicas: StrictInt = Field(default=1, ge=0)   # strict: "3", 2.0 and true are rejected, not coerced
    port: StrictInt = Field(ge=1, le=65535)
    resources: Resources
    env: list[EnvVar] = Field(default_factory=list, max_length=50)
    # optional: the API always adds app.kubernetes.io/name and managed-by itself (selector + ownership)
    labels: dict[str, str] = Field(default_factory=dict, max_length=20)
    command: list[str] | None = Field(default=None, max_length=20)
    args: list[str] | None = Field(default=None, max_length=50)

    _image = field_validator("image")(_check_image)

    @field_validator("env")
    @classmethod
    def _unique_env(cls, v: list[EnvVar]) -> list[EnvVar]:
        names = [e.name for e in v]
        if len(names) != len(set(names)):
            raise ValueError("env names must be unique")
        return v

    @field_validator("labels")
    @classmethod
    def _labels(cls, v: dict[str, str]) -> dict[str, str]:
        for key, val in v.items():
            if len(key) > 253 or not LABEL_KEY_RE.fullmatch(key) or len(key.split("/")[-1]) > 63:
                raise ValueError(f"invalid label key '{key}'")
            if len(val) > 63 or not LABEL_VALUE_RE.fullmatch(val):
                raise ValueError(f"invalid label value for '{key}'")
            if key in RESERVED_LABELS:
                raise ValueError(f"label '{key}' is reserved")
        return v


class DeploymentPatch(Strict):
    replicas: StrictInt | None = Field(default=None, ge=0)
    image: str | None = None

    @field_validator("image")
    @classmethod
    def _image(cls, v):
        return v if v is None else _check_image(v)

    @model_validator(mode="after")
    def _something_to_change(self):
        if self.replicas is None and self.image is None:
            raise ValueError("provide at least one of: replicas, image")
        return self


# ---------------- responses ----------------
class Replicas(BaseModel):
    desired: int
    ready: int
    available: int
    unavailable: int


Health = Literal["healthy", "degraded", "down", "idle"]


def replica_health(r: Replicas) -> Health:
    """Health from replica counts alone (all a list call knows).
    idle = scaled to zero on purpose; down = wanted but nothing ready; degraded = some missing."""
    if r.desired == 0:
        return "idle"
    if r.ready == 0:
        return "down"
    if r.ready < r.desired or r.unavailable > 0:
        return "degraded"
    return "healthy"


class DeploymentSummary(BaseModel):
    id: str
    name: str
    namespace: str
    image: str
    labels: dict[str, str]
    replicas: Replicas
    created_at: datetime | None = None

    @computed_field
    @property
    def health(self) -> Health:
        return replica_health(self.replicas)


class ContainerStatus(BaseModel):
    name: str
    ready: bool
    restart_count: int
    state: str  # e.g. "running", "waiting: ImagePullBackOff", "terminated: Error"


class PodStatus(BaseModel):
    name: str
    phase: str
    restart_count: int
    container_statuses: list[ContainerStatus]


class Condition(BaseModel):
    type: str
    status: str
    reason: str | None = None
    message: str | None = None
    last_transition_time: datetime | None = None


# Container states that mean "this pod will not start by itself".
BROKEN_STATES = ("CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull", "CreateContainerConfigError",
                 "InvalidImageName", "terminated: Error", "terminated: OOMKilled")


class DeploymentDetail(DeploymentSummary):
    pods: list[PodStatus]
    conditions: list[Condition]

    @computed_field
    @property
    def health(self) -> Health:
        """Replica health, made stricter by what the pods say: a pod stuck in CrashLoopBackOff
        means the deployment is not healthy even if counts look briefly fine."""
        base = replica_health(self.replicas)
        broken = any(b in c.state for p in self.pods for c in p.container_statuses for b in BROKEN_STATES)
        if broken and base == "healthy":
            return "degraded"
        return base


class NamespaceList(BaseModel):
    namespaces: list[str]

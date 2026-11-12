# Technology Stack: Backend (Deployment Manager API)

Versions are the ones **actually installed and used** when this document was written (2026-11-12).
`requirements.txt` is not pinned (see [Known gaps](#known-gaps)), so a fresh build may pick up newer releases.

## At a glance

| Layer | Technology | Version | Why it is used |
|---|---|---|---|
| Language | Python | 3.14.7 | One language for API, Kubernetes client and tests |
| Web framework | FastAPI | 0.162.5 | Typed request handling, dependency injection (auth/role checks), fast to test |
| HTTP toolkit | Starlette | 1.10.0 | FastAPI's foundation: middleware, routing, `TestClient` |
| ASGI server | Uvicorn (+ uvloop, httptools) | 0.54.0 | Production-grade ASGI server, runs one process per pod |
| Validation | Pydantic | 2.14.3 | Strict request/response schemas (DNS names, quantities, labels, image format) |
| Configuration | pydantic-settings | 2.16.2 | Typed config from `DEPLOY_API_*` environment variables |
| Kubernetes client | kubernetes (official Python client) | 37.0.2 | Talks to the cluster API; in-cluster ServiceAccount config or kubeconfig |
| Authentication | PyJWT (+ cryptography) | 2.15.1 (50.0.2) | Verifies HS256 bearer tokens |
| HTTP client (tests) | httpx | 0.28.1 | Backs FastAPI's `TestClient` |
| Test runner | pytest | 9.2.0 | Unit, API and real-cluster integration tests |
| Dependency audit | pip-audit | latest at run time | Known-vulnerability scan in CI |

## Runtime and packaging

| Concern | Technology | Detail |
|---|---|---|
| Container image | Docker, multi-stage build | Base `python:3.14-slim`; dependencies built in a venv, runtime stage has no build tools |
| Image hardening | non-root user | uid/gid 10001, no home dir, `nologin` shell; Kubernetes also enforces read-only root FS, no privilege escalation, all capabilities dropped, seccomp `RuntimeDefault` |
| Process model | single Uvicorn process | listens on `8080`; scaling is done with pod replicas (2 by default), not worker processes |
| Handlers | synchronous `def` routes | FastAPI runs them in a thread pool, which suits the blocking Kubernetes client |
| State | **none** | stateless; Kubernetes is the single source of truth; **no database, no cache** (reads are always live) |

## Kubernetes and delivery

| Concern | Technology | Version used here |
|---|---|---|
| Packaging / deploy | Helm chart `deploy/helm/deploy-api` | Helm 3.14.4 |
| Cluster (local) | minikube, Docker driver, containerd runtime | minikube 1.39.0, Kubernetes **1.37.0** |
| CLI | kubectl | 1.37.2 |
| Ingress | ingress-nginx (minikube `ingress` addon) | controller v1.15.1 |
| Network policy enforcement | kindnet CNI (minikube default) | enforcement confirmed: it blocked API-server traffic until the right port was allowed |
| Cluster access control | RBAC: ServiceAccount + ClusterRole + ClusterRoleBinding | least privilege (no secrets, no node or RBAC access) |
| Availability | PodDisruptionBudget, rolling updates (`maxUnavailable: 0`), liveness/readiness probes, topology spread | |
| Secrets | Kubernetes Secret for the JWT signing key | generated once by the chart and kept across upgrades, or supplied via `jwt.existingSecret` |
| Container tooling | Docker Engine | 29.9.3 |

## Security-related components

| Area | How it is implemented |
|---|---|
| Client → API auth | JWT bearer, HS256 pinned, `exp/iss/aud/sub` required, role + namespace-allowlist claims (PyJWT) |
| API → Kubernetes auth | pod **ServiceAccount token** (`load_incluster_config()`); kubeconfig only for local development |
| Authorization | role hierarchy viewer < operator < admin; namespace allowlist; hard-coded protected namespaces |
| Input hardening | Pydantic strict types (`StrictInt`), anchored regexes (`fullmatch`), unknown fields rejected, registry allowlist, size limits |
| Abuse control | in-memory sliding-window rate limiter (own implementation, `app/core/ratelimit.py`) |
| Response hardening | `Cache-Control: no-store`, `X-Content-Type-Options`, `X-Frame-Options`, Content-Security-Policy; OpenAPI/Swagger disabled |
| Audit | structured JSON lines on the `audit` logger (stdout) |
| Supply chain | `pip-audit` in the local CI pipeline (no known vulnerabilities at last run) |

## Testing and CI tooling

| Tool | Role |
|---|---|
| pytest | 304 unit/API tests (in-memory fake cluster and mocked clients) + 7 integration tests |
| Integration tests | `tests/integration/`: real app and real service against a real cluster, cross-checked with an independent raw Kubernetes client (no mocks) |
| `scripts/api-test.sh` | 67 curl assertions against a running API on the real cluster |
| `scripts/ci.sh` | local CI: tests → pip-audit → helm lint → docker build → image checks → optional deploy/commit; `--watch` for continuous runs |
| `scripts/mint-token.sh` | bash + openssl JWT generator for manual testing |

## Dependency list

Direct runtime dependencies (`requirements.txt`): `fastapi`, `uvicorn[standard]`, `pydantic-settings`, `kubernetes`, `pyjwt[crypto]`.
Dev (`requirements-dev.txt`): the above plus `pytest`, `httpx`, `pip-audit`.
Notable transitive packages in the environment: Starlette 1.10.0, Pydantic 2.14.3, anyio 4.16.0, urllib3 2.8.0, h11 0.16.0, PyYAML 6.0.3,
websockets 17.4, watchfiles 1.3.0, python-dotenv 1.2.4.

## Code size

About 1,200 lines of application code in `app/` and about 1,300 lines of tests in `tests/` (counted with `wc -l`).

## Known gaps

- **Dependencies are not pinned.** `requirements.txt` lists names without versions, so image builds are not reproducible and a new upstream
  release can change behaviour. Recommended: generate a pinned lock file (`pip freeze` / `pip-compile`) and build the image from it.
- **Python 3.14 is very new.** Some libraries may lag behind it. Everything here ran on 3.14.7, but a conservative production choice would be 3.12 or 3.13.
- **HS256 shared secret.** Simple, but no key rotation or revocation; OIDC/JWKS (RS256) is the production direction.
- **Rate limits are per pod and in memory.** Exact global limits would need a shared store such as Redis.
- **Local-only cluster tested.** Everything was verified on minikube; managed clusters (EKS, GKE) differ in API-server port, CNI and image architecture
  (the local image is arm64 only).

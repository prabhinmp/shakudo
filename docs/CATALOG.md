# Project Catalog and Progress Sheet: Deployment Manager API

Python (FastAPI) service that creates and manages Kubernetes Deployments through a secured REST API.
Legend: ✅ built and verified · 🟡 built, partly verified · ⬜ not started · ⏸ blocked on a decision

_Last updated: 2026-11-12. Tests: **412 unit + 7 real-cluster integration (no mocks) + 76 curl assertions + 39 UI browser tests (Playwright)**. Verified live on minikube through the ingress (API on port 18080, UI on 8088 via `deploy-ui.localtest.me`). Integration and curl suites need a running cluster and were not re-run after the read-only change._

## 1. Progress at a glance

| # | Step | Status |
|---|---|---|
| 0 | Environment check (Docker, minikube, Helm, kubectl) | ✅ |
| 1 | Scaffold, config, health endpoints | ✅ |
| 2 | Security core (JWT, roles, namespace policy) | ✅ |
| 3 | Schemas and validation (all POST field rules) | ✅ |
| 4 | Kubernetes service layer | ✅ |
| 5 | All six endpoints + error contract | ✅ |
| 6 | Rate limiting + audit of denied attempts | ✅ |
| 6b | Protected namespaces (kube-system, kube-public, kube-node-lease, default) | ✅ |
| 6c | Live reads (no cache, `no-store`) + POST pre-flight against the cluster | ✅ |
| 7 | Dockerfile (multi-stage, non-root) built and checked | ✅ |
| 8 | Helm chart (RBAC, NetworkPolicy, probes, PDB, ingress) | ✅ |
| 9 | Deploy to minikube + end-to-end via ingress | ✅ |
| 10 | Local CI script (`--watch`, tests gate the build) | ✅ |
| 11 | README, curl test guide, this catalog | ✅ |
| 12 | **Push image to a registry** | ⏸ waiting for your choice of registry (see §7) |
| 13 | Git: 6 local commits + auto-commit on green builds (`ci.sh --commit`) | 🟡 committed locally; **push to GitHub blocked, see §7** |
| 13b | No-mock proof: production code/image audited; real-cluster integration suite | ✅ |
| 13c | Strict input validation (strict ints, anchored regexes, uniform error shape) + edge-case tests | ✅ |
| 13d | `docs/API_TESTING.md` with per-endpoint commands and real expected responses; `scripts/api-test.sh`; `docs/TECH_STACK.md` | ✅ |
| 13e | Cluster-wide read, write-only-what-we-own: all namespaces/deployments listed, `managed`/`read_only` flags, 403 on changing read-only items, ClusterRole gains `namespaces: list` | ✅ unit tested; deployed on minikube |
| 13f | Web UI (React + Vite): dashboard, colour-coded health, namespace filter, detail (replicas, pods, conditions), scale/restart/delete with confirmation, 5 s polling, read-only handling | ✅ 39 Playwright tests; deployed on minikube |
| 13g | UI image (nginx-unprivileged) + Helm chart `deploy/helm/deploy-ui` (proxy `/api`, Ingress, NetworkPolicy, PDB) | ✅ `helm lint` + `nginx -t` + deployed |
| 14 | Production hardening (TLS, shared rate limits, OIDC, multi-arch, image scan) | ⬜ see §7 |

## 2. Features

| # | Feature | Endpoint | Min. role | Status |
|---|---|---|---|---|
| F1 | Create deployment (namespace auto-created); pre-flight check against live cluster | `POST /api/deployments` | admin | ✅ unit + live |
| F2 | List, optional `?namespace=`, always live | `GET /api/deployments` | viewer | ✅ unit + live |
| F3 | Detail: replica counts, per-pod status, conditions, always live | `GET /api/deployments/{id}` | viewer | ✅ unit + live |
| F4 | Scale replicas / change image (rolling update) | `PATCH /api/deployments/{id}` | operator | ✅ unit + live |
| F5 | Rollout restart (annotation patch) | `POST /api/deployments/{id}/restart` | operator | ✅ unit + live |
| F6 | Delete with foreground cascade (ReplicaSets, Pods) | `DELETE /api/deployments/{id}` | admin | ✅ unit + live |
| F7 | Liveness / readiness | `GET /healthz`, `/readyz` | none | ✅ unit + live |

`{id}` is the Deployment UID. POST fields: **mandatory** `name`, `namespace`, `image`, `port`, and `resources` (`requests` and `limits`, each with `cpu` and `memory`); **optional** `replicas` (default 1), `env`, `labels`, `command`, `args`, `writable_paths`, `run_as_user`; unknown fields are rejected. Enforced by `tests/test_field_spec.py`. See `app/schemas.py`.

### Live-state guarantees
- **GET is never cached.** No cache layer exists in the app; every list/detail reads the cluster at request time
  (`locate` re-lists, `get_detail` re-reads deployment and pods). Every response carries `Cache-Control: no-store`.
  A tripwire test fails the build if a cache is introduced.
- **POST pre-flight** (read-only, runs before anything is created): rejects with 409 if the namespace is being deleted or if
  **any** deployment with that name exists there, including ones not created by this API. A rejected request creates nothing,
  not even a namespace. The cluster's own 409 on create remains as the backstop for races.

## 3. Error contract
Body: `{"error": {"code": "...", "message": "...", "details": [...]}}`

| Status | When |
|---|---|
| 400 | invalid input: bad image, negative replicas, bad quantities, DNS-incompatible names, unknown fields, malformed id, disallowed registry |
| 401 | missing / invalid / expired token |
| 403 | role too low, or namespace not permitted |
| 404 | id unknown **or not visible to the caller** (no existence leak) |
| 409 | name exists in namespace (managed or not), or namespace terminating |
| 413 | body larger than `max_body_bytes` |
| 429 | rate limited, or IP cooling down after repeated failed auth (`Retry-After`) |
| 500 | Kubernetes failure with the cluster's message; unexpected bugs return a generic message (details only in logs) |
| 503 | `/readyz` only: Kubernetes unreachable |

## 4. Components

| Component | File | Responsibility |
|---|---|---|
| App factory | `app/main.py` | wiring, startup secret check, body-size limit, `no-store` headers |
| Settings | `app/config.py` | env config (`DEPLOY_API_*`), hard-coded protected namespaces |
| Errors | `app/errors.py` | error classes → HTTP codes; denied-request audit |
| Routes | `app/api/deployments.py`, `health.py` | HTTP only, no Kubernetes logic |
| Security | `app/core/security.py` | JWT verify, roles, namespace authorization, rate-limit hooks |
| Policy | `app/core/policy.py` | registry allowlist, replica cap |
| Rate limiter | `app/core/ratelimit.py` | in-memory sliding window, bounded memory |
| Audit | `app/core/audit.py` | JSON line per mutation |
| Schemas | `app/schemas.py` | request validation, response models |
| K8s service | `app/services/k8s.py` | only code that calls Kubernetes; pre-flight; hardened manifest builder |
| Image | `Dockerfile` | multi-stage, uid 10001, no shell login, fail-fast without secret |
| Chart | `deploy/helm/deploy-api/` | Deployment ×2, Service, Ingress, ServiceAccount, ClusterRole, NetworkPolicy, PDB, Secret |
| Local CI | `scripts/ci.sh` | tests → pip-audit → helm lint → docker build → image checks → deploy; `--watch` |
| Token helper | `scripts/mint-token.sh` | bash + openssl JWT for testing |

Request flow: `rate limit → token → role → namespace → input policy → cluster pre-flight → Kubernetes → audit`.

## 5. Security controls

| Control | Status |
|---|---|
| Client → API: JWT HS256 pinned; `exp/iss/aud/sub` required; `alg=none` rejected; uniform 401 | ✅ tested |
| API → Kubernetes: pod ServiceAccount token (in-cluster config), least-privilege ClusterRole | ✅ live (`kubectl auth can-i` checked) |
| ClusterRole allows only: namespaces get/list/create · deployments get/list/create/patch/delete · pods list. **Denied:** secrets, delete namespaces/pods, RBAC, nodes | ✅ live (`list namespaces` checked with `kubectl auth can-i`) |
| Refuses to start with signing secret < 32 bytes | ✅ tested |
| Roles viewer < operator < admin; per-token namespace allowlist with wildcards | ✅ tested |
| Protected namespaces hard-coded **write-protected** (readable, never changed); enforced in routes **and** service layer; config cannot remove them | ✅ tested |
| Reads are cluster-wide, writes only on objects labelled `managed-by=deploy-api` outside protected namespaces (`read_only` flag, 403 otherwise) | ✅ unit tested (`tests/test_readonly_view.py`); real-cluster assertions updated but not re-run |
| Cross-tenant ids return 404, not 403 | ✅ tested + live |
| Strict validation; errors never echo values; Swagger/OpenAPI disabled | ✅ tested |
| Created pods: securityContext is a **switch, OFF by default** (operator decision): off = pods run as their image intends; on (`DEPLOY_API_POD_HARDENING=true`) = non-root, no privilege escalation, drop ALL caps, read-only rootfs + small `/tmp`, seccomp; no SA token either way | ✅ both modes tested and verified on the cluster |
| Rate limits (reads 120/min, writes 30/min per subject; failed-auth lockout 10/min per IP) | ✅ live (240 of 320 allowed with 2 pods, see §6) |
| Audit of mutations and denied requests; tokens/bodies never logged | ✅ live |
| Container: non-root uid 10001, read-only rootfs + dropped caps, `pip-audit` clean | ✅ checked |
| NetworkPolicy (ingress from same ns + ingress-nginx; egress DNS + API server) | ✅ enforced on minikube (it blocked the API server until port 8443 was allowed) |
| TLS on the ingress | ⬜ local ingress is plain HTTP |

## 6. Verification record
- **Namespace scope (user question: why 403 on `testuser-api`):** the token's `namespaces` claim, not namespace creation, was the cause. A `["demo-*"]` token is refused (and creates nothing); a `["*"]` token creates the missing namespace itself (labelled `managed-by=deploy-api`); the four built-in namespaces are refused for every token. The 403 now says which reason applies. `mint-token.sh` defaults to `["*"]`. Tests: `tests/test_namespace_scope.py`; curl checks in `scripts/api-test.sh` section 3b.
- **Validation hardening (found by edge-case tests):** `replicas`/`port` accepted `"3"`, `2.0` and `true` (coerced); trailing newline passed the image/label/env/quantity regexes (`.match` + `$`); framework errors (unparseable body, unknown route, wrong method) used FastAPI's `{"detail"}` shape. Fixed with `StrictInt`, `fullmatch`, and a handler that uses the standard error shape. Each fix was mutation-checked.
- **Known behaviour:** after `DELETE` returns 204 the deployment remains visible until its pods finish terminating (about 4-12 s here, longer on a loaded node); the API does not yet expose a "terminating" state.
- **No mock data in production:** `app/` contains no mock/fake/stub/sample data (only two comments mention that tests substitute a fake); nothing in `app/` imports from `tests/`; the Docker image contains only `/srv/app` (no tests, no fakes); every API value comes from a real Kubernetes client call. The unit tests do use an in-memory fake and `MagicMock` (test-only), which is why a separate **no-mock integration suite** exists: `tests/integration/` runs the real app and real service against the real cluster and cross-checks results with an independent raw Kubernetes client (create, duplicate, live reads, patch, restart, cascade delete, foreign deployment, protected namespaces). Run: `pytest -m integration` or `./scripts/ci.sh --integration`.
- **132 tests:** security, API contract, validation, manifest hardening, error translation, protected namespaces, rate limiting, audit, live-state/pre-flight. Mutation-checked: deliberately removing the pre-flight call, the `no-store` header, or adding a cache each made the new tests fail.
- **Live on minikube via ingress (`deploy-api.localtest.me:18080`):** every endpoint and every status code in the contract; foreign (kubectl-made) deployment rejected with 409 and left untouched; `kubectl scale` visible on the very next GET; `kubectl delete` gives immediate 404; `no-store` header present; cascade delete leaves no ReplicaSets/Pods; protected namespaces unchanged (state diffed); rate limit and audit lines observed.
- **`docs/API_TESTING.md`:** every `bash` block was extracted and executed against the live ingress; the documented codes matched.
- **Local CI:** full pipeline passes (~110 s); a deliberately failing test stopped the pipeline before any image was built, and removing it let the watcher pass again.
- **Bugs found and fixed along the way:** NetworkPolicy blocked the API-server port (8443 on minikube); readiness ping could stall for ~40 s on retries (now 2 s, no retries); CI script `pipefail`/`grep -q` false failure; `pip-audit -q` flag; two weak test assertions (`or True`, `and/or` precedence); the curl guide's rate-limit step undercounted because limits are per pod.

## 7. What is left

### Blocked on you
0. ~~Push to GitHub~~ done: `origin/main` (`github.com/prabhinmp/shakudo`) is in sync with local `main`.
1. **Push the image to a registry.** Your Docker config holds credentials for both **Docker Hub** and **AWS ECR (801935342396.dkr.ecr.ap-south-1.amazonaws.com)**. Pushing publishes the image, so tell me which one and the repository name (e.g. `docker.io/<you>/deploy-api`, or the ECR repo). The CI already supports it: `REGISTRY=<repo> ./scripts/ci.sh --push`. If the target is an EKS cluster, I should also build **multi-arch (amd64+arm64)**: the current image is arm64-only (built on Apple silicon).

### Should do next
3. **Real client IP behind the ingress:** the failed-auth lockout and audit logs currently see the ingress pod's IP (`10.244.0.x`), so one abusive client can lock out everyone for a minute. Fix: trust `X-Forwarded-For` only from the ingress.
4. **Rate limits are per pod:** with 2 replicas the effective limit doubles (observed). Exact global limits need Redis.
5. **TLS** on the ingress (cert-manager, or a self-signed cert for local).
6. **Pod security trade-off:** with the default (off) any public image runs (stock `nginx`, `httpd`, `postgres` verified) but workloads are less isolated. Turning it on is one setting; with it on, images that write to disk use `writable_paths` / `run_as_user` (root and `..`, `/proc`, `/sys`, `/dev` paths are rejected). Verified ON: nginx, nginx-unprivileged, httpd, caddy, redis, prometheus, http-echo, whoami, podinfo, jmalloc/ealen/mendhak echo servers. Not possible ON: `postgres` (needs root). History: an earlier note wrongly said `nginx-unprivileged` worked; that test had added a `/tmp` tmpfs the pods did not have.
7. **Pin NetworkPolicy egress to the API server CIDR** (`networkPolicy.apiServerCidr`) instead of port-only.

### Production hardening (later)
8. OIDC/JWKS in place of the shared HS256 secret; key rotation; token revocation.
9. Image: pin base by digest, add a container scan (e.g. Trivy), SBOM.
10. Load/concurrency test; list pagination; the UID lookup is O(n) over managed deployments.
11. Metrics (Prometheus), request IDs, HPA for the API itself.
12. Remote CI (GitHub Actions) mirroring `scripts/ci.sh`; `helm test` hook.
13. Namespaces the API creates are never deleted automatically (document or add cleanup policy).

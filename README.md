# Deployment Manager API

A Python (FastAPI) service that creates and manages Kubernetes **Deployments** through a secured REST API.
It runs inside the cluster and talks to the Kubernetes API with its own ServiceAccount. It can **read** every
namespace and deployment (so a dashboard shows the whole cluster) but only **changes** deployments it created itself;
built-in namespaces (`default`, `kube-*`) and deployments made by anyone else are shown read-only. A React **web UI**
(dashboard, detail view, scale/restart/delete) sits on top of the API.

| | |
|---|---|
| Endpoints | `POST/GET /api/deployments`, `GET/PATCH/DELETE /api/deployments/{id}`, `POST /api/deployments/{id}/restart`, `GET /api/namespaces`, `/healthz`, `/readyz` |
| Web UI | React + Vite app in `ui/`, served by unprivileged nginx (own image and Helm chart `deploy/helm/deploy-ui`); see [Web UI](#web-ui) |
| Data | Every GET reads the live cluster (no cache, `Cache-Control: no-store`); POST pre-checks the cluster and rejects an existing name (even if not created by the API) before creating anything |
| Auth | Client → API: JWT bearer (HS256). API → Kubernetes: pod ServiceAccount token + RBAC |
| Packaging | Two Docker images (API, UI; both non-root) + two Helm charts |
| More docs | [Feature/progress catalog](docs/CATALOG.md) · [curl test guide](docs/API_TESTING.md) · [Technology stack](docs/TECH_STACK.md) · [Authentication: who gets a token](docs/AUTHENTICATION.md) |

## Architecture

```
Browser ─► Ingress ─► [ nginx UI pod ×2 ] ── /api/* ──┐   (static React build at /ui/)
                                                      ▼
Client ──► Ingress ──► [ FastAPI pod ×2 ]
                         1. rate limit + JWT check          (app/core/security.py, ratelimit.py)
                         2. role check (viewer/operator/admin)
                         3. namespace policy (allowlist + protected namespaces)
                         4. input validation + image-registry policy   (app/schemas.py, core/policy.py)
                         5. Kubernetes call via ServiceAccount          (app/services/k8s.py)
                         6. audit log
                                   ▼
                          Kubernetes API  (RBAC: deployments, pods(list), namespaces(get/list/create) only)
```

Layers are separate so each can be tested alone: routes (`app/api`) contain no Kubernetes code, and the
Kubernetes service (`app/services/k8s.py`) is the only module that calls the cluster.

## Requirements
Python 3.14 (3.12+ should work), Docker, kubectl, Helm, minikube (for the local cluster).

## Quick start (local minikube)

```sh
# 1. dev environment
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt

# 2. run the full local pipeline: tests -> audit -> helm lint -> docker build -> image checks -> deploy
minikube start && minikube addons enable ingress
./scripts/ci.sh --deploy

# 3. expose the ingress on your laptop (leave running)
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 18080:80
```

`ci.sh --deploy` installs the chart using `deploy/helm/deploy-api/values-minikube.yaml` (ingress on
`deploy-api.localtest.me`, NetworkPolicy open to the ingress controller), so once the port-forward is running:

```sh
curl http://deploy-api.localtest.me:18080/healthz      # {"status":"ok"}
```

Then follow **[docs/API_TESTING.md](docs/API_TESTING.md)** to exercise every endpoint with curl.

## Web UI

`ui/` is a React 18 + Vite single-page app (no CORS: the browser only ever talks to the UI origin, whose nginx proxies `/api/*` to the API).

| Feature | Detail |
|---|---|
| Dashboard | Every deployment across **all** namespaces, colour-coded health (green healthy, yellow degraded, red down, grey idle), summary tiles, name search |
| Namespace filter | Lists every cluster namespace (including empty and built-in ones) that the token may see |
| Detail view | Replica counts, per-pod table (phase, restarts, container states), deployment conditions |
| Actions | Scale (validated 0..max replicas), restart, delete (type the exact name to confirm). Buttons follow the token's role |
| Read-only items | Deployments in `default`/`kube-*` or not created by this API are tagged **read-only**; scale, restart and delete are disabled for every role (the API refuses them too: 403) |
| Auto-refresh | Polls every 5 s; pauses while the tab is hidden; a 401 returns to sign-in; a 429 backs off for `Retry-After` |
| Sign-in | Paste a JWT (kept in `sessionStorage`); see [Token claims](#security-model-what-protects-each-call) / `scripts/mint-token.sh` |

```sh
# build and deploy the UI next to the API (minikube)
docker build -t deploy-ui:dev ui && minikube image load deploy-ui:dev
helm upgrade --install deploy-ui deploy/helm/deploy-ui -n deploy-api \
  -f deploy/helm/deploy-ui/values-minikube.yaml --set image.pullPolicy=Never
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8088:80      # leave running
# open http://deploy-ui.localtest.me:8088/ui/   (localtest.me resolves to 127.0.0.1, no /etc/hosts edit)
```

The UI chart's nginx proxies `/api/` to `backend.host` (default `deploy-api-deploy-api`, the API chart's Service) and applies the same CSP and
security headers as the API. If the API chart's NetworkPolicy is enabled, UI pods in the same namespace are allowed by default; from another
namespace add them to the API's `networkPolicy.ingressFrom`. The API image also serves a built UI at `/ui` when `app/static` exists, but the
separate UI chart is the supported route.

```sh
cd ui && npm ci && npx playwright test     # 39 browser tests against a mocked API (no cluster needed)
cd ui && npm run dev                       # Vite dev server on :5173, /api proxied to API_URL (default http://localhost:8080)
```

## Run the tests

```sh
.venv/bin/python -m pytest -q          # 412 tests, uses an in-memory fake cluster, no Kubernetes needed
./scripts/ci.sh                        # full pipeline once
./scripts/ci.sh --watch                # continuous: re-runs on every file change; tests gate the build
./scripts/ci.sh --integration          # also run the NO-MOCK suite against the real cluster in your kube context
./scripts/ci.sh --watch --commit       # periodic commits: one commit after every GREEN build, never on red
.venv/bin/python -m pytest -m integration   # real-cluster tests only (creates/deletes its own demo-it-* namespace)
```

CI stops at the first failing stage, so an image is only built after all tests pass.
`REGISTRY=<repo> ./scripts/ci.sh --push` additionally tags and pushes, only after everything passed.

## Authentication & authorization

**There are no user accounts or login.** The platform operator (whoever can read the signing secret with `kubectl`) mints one signed token per person with `scripts/mint-token.sh`, choosing the role and namespaces, and hands over only that token. The API verifies the signature on each request. Full explanation, per-person examples and limits: **[docs/AUTHENTICATION.md](docs/AUTHENTICATION.md)**.

**Token claims:** `sub`, `iss`, `aud`, `exp`, `role` (`viewer`|`operator`|`admin`), `namespaces` (list, `*` suffix wildcards ok).

| Role | Allowed |
|---|---|
| viewer | GET list, GET detail |
| operator | + PATCH, restart |
| admin | + POST create, DELETE |

Create a test token: `SECRET=... ./scripts/mint-token.sh admin '["demo-*"]'` (restricted to `demo-*`), or `./scripts/mint-token.sh admin` (any namespace except the protected built-in ones; missing namespaces are created automatically). The secret lives in the Secret
`<release>-deploy-api-jwt` (generated by the chart, kept across upgrades), or supply your own with `jwt.existingSecret`.

## Error codes

| Code | Meaning |
|---|---|
| 400 | invalid input (bad image, negative replicas, bad quantity, DNS-incompatible name, unknown field, malformed id, disallowed registry) |
| 401 | missing/invalid/expired token |
| 403 | role too low, or namespace not permitted |
| 404 | deployment not found, **or not visible to the caller** |
| 409 | name already exists in that namespace (checked against the live cluster, including deployments not created by this API), or namespace is being deleted |
| 413 | request body too large |
| 429 | rate limited (`Retry-After` header) |
| 500 | Kubernetes API failure, with the cluster's message; unexpected bugs return a generic message |

Body shape: `{"error": {"code": "...", "message": "...", "details": [...]}}`

## Security model (what protects each call)

1. **Transport:** terminate TLS at the ingress (not yet configured locally, see Limitations).
2. **AuthN:** JWT, algorithm pinned to HS256, `exp/iss/aud/sub` required, `alg=none` rejected, uniform error message; the app refuses to start with a signing secret under 32 bytes.
3. **AuthZ:** role per endpoint, plus a per-token namespace allowlist (`namespaces` claim) that limits what a caller can **see**. Cross-tenant ids return 404, not 403.
4. **Protected namespaces:** `kube-system`, `kube-public`, `kube-node-lease`, `default` and any `kube-*` are hard-coded **write-protected**: they can be listed and viewed (read-only) but never created into, scaled, restarted or deleted, by any token. Enforced in the routes **and** in the Kubernetes service layer; config can add to the list but never remove from it.
5. **Scope: read everything, write only what we own.** Lists, detail and `/api/namespaces` cover the whole cluster (subject to the token). Each item carries `managed` (labelled `app.kubernetes.io/managed-by=deploy-api`) and `read_only` (protected namespace, or not created by this API). PATCH, restart and DELETE on a `read_only` item return 403 `... is read-only`.
6. **Input:** strict schemas (unknown fields rejected), DNS-1123 names, resource caps, registry allowlist, replica cap, 64 KiB body limit. Validation errors never echo submitted values.
7. **Workloads it creates:** no ServiceAccount token is mounted into them. A pod `securityContext` (non-root, read-only root filesystem, no capabilities, seccomp, no privilege escalation) is available behind `DEPLOY_API_POD_HARDENING=true` and is **off by default**.
8. **Abuse control:** per-subject read/write rate limits; failed-auth lockout per IP.
9. **Audit:** JSON log for every mutation and every denied request (401/403/429, cross-namespace probes). Never logs tokens or bodies.
10. **Cluster access:** least-privilege ClusterRole (no secrets, no node access, no RBAC changes, no pod/namespace delete), NetworkPolicy limiting ingress and egress, non-root read-only container, PodDisruptionBudget.

## Configuration

Environment variables (`DEPLOY_API_` prefix); in Helm they come from `values.yaml`. Key ones:
`JWT_SECRET` (required), `ALLOWED_REGISTRIES`, `DENIED_NAMESPACES`, `MAX_REPLICAS`,
`RATE_LIMIT_READ_PER_MIN` (120), `RATE_LIMIT_WRITE_PER_MIN` (30), `RATE_LIMIT_AUTH_FAIL_PER_MIN` (10),
`POD_HARDENING` (false: pods run as their image intends; true enforces the lock-down), `POD_READ_ONLY_ROOT_FS` (true, only with hardening on).

## Project layout

```
app/            main.py (factory) · config.py · errors.py · schemas.py
  api/          deployments.py · health.py          (HTTP only)
  core/         security.py · policy.py · ratelimit.py · audit.py
  services/     k8s.py                              (only code that calls Kubernetes)
tests/          412 tests + in-memory fake cluster (+ tests/integration: 7 real-cluster tests)
ui/             React + Vite web UI: src/ · e2e/ (39 Playwright tests) · Dockerfile
deploy/helm/deploy-api/   API chart (+ values-minikube.yaml)
deploy/helm/deploy-ui/    UI chart: nginx, ConfigMap, Ingress, NetworkPolicy, PDB (+ values-minikube.yaml)
scripts/        ci.sh (local CI, --watch) · mint-token.sh
docs/           CATALOG.md (progress) · API_TESTING.md (curl guide)
Dockerfile
```

## Known limitations (read before production)

- **Rate limits and the IP lockout are per pod and in-memory.** With 2 replicas the effective limit is double (verified: 240 of 320 requests allowed against a 120 limit). Behind the ingress the lockout sees the ingress pod's IP, so one abusive client can lock out all clients for a minute. Fix: shared store (Redis) and trusting `X-Forwarded-For` only from the ingress.
- **Cluster-wide RBAC.** Creating namespaces and deployments anywhere requires cluster-scoped permissions, and RBAC cannot deny namespaces. The app's protected-namespace guard is the real barrier.
- **Cluster-wide read access.** To show every namespace and deployment, the API reads all deployments and pods and lists namespaces cluster-wide. Anyone with a `["*"]` token can see names, images and replica state of everything (not secrets or configmaps). Scope tokens' `namespaces` claim to limit it.
- **UI in front of the API.** The UI keeps the JWT in `sessionStorage`; it is only as safe as the tokens you hand out. nginx adds the CSP and security headers but does not authenticate anything itself.
- **Pod security on workloads is a switch, OFF by default.** Pods the API creates run as their image intends (often root, writable filesystem, default capabilities), so any public image runs, including stock `nginx` and `postgres` (verified on the cluster). That is a deliberate operator decision and a weaker posture for the workloads. To enforce the lock-down (non-root uid 10001, read-only root filesystem, no capabilities, seccomp, no privilege escalation) set `DEPLOY_API_POD_HARDENING=true` (chart: `config.podHardening: true`). With it on, images that write to disk need the optional request fields `writable_paths` and `run_as_user`; see [docs/API_TESTING.md](docs/API_TESTING.md#public-images-and-pod-security).
- HS256 shared secret, no revocation: use short expiries; move to OIDC/JWKS for production.
- ID lookup lists all deployments in the cluster (O(n)); no pagination yet.
- No TLS on the local ingress; the images are not pushed to a registry; the local images are arm64 only.

# Testing the Deployment Manager API with curl

Run every step **in the same terminal**, in order: later steps reuse variables from earlier ones.
Blocks marked `bash` are runnable; blocks marked `sh` are for reading only.

**Fastest path:** `./scripts/api-test.sh` runs every step below against the real cluster with PASS/FAIL assertions
(76 checks; `--ratelimit` adds the slow rate-limit step; `BASE=... SECRET=...` points it at another server).
The rest of this page is the same flow step by step, so you can run and inspect each command yourself.

Expected status codes are shown as `# → 201`. If a step gives something else, see Troubleshooting at the end.

## 0. Prerequisites (one-time)

- minikube running, the `ingress` addon enabled, and the chart installed with the minikube values:

```sh
minikube addons enable ingress
helm upgrade --install deploy-api deploy/helm/deploy-api -n deploy-api --create-namespace \
  -f deploy/helm/deploy-api/values-minikube.yaml        # add: --set image.tag=<tag> if not "dev"
kubectl -n deploy-api get pods                          # both pods should be 1/1 Running
```

- Open the ingress on your laptop. Leave this running in **a separate terminal**:

```sh
kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 18080:80
```

The API is then at `http://deploy-api.localtest.me:18080`. (`localtest.me` is public DNS that resolves to
127.0.0.1, so no `/etc/hosts` edit is needed. Offline? Use `curl --resolve deploy-api.localtest.me:18080:127.0.0.1 ...`.)

## 1. Set up variables and tokens

Callers authenticate with a JWT (`Authorization: Bearer <token>`). Tokens are signed with the secret stored in the
cluster; [scripts/mint-token.sh](../scripts/mint-token.sh) creates them with plain bash + openssl.

```bash
export BASE=http://deploy-api.localtest.me:18080
export SECRET=$(kubectl -n deploy-api get secret deploy-api-deploy-api-jwt -o jsonpath='{.data.jwt-secret}' | base64 -d)

# role + which namespaces the token may touch ("demo-*" = any namespace starting with demo-)
# each role gets its own identity (JWT_SUB): rate limits are counted per identity, and rejected requests count too
export ADMIN=$(JWT_SUB=admin-user ./scripts/mint-token.sh admin '["demo-*"]')
export OPERATOR=$(JWT_SUB=operator-user ./scripts/mint-token.sh operator '["demo-*"]')
export VIEWER=$(JWT_SUB=viewer-user ./scripts/mint-token.sh viewer '["demo-*"]')
export OTHER=$(JWT_SUB=mallory ./scripts/mint-token.sh admin '["team-z"]')   # admin, but a different namespace
export GOD=$(JWT_SUB=god ./scripts/mint-token.sh admin '["*"]')              # admin for everything (still blocked from protected namespaces)
export ADMIN_ANY=$(JWT_SUB=admin-any ./scripts/mint-token.sh admin)         # no 2nd argument = ["*"]: any namespace, created if missing

# small helper: prints the body, then the HTTP status
api() { curl -s -w "\n[HTTP %{http_code}]\n" "$@"; }
echo "tokens ready"
```

**Which namespaces a token may use is part of the token** (its `namespaces` claim). The `ADMIN`, `OPERATOR` and `VIEWER` tokens above
only allow names starting with `demo-`; `ADMIN_ANY` allows every namespace except the protected built-in ones
(`kube-system`, `kube-public`, `kube-node-lease`, `default`, `kube-*`). If a create or list returns
`403 ... is not in this token's allowed namespaces`, mint a token whose claim includes that namespace (or `*`).

| Role | May call |
|---|---|
| viewer | GET list, GET detail |
| operator | + PATCH, restart |
| admin | + POST create, DELETE |

## 1b. API reference: command and expected result for every endpoint

Run section 1 first (it defines `$BASE`, `$ADMIN`, `$OPERATOR`, `$VIEWER`, `$OTHER`, `$GOD`, `api`). `$ID` is the `id` returned by create. The bodies below are **real responses captured from the live system**; values such as ids, timestamps and pod names differ on every run.

| # | Endpoint | Min. role | Success | Failure codes |
|---|---|---|---|---|
| 1 | `GET /healthz` | none | 200 | – |
| 2 | `GET /readyz` | none | 200 | 503 if Kubernetes is unreachable |
| 3 | `POST /api/deployments` | admin | 201 | 400, 401, 403, 409, 429, 500 |
| 4 | `GET /api/deployments[?namespace=]` | viewer | 200 | 400, 401, 403, 429, 500 |
| 5 | `GET /api/deployments/{id}` | viewer | 200 | 400, 401, 404, 429, 500 |
| 6 | `PATCH /api/deployments/{id}` | operator | 200 | 400, 401, 403, 404, 429, 500 |
| 7 | `POST /api/deployments/{id}/restart` | operator | 200 | 400, 401, 403, 404, 429, 500 |
| 8 | `DELETE /api/deployments/{id}` | admin | 204 | 400, 401, 403, 404, 429, 500 |
| 9 | `GET /api/namespaces` | viewer | 200 | 401, 429 (needs image from commit `c132076`+) |

### 1. `GET /healthz`: liveness (no token)
```sh
curl -s $BASE/healthz
```
**Expected: `200 OK`**

```json
{
  "status": "ok"
}
```

### 2. `GET /readyz`: readiness, checks the Kubernetes API (no token)
```sh
curl -s $BASE/readyz
```
**Expected: `200 OK`**

```json
{
  "status": "ready"
}
```

If the cluster cannot be reached: `503 {"status":"kubernetes unreachable"}`.

### 3. `POST /api/deployments`: create (admin)
Required: `name`, `namespace` (auto-created), `image`, `port`, `resources` (requests and limits). Optional: `replicas` (default 1), `labels` (the API always adds `app.kubernetes.io/name` and `managed-by`), `env`, `command`, `args`.

```sh
curl -s -X POST $BASE/api/deployments \
  -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" \
  -d '{
    "name": "echo", "namespace": "demo-api",
    "image": "hashicorp/http-echo:latest", "port": 5678, "replicas": 2,
    "args": ["-listen=:5678", "-text=hello"],
    "env": [{"name": "MODE", "value": "demo"}],
    "resources": {"requests": {"cpu": "50m", "memory": "32Mi"}, "limits": {"cpu": "100m", "memory": "64Mi"}},
    "labels": {"app": "echo", "team": "demo"}
  }'
```
**Expected: `201 Created`**

```json
{
  "id": "046ad721-62b2-4ee8-9924-9ec7f1bc8fd1",
  "name": "echo",
  "namespace": "demo-api",
  "image": "hashicorp/http-echo:latest",
  "labels": {
    "app": "echo",
    "app.kubernetes.io/managed-by": "deploy-api",
    "app.kubernetes.io/name": "echo",
    "team": "demo"
  },
  "replicas": {
    "desired": 2,
    "ready": 0,
    "available": 0,
    "unavailable": 0
  },
  "created_at": "2026-11-12T15:29:55Z",
  "health": "down"
}
```

`ready` is 0 because the response is the state at creation time; poll the detail endpoint for readiness.

**Same command again** (name already exists in the live cluster, even if it was not created by this API):

**Expected: `409 Conflict`**

```json
{
  "error": {
    "code": "conflict",
    "message": "Deployment 'echo' already exists in namespace 'demo-api'"
  }
}
```

### 4. `GET /api/deployments`: list, always live (viewer)
```sh
curl -s $BASE/api/deployments -H "Authorization: Bearer $VIEWER"                         # everything this token may see
curl -s "$BASE/api/deployments?namespace=demo-api" -H "Authorization: Bearer $VIEWER"    # one namespace
```
**Expected: `200 OK`**

```json
[
  {
    "id": "046ad721-62b2-4ee8-9924-9ec7f1bc8fd1",
    "name": "echo",
    "namespace": "demo-api",
    "image": "hashicorp/http-echo:latest",
    "labels": {
      "app": "echo",
      "app.kubernetes.io/managed-by": "deploy-api",
      "app.kubernetes.io/name": "echo",
      "team": "demo"
    },
    "replicas": {
      "desired": 2,
      "ready": 2,
      "available": 2,
      "unavailable": 0
    },
    "created_at": "2026-11-12T15:29:55Z",
    "health": "healthy"
  }
]
```

### 5. `GET /api/deployments/{id}`: detail with pods and conditions (viewer)
```sh
curl -s $BASE/api/deployments/$ID -H "Authorization: Bearer $VIEWER"
```
**Expected: `200 OK`**

```json
{
  "id": "046ad721-62b2-4ee8-9924-9ec7f1bc8fd1",
  "name": "echo",
  "namespace": "demo-api",
  "image": "hashicorp/http-echo:latest",
  "labels": {
    "app": "echo",
    "app.kubernetes.io/managed-by": "deploy-api",
    "app.kubernetes.io/name": "echo",
    "team": "demo"
  },
  "replicas": {
    "desired": 2,
    "ready": 2,
    "available": 2,
    "unavailable": 0
  },
  "created_at": "2026-11-12T15:29:55Z",
  "pods": [
    {
      "name": "echo-7888786cfb-hmj8l",
      "phase": "Running",
      "restart_count": 0,
      "container_statuses": [
        {
          "name": "echo",
          "ready": true,
          "restart_count": 0,
          "state": "running"
        }
      ]
    },
    {
      "name": "echo-7888786cfb-npjcq",
      "phase": "Running",
      "restart_count": 0,
      "container_statuses": [
        {
          "name": "echo",
          "ready": true,
          "restart_count": 0,
          "state": "running"
        }
      ]
    }
  ],
  "conditions": [
    {
      "type": "Available",
      "status": "True",
      "reason": "MinimumReplicasAvailable",
      "message": "Deployment has minimum availability.",
      "last_transition_time": "2026-11-12T15:29:59Z"
    },
    {
      "type": "Progressing",
      "status": "True",
      "reason": "NewReplicaSetAvailable",
      "message": "ReplicaSet \"echo-7888786cfb\" has successfully progressed.",
      "last_transition_time": "2026-11-12T15:29:55Z"
    }
  ],
  "health": "healthy"
}
```

`health` is `healthy`, `degraded`, `down` or `idle` (scaled to zero). Response headers include `Cache-Control: no-store`.

### 6. `PATCH /api/deployments/{id}`: scale and/or change image (operator)
Only `replicas` and `image` are accepted; at least one is required.

```sh
curl -s -X PATCH $BASE/api/deployments/$ID \
  -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas": 1}'
```
**Expected: `200 OK`**

```json
{
  "id": "046ad721-62b2-4ee8-9924-9ec7f1bc8fd1",
  "name": "echo",
  "namespace": "demo-api",
  "image": "hashicorp/http-echo:latest",
  "labels": {
    "app": "echo",
    "app.kubernetes.io/managed-by": "deploy-api",
    "app.kubernetes.io/name": "echo",
    "team": "demo"
  },
  "replicas": {
    "desired": 1,
    "ready": 2,
    "available": 2,
    "unavailable": 0
  },
  "created_at": "2026-11-12T15:29:55Z",
  "health": "healthy"
}
```

### 7. `POST /api/deployments/{id}/restart`: rollout restart (operator)
Sets the pod-template annotation `kubectl.kubernetes.io/restartedAt`, so Kubernetes replaces the pods.

```sh
curl -s -X POST $BASE/api/deployments/$ID/restart -H "Authorization: Bearer $OPERATOR"
kubectl -n demo-api get deploy echo -o jsonpath='{.spec.template.metadata.annotations}'   # shows restartedAt
```
**Expected: `200 OK`**

```json
{
  "id": "046ad721-62b2-4ee8-9924-9ec7f1bc8fd1",
  "name": "echo",
  "namespace": "demo-api",
  "image": "hashicorp/http-echo:latest",
  "labels": {
    "app": "echo",
    "app.kubernetes.io/managed-by": "deploy-api",
    "app.kubernetes.io/name": "echo",
    "team": "demo"
  },
  "replicas": {
    "desired": 1,
    "ready": 2,
    "available": 2,
    "unavailable": 0
  },
  "created_at": "2026-11-12T15:29:55Z",
  "health": "healthy"
}
```

### 8. `DELETE /api/deployments/{id}`: delete with cascade (admin)
```sh
curl -s -o /dev/null -w '%{http_code}\n' -X DELETE $BASE/api/deployments/$ID -H "Authorization: Bearer $ADMIN"
```
**Expected: `204 No Content`** with an empty body.

Foreground cascade: the Deployment stays visible until its pods have terminated (about 10 s here, up to ~30 s+ on a busy node). After that:

**Expected: `404 Not Found`**

```json
{
  "error": {
    "code": "not_found",
    "message": "Deployment '046ad721-62b2-4ee8-9924-9ec7f1bc8fd1' not found"
  }
}
```

### 9. `GET /api/namespaces`: namespaces with managed deployments that this token may see (viewer)
```sh
curl -s $BASE/api/namespaces -H "Authorization: Bearer $VIEWER"
```
**Expected: `200 OK`**

```json
{
  "namespaces": [
    "demo-api",
    "demo-chat"
  ]
}
```

### Error responses (real examples)
Every error has the shape `{"error": {"code", "message", "details"?}}`.

**400 invalid input** (here: DNS-incompatible name **and** `port` sent as a string; all problems are listed):
```sh
curl -s -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d '{"name":"Bad_Name","namespace":"demo-api","image":"nginx:1","port":"80","resources":{"requests":{"cpu":"50m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}},"labels":{"a":"b"}}'
```
**Expected: `400 Bad Request`**

```json
{
  "error": {
    "code": "invalid_input",
    "message": "Request validation failed",
    "details": [
      {
        "field": "name",
        "message": "String should match pattern '^[a-z0-9]([-a-z0-9]*[a-z0-9])?$'"
      },
      {
        "field": "port",
        "message": "Input should be a valid integer"
      }
    ]
  }
}
```

**400 malformed JSON** (`-d '{"name": '`):

**Expected: `400 Bad Request`**

```json
{
  "error": {
    "code": "invalid_input",
    "message": "Request validation failed",
    "details": [
      {
        "field": "9",
        "message": "JSON decode error"
      }
    ]
  }
}
```

**401 missing/invalid token** (`curl -s $BASE/api/deployments`), response also has `WWW-Authenticate: Bearer`:

**Expected: `401 Unauthorized`**

```json
{
  "error": {
    "code": "unauthorized",
    "message": "Missing bearer token"
  }
}
```

**403 role too low** (viewer token on POST):

**Expected: `403 Forbidden`**

```json
{
  "error": {
    "code": "forbidden",
    "message": "Role 'viewer' may not perform this action"
  }
}
```

**403 namespace not in the token's list** (the message names the token's allowed namespaces and the fix):

```json
{
  "error": {
    "code": "forbidden",
    "message": "Namespace 'testuser-api' is not in this token's allowed namespaces (demo-*); use a token whose 'namespaces' claim includes it, or '*' for all non-protected namespaces"
  }
}
```

**403 protected namespace** (`kube-system`, `kube-public`, `kube-node-lease`, `default`, `kube-*`: refused for every token, even `*`):

```json
{
  "error": {
    "code": "forbidden",
    "message": "Namespace 'kube-system' is protected (built-in Kubernetes namespace) and cannot be used"
  }
}
```

**404 unknown id**:

**Expected: `404 Not Found`**

```json
{
  "error": {
    "code": "not_found",
    "message": "Deployment '11111111-1111-1111-1111-111111111111' not found"
  }
}
```

**404 for another team's deployment** (existence is deliberately not revealed; identical to unknown id):

**Expected: `404 Not Found`**

```json
{
  "error": {
    "code": "not_found",
    "message": "Deployment '046ad721-62b2-4ee8-9924-9ec7f1bc8fd1' not found"
  }
}
```

**409** duplicate name: see section 3 above. **429** too many requests: body `{"error":{"code":"rate_limited","message":"Too many requests"}}` plus a `Retry-After: <seconds>` header (section 11). **500** Kubernetes failure: `{"error":{"code":"kubernetes_error","message":"Kubernetes API error (403): exceeded quota: ..."}}` carrying the cluster's own message.

## 2. Health (no token needed)

```bash
api $BASE/healthz        # → 200 {"status":"ok"}
api $BASE/readyz         # → 200 {"status":"ready"}  (503 if the API cannot reach Kubernetes)
```

## 3. Authentication failures

```bash
api $BASE/api/deployments                                            # → 401 missing token
api -H "Authorization: Bearer not-a-real-token" $BASE/api/deployments   # → 401 invalid token
```

## 4. Create a deployment (POST, admin)

Required: `name`, `namespace` (created if missing), `image`, `port`, `resources` (requests + limits). Optional: `labels`.
Optional: `replicas` (default 1), `env`, `command`, `args`.

```bash
export CREATE_BODY='{
  "name": "echo",
  "namespace": "demo-api",
  "image": "hashicorp/http-echo:latest",
  "port": 5678,
  "args": ["-listen=:5678", "-text=hello from the API"],
  "env": [{"name": "MODE", "value": "demo"}],
  "resources": {
    "requests": {"cpu": "50m",  "memory": "32Mi"},
    "limits":   {"cpu": "100m", "memory": "64Mi"}
  },
  "labels": {"app": "echo", "team": "demo"}
}'

RESP=$(curl -s -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d "$CREATE_BODY")
echo "$RESP"                                                                   # → 201 JSON with "id"
export ID=$(echo "$RESP" | python3 -c 'import sys,json; print(json.load(sys.stdin)["id"])')
echo "deployment id: $ID"
```

Duplicate name in the same namespace:

```bash
api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d "$CREATE_BODY"   # → 409
```

### Pre-flight check against the live cluster
Before anything is created, the API asks the cluster whether the name is already taken, **including deployments it
did not create** (made with `kubectl`, Helm, etc.). A rejected request creates nothing, not even a namespace.

```bash
kubectl -n demo-api create deployment foreign --image=hashicorp/http-echo:latest     # made outside the API
FOREIGN_BODY=$(echo "$CREATE_BODY" | sed 's/"name": "echo"/"name": "foreign"/')
api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d "$FOREIGN_BODY"   # → 409
kubectl -n demo-api get deploy foreign -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'                          # untouched
api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=demo-api"       # → 200, lists only "echo": the API never exposes what it does not manage
kubectl -n demo-api delete deployment foreign
```

### Any namespace: created automatically when it does not exist
The API creates a missing namespace itself (labelled `managed-by=deploy-api`), as long as the token allows it and it is not a built-in one.

```bash
ANYNS=scope-demo
echo "-- token limited to demo-* is refused, and NO namespace is created"
api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d "$(echo "$CREATE_BODY" | sed "s/demo-api/$ANYNS/")"     # → 403 "not in this token's allowed namespaces"
kubectl get namespace $ANYNS 2>&1 | tail -1                                                                                                                # → NotFound
echo "-- token with '*': namespace is created together with the deployment"
api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN_ANY" -H "Content-Type: application/json" -d "$(echo "$CREATE_BODY" | sed "s/demo-api/$ANYNS/")"  # → 201
kubectl get namespace $ANYNS --show-labels                                                                                                                  # → Active, app.kubernetes.io/managed-by=deploy-api
```

## 5. Invalid input → 400

Each of these must return **400** with `"code":"invalid_input"`.

```bash
# Writes are limited to 30/min per identity and rejected requests count, so rotate the identity every 10 calls.
N=0
post() { N=$((N+1)); local T; T=$(JWT_SUB=validation-$((N/10)) ./scripts/mint-token.sh admin '["demo-*"]')
         api -X POST $BASE/api/deployments -H "Authorization: Bearer $T" -H "Content-Type: application/json" -d "$1"; }
GOOD_RES='"resources":{"requests":{"cpu":"50m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}}'

echo "-- DNS-incompatible name";   post '{"name":"Bad_Name","namespace":"demo-api","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- negative replicas";       post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"replicas":-1,'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- bad image format";        post '{"name":"x","namespace":"demo-api","image":"NOT AN IMAGE","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- bad cpu value";           post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"resources":{"requests":{"cpu":"lots","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}},"labels":{"a":"b"}}'
echo "-- request above limit";     post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"resources":{"requests":{"cpu":"500m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}},"labels":{"a":"b"}}'
echo "-- missing required fields"; post '{"name":"x"}'
echo "-- unknown field (privileged)"; post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"},"privileged":true}'
echo "-- registry not allowed";    post '{"name":"x","namespace":"demo-api","image":"evil.example.com/miner:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- replicas above cap";      post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"replicas":999,'"$GOOD_RES"',"labels":{"a":"b"}}'

echo "-- wrong types are rejected, never coerced (replicas as text / decimal / boolean)"
post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"replicas":"abc",'"$GOOD_RES"',"labels":{"a":"b"}}'
post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"replicas":1.5,'"$GOOD_RES"',"labels":{"a":"b"}}'
post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"replicas":"3",'"$GOOD_RES"',"labels":{"a":"b"}}'
post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":80,"replicas":true,'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- port as a string";        post '{"name":"x","namespace":"demo-api","image":"nginx:1","port":"80",'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- name: trailing hyphen / dot / empty / unicode"
post '{"name":"web-","namespace":"demo-api","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
post '{"name":"my.app","namespace":"demo-api","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
post '{"name":"","namespace":"demo-api","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
post '{"name":"名前","namespace":"demo-api","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- image: uppercase registry host"; post '{"name":"x","namespace":"demo-api","image":"Docker.IO/library/nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'
echo "-- image: longer than 255 chars";    post '{"name":"x","namespace":"demo-api","image":"'"$(printf 'a%.0s' $(seq 1 256))"'","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'

echo "-- malformed JSON body (400, not FastAPI's default 422)"
ADMIN=$(JWT_SUB=admin-user2 ./scripts/mint-token.sh admin '["demo-*"]')
api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d '{"name": '
api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d ''
api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d '[]'
```

Every response above has the same shape: `{"error": {"code": "invalid_input", "message": "...", "details": [...]}}`, and
the submitted value is never echoed back. Unknown routes and wrong methods use that shape too (404 `not_found`, 405 `method_not_allowed`).

## 6. List (GET, viewer)

```bash
api -H "Authorization: Bearer $VIEWER" $BASE/api/deployments                              # → 200 list (only namespaces your token allows)
api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=demo-api"          # → 200 filtered
api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=team-z"            # → 403 namespace not allowed
api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=BAD!"              # → 400
for NSV in UPPER a.b a- -a; do api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=$NSV"; done   # → 400 each
```

## 7. Detail (GET by id, viewer)

Shows replica counts, per-pod status (phase, restarts, container states) and the Deployment conditions.
Pods take a few seconds to start; the first call may show 0 ready.

```bash
sleep 12
api -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/$ID                          # → 200
api -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/11111111-1111-1111-1111-111111111111   # → 404 unknown id
api -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/not-a-uuid                   # → 400 malformed id
```

### Responses are always live (never cached)
Every GET reads the cluster at request time, and every response carries `Cache-Control: no-store`. Prove it by
changing the cluster **behind the API's back** and reading again immediately:

```bash
curl -s -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/$ID | python3 -c 'import sys,json; print("before:", json.load(sys.stdin)["replicas"])'
kubectl -n demo-api scale deployment echo --replicas=3
curl -s -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/$ID | python3 -c 'import sys,json; print("after kubectl scale:", json.load(sys.stdin)["replicas"])'   # desired is now 3, no delay
curl -s -D - -o /dev/null -H "Authorization: Bearer $VIEWER" $BASE/api/deployments | grep -iE "^HTTP|cache-control|pragma"                                     # → cache-control: no-store
```

## 7b. Namespaces, health and security headers

*These need an image built from commit `c132076` or later; on an older deployment `/api/namespaces` returns 404.*

`GET /api/namespaces` returns the namespaces that hold managed deployments **and** that your token may see (for UI filters).
List and detail responses also carry a computed `health` field: `healthy`, `degraded` (some replicas missing, or a pod is in
CrashLoopBackOff/ImagePullBackOff), `down` (wanted but none ready) or `idle` (scaled to zero on purpose).

```bash
api -H "Authorization: Bearer $VIEWER" $BASE/api/namespaces                    # → 200 {"namespaces":["demo-api"]}
api -H "Authorization: Bearer $OTHER"  $BASE/api/namespaces                    # → 200 {"namespaces":[]}  (other team sees none of ours)
api $BASE/api/namespaces                                                       # → 401
curl -s -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/$ID | python3 -c 'import sys,json; print("health:", json.load(sys.stdin)["health"])'
curl -s -D - -o /dev/null $BASE/healthz | grep -iE "x-content-type-options|x-frame-options|content-security-policy"   # nosniff, DENY, CSP
```

## 8. Update (PATCH, operator)

Only `replicas` and `image` can be changed; at least one is required.

```bash
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas": 2}'                               # → 200 scale
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"image": "hashicorp/http-echo:1.0"}'        # → 200 rolling update
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{}'                                         # → 400 nothing to change
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas": -3}'                           # → 400 negative
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"name": "hacked"}'                         # → 400 field not allowed
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas": "abc"}'                        # → 400 wrong type
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas": 1.5}'                          # → 400 not an integer
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas":'                               # → 400 malformed JSON
kubectl -n demo-api get deploy echo -o jsonpath='replicas={.spec.replicas} image={.spec.template.spec.containers[0].image}{"\n"}'   # confirm in the cluster
```

## 9. Rollout restart (POST, operator)

```bash
api -X POST $BASE/api/deployments/$ID/restart -H "Authorization: Bearer $OPERATOR"       # → 200
kubectl -n demo-api get deploy echo -o jsonpath='{.spec.template.metadata.annotations}{"\n"}'   # restartedAt annotation was set
```

## 10. Authorization checks (403 / 404)

```bash
echo "-- viewer cannot create";   api -X POST $BASE/api/deployments -H "Authorization: Bearer $VIEWER" -H "Content-Type: application/json" -d "$CREATE_BODY"      # → 403
echo "-- operator cannot delete"; api -X DELETE $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR"                                                  # → 403
echo "-- viewer cannot patch";    api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $VIEWER" -H "Content-Type: application/json" -d '{"replicas":3}'  # → 403
echo "-- other team's admin sees 404, not 403 (existence is not leaked)"
api -H "Authorization: Bearer $OTHER" $BASE/api/deployments/$ID                          # → 404
api -X DELETE $BASE/api/deployments/$ID -H "Authorization: Bearer $OTHER"                # → 404
```

### Protected namespaces
Even a token allowed for `*` can never touch `kube-system`, `kube-public`, `kube-node-lease`, `default` or any `kube-*`.

```bash
for NS in kube-system kube-public kube-node-lease default; do
  echo "-- create in $NS"
  api -X POST $BASE/api/deployments -H "Authorization: Bearer $GOD" -H "Content-Type: application/json" \
    -d '{"name":"evil","namespace":"'$NS'","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'    # → 403 "... is protected (built-in Kubernetes namespace) and cannot be used"
done
```

## 11. Rate limiting (429)

Reads are limited to 120/min per token subject (writes 30/min). **Limits are counted per pod**, and the chart runs
2 replicas, so the effective limit is about 2 x 120 = 240/min. A separate subject is used here so other steps are unaffected.

```bash
RL=$(JWT_SUB=ratelimit-demo ./scripts/mint-token.sh viewer '["demo-*"]')
for i in $(seq 1 320); do curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $RL" $BASE/api/deployments; done | sort | uniq -c   # → 2 pods (ingress): about 240 x 200 and 80 x 429; one pod (local server): 120 x 200 and 200 x 429
curl -s -i -H "Authorization: Bearer $RL" $BASE/api/deployments | grep -iE "^HTTP|retry-after"                                                    # → 429 + Retry-After
```

## 12. Delete (DELETE, admin)

Foreground cascade: the Deployment's ReplicaSets and Pods are removed too. **The Deployment stays visible (still 200) until
its pods have finished terminating**, which can take up to the pod's 30 s grace period, so poll instead of sleeping:

```bash
api -X DELETE $BASE/api/deployments/$ID -H "Authorization: Bearer $ADMIN"               # → 204 (deletion accepted)
for i in $(seq 1 60); do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/$ID)
  [ "$CODE" = "404" ] && { echo "gone after ~$((i*2))s"; break; }
  [ $i = 60 ] && echo "still terminating after 120s: the node is busy; check: kubectl -n demo-api get pods"; sleep 2
done                                                                                     # → 404 once pods are gone (typically 10-30 s)
api -X DELETE $BASE/api/deployments/$ID -H "Authorization: Bearer $ADMIN"               # → 404 second delete
kubectl -n demo-api get deploy,rs,pods                                                   # → "No resources found"
```

## 13. See the audit trail

Denied attempts (401/403/429, cross-namespace probes) and successful changes are logged as JSON. Tokens and request bodies are never logged.

```bash
kubectl -n deploy-api logs deploy/deploy-api-deploy-api --tail=400 | grep '"audit"\|audit:' | tail -8
```

## 14. Clean up

```bash
kubectl delete namespace demo-api scope-demo --wait=false
echo "done. Stop the port-forward terminal with Ctrl-C."
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `curl: Could not resolve host` | offline: add `--resolve deploy-api.localtest.me:18080:127.0.0.1` or use `-H "Host: deploy-api.localtest.me" http://127.0.0.1:18080` |
| `Connection refused` on :18080 | the port-forward terminal is not running (step 0) |
| `404 nginx` page | wrong Host header; the ingress only routes `deploy-api.localtest.me` |
| 403 `... is not in this token's allowed namespaces (demo-*)` | the token's `namespaces` claim does not include that namespace. Mint one that does, or `./scripts/mint-token.sh admin` (defaults to `["*"]`). The API creates missing namespaces itself |
| 403 `... is protected (built-in Kubernetes namespace)` | `kube-system`, `kube-public`, `kube-node-lease`, `default` and `kube-*` can never be used, by any token |
| 401 on every call | secret mismatch: re-run step 1; or the token expired (default 1 h) |
| 429 right after several 401s | repeated failed auth locks the caller's IP for 60 s. **Through the ingress all clients share the ingress pod's IP**, so wait a minute |
| DELETE returned 204 but GET still shows the deployment | normal: foreground deletion waits for pods to terminate (up to ~30 s). Poll until 404 |
| 500 `Kubernetes API error ... exceeded quota` | the cluster rejected the request; the message says why |
| Pod `CreateContainerConfigError` / `CrashLoopBackOff` for another image | pods run non-root with a read-only filesystem. Use an image that supports it (e.g. `nginxinc/nginx-unprivileged`) |

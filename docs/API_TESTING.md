# Testing the Deployment Manager API with curl

Run every step **in the same terminal**, in order: later steps reuse variables from earlier ones.
Blocks marked `bash` are runnable; blocks marked `sh` are for reading only.

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
export ADMIN=$(./scripts/mint-token.sh admin '["demo-*"]')
export OPERATOR=$(./scripts/mint-token.sh operator '["demo-*"]')
export VIEWER=$(./scripts/mint-token.sh viewer '["demo-*"]')
export OTHER=$(JWT_SUB=mallory ./scripts/mint-token.sh admin '["team-z"]')   # admin, but a different namespace
export GOD=$(JWT_SUB=god ./scripts/mint-token.sh admin '["*"]')              # admin for everything (still blocked from protected namespaces)

# small helper: prints the body, then the HTTP status
api() { curl -s -w "\n[HTTP %{http_code}]\n" "$@"; }
echo "tokens ready"
```

| Role | May call |
|---|---|
| viewer | GET list, GET detail |
| operator | + PATCH, restart |
| admin | + POST create, DELETE |

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

Required: `name`, `namespace` (created if missing), `image`, `port`, `resources` (requests + limits), `labels`.
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

## 5. Invalid input → 400

Each of these must return **400** with `"code":"invalid_input"`.

```bash
post() { api -X POST $BASE/api/deployments -H "Authorization: Bearer $ADMIN" -H "Content-Type: application/json" -d "$1"; }
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
```

## 6. List (GET, viewer)

```bash
api -H "Authorization: Bearer $VIEWER" $BASE/api/deployments                              # → 200 list (only namespaces your token allows)
api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=demo-api"          # → 200 filtered
api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=team-z"            # → 403 namespace not allowed
api -H "Authorization: Bearer $VIEWER" "$BASE/api/deployments?namespace=BAD!"              # → 400
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

## 8. Update (PATCH, operator)

Only `replicas` and `image` can be changed; at least one is required.

```bash
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas": 2}'                               # → 200 scale
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"image": "hashicorp/http-echo:1.0"}'        # → 200 rolling update
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{}'                                         # → 400 nothing to change
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"replicas": -3}'                           # → 400 negative
api -X PATCH $BASE/api/deployments/$ID -H "Authorization: Bearer $OPERATOR" -H "Content-Type: application/json" -d '{"name": "hacked"}'                         # → 400 field not allowed
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
    -d '{"name":"evil","namespace":"'$NS'","image":"nginx:1","port":80,'"$GOOD_RES"',"labels":{"a":"b"}}'    # → 403
done
```

## 11. Rate limiting (429)

Reads are limited to 120/min per token subject (writes 30/min). **Limits are counted per pod**, and the chart runs
2 replicas, so the effective limit is about 2 x 120 = 240/min. A separate subject is used here so other steps are unaffected.

```bash
RL=$(JWT_SUB=ratelimit-demo ./scripts/mint-token.sh viewer '["demo-*"]')
for i in $(seq 1 320); do curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $RL" $BASE/api/deployments; done | sort | uniq -c   # → roughly 240 x 200 and 80 x 429
curl -s -i -H "Authorization: Bearer $RL" $BASE/api/deployments | grep -iE "^HTTP|retry-after"                                                    # → 429 + Retry-After
```

## 12. Delete (DELETE, admin)

Foreground cascade: the Deployment's ReplicaSets and Pods are removed too.

```bash
api -X DELETE $BASE/api/deployments/$ID -H "Authorization: Bearer $ADMIN"               # → 204
sleep 10
api -H "Authorization: Bearer $VIEWER" $BASE/api/deployments/$ID                         # → 404 gone
kubectl -n demo-api get deploy,rs,pods                                                   # → "No resources found"
```

## 13. See the audit trail

Denied attempts (401/403/429, cross-namespace probes) and successful changes are logged as JSON. Tokens and request bodies are never logged.

```bash
kubectl -n deploy-api logs deploy/deploy-api-deploy-api --tail=400 | grep '"audit"\|audit:' | tail -8
```

## 14. Clean up

```bash
kubectl delete namespace demo-api --wait=false
echo "done. Stop the port-forward terminal with Ctrl-C."
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `curl: Could not resolve host` | offline: add `--resolve deploy-api.localtest.me:18080:127.0.0.1` or use `-H "Host: deploy-api.localtest.me" http://127.0.0.1:18080` |
| `Connection refused` on :18080 | the port-forward terminal is not running (step 0) |
| `404 nginx` page | wrong Host header; the ingress only routes `deploy-api.localtest.me` |
| 401 on every call | secret mismatch: re-run step 1; or the token expired (default 1 h) |
| 429 right after several 401s | repeated failed auth locks the caller's IP for 60 s. **Through the ingress all clients share the ingress pod's IP**, so wait a minute |
| 500 `Kubernetes API error ... exceeded quota` | the cluster rejected the request; the message says why |
| Pod `CreateContainerConfigError` / `CrashLoopBackOff` for another image | pods run non-root with a read-only filesystem. Use an image that supports it (e.g. `nginxinc/nginx-unprivileged`) |

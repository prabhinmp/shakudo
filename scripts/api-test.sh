#!/usr/bin/env bash
# End-to-end curl tests for every endpoint, with PASS/FAIL assertions. Mirrors docs/API_TESTING.md.
# Uses the REAL cluster (no mocks): creates namespace $NS, deletes it at the end.
#
#   ./scripts/api-test.sh                      # against the ingress: http://deploy-api.localtest.me:18080
#   BASE=http://127.0.0.1:18081 SECRET=... ./scripts/api-test.sh      # against a local server
#   ./scripts/api-test.sh --ratelimit          # also run the (slow, 320 requests) rate-limit step
# Exit code is non-zero if any check fails.
set -uo pipefail
cd "$(dirname "$0")/.."

BASE="${BASE:-http://deploy-api.localtest.me:18080}"
NS="${NS:-demo-apitest}"                       # must match the "demo-*" token allowlist below
SECRET="${SECRET:-$(kubectl -n deploy-api get secret deploy-api-deploy-api-jwt -o jsonpath='{.data.jwt-secret}' | base64 -d)}"
export SECRET
RATELIMIT=0; [ "${1:-}" = "--ratelimit" ] && RATELIMIT=1

PASS=0; FAIL=0; BODY=""
green(){ printf '\033[32m%s\033[0m\n' "$*"; }; red(){ printf '\033[31m%s\033[0m\n' "$*"; }
section(){ printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
ok(){ PASS=$((PASS+1)); green "  PASS  $1"; }
bad(){ FAIL=$((FAIL+1)); red "  FAIL  $1"; [ -n "${2:-}" ] && printf '        %s\n' "$2" | cut -c1-220; }

# expect <status> <description> <curl args...>   (sets $BODY)
expect() {
  local want=$1 desc=$2; shift 2
  local out code; out=$(curl -s -m 25 -w '\n%{http_code}' "$@"); code=${out##*$'\n'}; BODY=${out%$'\n'*}
  if [ "$code" = "$want" ]; then ok "$desc  [$code]"; else bad "$desc  (wanted $want, got $code)" "$BODY"; fi
}
has()  { if printf '%s' "$BODY" | grep -q -- "$2"; then ok "$1"; else bad "$1 (missing: $2)" "$BODY"; fi; }
hasnt(){ if printf '%s' "$BODY" | grep -q -- "$2"; then bad "$1 (unexpected: $2)" "$BODY"; else ok "$1"; fi; }
jq_(){ printf '%s' "$BODY" | python3 -c "import sys,json; d=json.load(sys.stdin); print($1)" 2>/dev/null; }
check(){ if [ "$2" = "$3" ]; then ok "$1  [$3]"; else bad "$1 (wanted '$3', got '$2')"; fi; }

ADMIN=$(./scripts/mint-token.sh admin '["demo-*"]');  OPER=$(./scripts/mint-token.sh operator '["demo-*"]')
VIEW=$(./scripts/mint-token.sh viewer '["demo-*"]');  OTHER=$(JWT_SUB=mallory ./scripts/mint-token.sh admin '["team-z"]')
GOD=$(JWT_SUB=god ./scripts/mint-token.sh admin '["*"]')
H(){ echo "Authorization: Bearer $1"; }
JSON="Content-Type: application/json"
RES='"resources":{"requests":{"cpu":"50m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}}'
mk(){ echo '{"name":"'$1'","namespace":"'${2:-$NS}'","image":"hashicorp/http-echo:latest","port":5678,"args":["-listen=:5678","-text=hi"],"env":[{"name":"MODE","value":"t"}],'"$RES"',"labels":{"app":"'$1'"}}'; }
POST(){ expect "$1" "$2" -X POST "$BASE/api/deployments" -H "$(H "${4:-$ADMIN}")" -H "$JSON" -d "$3"; }

echo "target: $BASE   namespace: $NS"
kubectl delete ns "$NS" --wait=true >/dev/null 2>&1   # start clean

section "1. health (no token)"
expect 200 "GET /healthz" "$BASE/healthz"
expect 200 "GET /readyz"  "$BASE/readyz"

section "2. authentication"
expect 401 "no token"      "$BASE/api/deployments"
expect 401 "garbage token" -H "Authorization: Bearer nope" "$BASE/api/deployments"

section "3. create (POST, admin)"
POST 201 "create deployment (namespace auto-created)" "$(mk echo)"
ID=$(jq_ 'd["id"]'); echo "        id=$ID"
has "response carries managed-by label" "app.kubernetes.io/managed-by"
POST 409 "duplicate name rejected" "$(mk echo)"
kubectl -n "$NS" create deployment foreign --image=hashicorp/http-echo:latest >/dev/null
POST 409 "deployment made OUTSIDE the API is rejected (live pre-flight)" "$(mk foreign)"
check "foreign deployment untouched (no managed-by label)" "$(kubectl -n "$NS" get deploy foreign -o jsonpath='{.metadata.labels.app\.kubernetes\.io/managed-by}')" ""
expect 200 "list excludes foreign deployment" "$BASE/api/deployments?namespace=$NS" -H "$(H "$VIEW")"; hasnt "foreign not listed" '"foreign"'
kubectl -n "$NS" delete deployment foreign >/dev/null

section "4. invalid input -> 400"
POST 400 "DNS-incompatible name"   '{"name":"Bad_Name","namespace":"'$NS'","image":"nginx:1","port":80,'"$RES"',"labels":{"a":"b"}}'
POST 400 "negative replicas"       '{"name":"x","namespace":"'$NS'","image":"nginx:1","port":80,"replicas":-1,'"$RES"',"labels":{"a":"b"}}'
POST 400 "bad image format"        '{"name":"x","namespace":"'$NS'","image":"NOT AN IMAGE","port":80,'"$RES"',"labels":{"a":"b"}}'
POST 400 "bad cpu value"           '{"name":"x","namespace":"'$NS'","image":"nginx:1","port":80,"resources":{"requests":{"cpu":"lots","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}},"labels":{"a":"b"}}'
POST 400 "request above limit"     '{"name":"x","namespace":"'$NS'","image":"nginx:1","port":80,"resources":{"requests":{"cpu":"500m","memory":"32Mi"},"limits":{"cpu":"100m","memory":"64Mi"}},"labels":{"a":"b"}}'
POST 400 "missing required fields" '{"name":"x"}'
POST 400 "unknown field (privileged)" '{"name":"x","namespace":"'$NS'","image":"nginx:1","port":80,'"$RES"',"labels":{"a":"b"},"privileged":true}'
POST 400 "registry not allowed"    '{"name":"x","namespace":"'$NS'","image":"evil.example.com/m:1","port":80,'"$RES"',"labels":{"a":"b"}}'
POST 400 "replicas above cap"      '{"name":"x","namespace":"'$NS'","image":"nginx:1","port":80,"replicas":999,'"$RES"',"labels":{"a":"b"}}'

section "5. list (GET, viewer)"
expect 200 "list"                          "$BASE/api/deployments" -H "$(H "$VIEW")"
expect 200 "list filtered by namespace"    "$BASE/api/deployments?namespace=$NS" -H "$(H "$VIEW")"; has "contains echo" '"echo"'
expect 403 "namespace not allowed"         "$BASE/api/deployments?namespace=team-z" -H "$(H "$VIEW")"
expect 400 "invalid namespace value"       "$BASE/api/deployments?namespace=BAD!" -H "$(H "$VIEW")"

section "6. detail (GET by id) - always live"
for i in $(seq 1 30); do BODY=$(curl -s -m 10 "$BASE/api/deployments/$ID" -H "$(H "$VIEW")"); [ "$(jq_ 'd["replicas"]["ready"]')" = "1" ] && break; sleep 2; done
expect 200 "detail" "$BASE/api/deployments/$ID" -H "$(H "$VIEW")"
check "pod is Running (real pod status)" "$(jq_ 'd["pods"][0]["phase"]')" "Running"
has "conditions present" '"Available"'
expect 404 "unknown id" "$BASE/api/deployments/11111111-1111-1111-1111-111111111111" -H "$(H "$VIEW")"
expect 400 "malformed id" "$BASE/api/deployments/not-a-uuid" -H "$(H "$VIEW")"
kubectl -n "$NS" scale deployment echo --replicas=3 >/dev/null          # change the cluster behind the API's back
expect 200 "detail right after kubectl scale" "$BASE/api/deployments/$ID" -H "$(H "$VIEW")"
check "reflects out-of-band change immediately (no cache)" "$(jq_ 'd["replicas"]["desired"]')" "3"
check "Cache-Control header" "$(curl -s -D - -o /dev/null "$BASE/api/deployments" -H "$(H "$VIEW")" | tr -d '\r' | grep -i '^cache-control' | cut -d' ' -f2)" "no-store"

section "7. update (PATCH, operator)"
PATCH(){ expect "$1" "$2" -X PATCH "$BASE/api/deployments/$ID" -H "$(H "$OPER")" -H "$JSON" -d "$3"; }
PATCH 200 "scale to 2"                  '{"replicas":2}'
PATCH 200 "change image (rolling update)" '{"image":"hashicorp/http-echo:1.0"}'
PATCH 400 "empty body"                  '{}'
PATCH 400 "negative replicas"           '{"replicas":-3}'
PATCH 400 "field not allowed"           '{"name":"hacked"}'
check "cluster really has 2 replicas + new image" "$(kubectl -n "$NS" get deploy echo -o jsonpath='{.spec.replicas} {.spec.template.spec.containers[0].image}')" "2 hashicorp/http-echo:1.0"

section "8. restart (POST, operator)"
expect 200 "restart" -X POST "$BASE/api/deployments/$ID/restart" -H "$(H "$OPER")"
check "restartedAt annotation set" "$(kubectl -n "$NS" get deploy echo -o jsonpath='{.spec.template.metadata.annotations}' | grep -c restartedAt)" "1"

section "9. authorization"
POST 403 "viewer cannot create" "$(mk v1)" "$VIEW"
expect 403 "operator cannot delete" -X DELETE "$BASE/api/deployments/$ID" -H "$(H "$OPER")"
expect 403 "viewer cannot patch" -X PATCH "$BASE/api/deployments/$ID" -H "$(H "$VIEW")" -H "$JSON" -d '{"replicas":3}'
expect 404 "other team: GET is 404, not 403 (no existence leak)" "$BASE/api/deployments/$ID" -H "$(H "$OTHER")"
expect 404 "other team: DELETE is 404" -X DELETE "$BASE/api/deployments/$ID" -H "$(H "$OTHER")"
before=$(kubectl get ns -o name | sort | shasum)        # snapshot BEFORE the protected-namespace attempts
for P in kube-system kube-public kube-node-lease default; do POST 403 "protected namespace $P (even with '*' token)" "$(mk evil "$P")" "$GOD"; done
check "no namespace was created or removed by those attempts" "$(kubectl get ns -o name | sort | shasum)" "$before"

section "10. newer endpoints (need an image built from commit c132076 or later)"
BODY=$(curl -s -m 10 -o /dev/null -w '%{http_code}' "$BASE/api/namespaces" -H "$(H "$VIEW")")
if [ "$BODY" = "404" ]; then
  echo "  SKIP  /api/namespaces not present on this deployment (older image). Redeploy to test it."
else
  expect 200 "GET /api/namespaces (viewer)" "$BASE/api/namespaces" -H "$(H "$VIEW")"; has "lists $NS" "\"$NS\""
  expect 200 "other team sees only its own namespaces" "$BASE/api/namespaces" -H "$(H "$OTHER")"; hasnt "other team does not see $NS" "\"$NS\""
  expect 401 "namespaces needs a token" "$BASE/api/namespaces"
  expect 200 "list carries a health field" "$BASE/api/deployments?namespace=$NS" -H "$(H "$VIEW")"; has "health present" '"health"'
  expect 200 "detail carries a health field" "$BASE/api/deployments/$ID" -H "$(H "$VIEW")"
  case "$(jq_ 'd["health"]')" in healthy|degraded|down|idle) ok "health is one of healthy/degraded/down/idle  [$(jq_ 'd["health"]')]";; *) bad "unexpected health value";; esac
  HDR=$(curl -s -D - -o /dev/null "$BASE/healthz" | tr -d '\r')
  echo "$HDR" | grep -qi '^x-content-type-options: nosniff' && ok "X-Content-Type-Options: nosniff" || bad "missing X-Content-Type-Options"
  echo "$HDR" | grep -qi '^x-frame-options: deny' && ok "X-Frame-Options: DENY" || bad "missing X-Frame-Options"
  echo "$HDR" | grep -qi "^content-security-policy: default-src 'self'" && ok "Content-Security-Policy set" || bad "missing Content-Security-Policy"
fi

if [ $RATELIMIT = 1 ]; then
  section "11. rate limiting (reads 120/min per subject, per pod)"
  RL=$(JWT_SUB=rl-$$ ./scripts/mint-token.sh viewer '["demo-*"]')
  n429=$(for i in $(seq 1 320); do curl -s -o /dev/null -w '%{http_code}\n' -H "$(H "$RL")" "$BASE/api/deployments"; done | grep -c 429)
  [ "$n429" -gt 0 ] && ok "got $n429 x 429 after exceeding the limit" || bad "no 429 seen"
fi

section "12. delete (DELETE, admin) + cascade"
expect 204 "delete" -X DELETE "$BASE/api/deployments/$ID" -H "$(H "$ADMIN")"
# Foreground deletion: the Deployment stays visible (terminating) until its Pods are gone, which can
# take as long as the pod's 30s grace period. So poll for the 404 instead of sleeping a fixed time.
gone=0; for i in $(seq 1 45); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$BASE/api/deployments/$ID" -H "$(H "$VIEW")")" = "404" ] && { gone=1; break; }; sleep 2; done
[ $gone = 1 ] && ok "GET returns 404 once deletion completes  [after ~$((i*2))s]" || bad "still visible 90s after DELETE"
expect 404 "second delete" -X DELETE "$BASE/api/deployments/$ID" -H "$(H "$ADMIN")"
check "ReplicaSets and Pods are gone (cascade)" "$(kubectl -n "$NS" get rs,pods --no-headers 2>/dev/null | wc -l | tr -d ' ')" "0"

kubectl delete ns "$NS" --wait=false >/dev/null 2>&1
printf '\n'; [ $FAIL -eq 0 ] && green "ALL $PASS CHECKS PASSED" || red "$FAIL FAILED, $PASS passed"
exit $([ $FAIL -eq 0 ] && echo 0 || echo 1)

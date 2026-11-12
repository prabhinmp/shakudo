#!/usr/bin/env bash
# Local CI for deploy-api. Stages run in order and STOP at the first failure,
# so an image is only built after every test has passed.
#
#   ./scripts/ci.sh                 run the pipeline once
#   ./scripts/ci.sh --watch         re-run whenever source files change (continuous)
#   ./scripts/ci.sh --deploy        also helm-upgrade the local minikube cluster
#   REGISTRY=docker.io/me ./scripts/ci.sh --push   also tag + push (only after everything passed)
set -uo pipefail
cd "$(dirname "$0")/.."

WATCH=0; DEPLOY=0; PUSH=0
for a in "$@"; do case $a in --watch) WATCH=1;; --deploy) DEPLOY=1;; --push) PUSH=1;;
  *) echo "unknown option $a"; exit 2;; esac; done

IMAGE=deploy-api
PY=.venv/bin/python
WATCHED=(app tests requirements.txt Dockerfile deploy scripts/ci.sh)

green(){ printf '\033[32m%s\033[0m\n' "$*"; }; red(){ printf '\033[31m%s\033[0m\n' "$*"; }
stage(){ printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

fingerprint() { # hash of file names + contents, ignoring caches
  find "${WATCHED[@]}" -type f ! -name '*.pyc' ! -path '*/__pycache__/*' -print0 2>/dev/null \
    | sort -z | xargs -0 shasum 2>/dev/null | shasum | cut -c1-12
}

pipeline() {
  local start=$SECONDS fp; fp=$(fingerprint); local tag="ci-$fp"

  stage "1/6 unit + API tests"
  $PY -m pytest -q -p no:warnings || { red "FAILED: tests. Nothing was built."; return 1; }

  stage "2/6 dependency vulnerability scan"
  .venv/bin/pip-audit -r requirements.txt || { red "FAILED: vulnerable dependencies"; return 1; }

  stage "3/6 helm lint + render"
  helm lint deploy/helm/deploy-api >/dev/null && helm template ci deploy/helm/deploy-api >/dev/null \
    || { red "FAILED: helm chart"; return 1; }

  stage "4/6 docker build ($IMAGE:$tag)"
  docker build -q -t "$IMAGE:$tag" -t "$IMAGE:latest-ci" . >/dev/null || { red "FAILED: docker build"; return 1; }

  stage "5/6 image checks"
  local uid; uid=$(docker run --rm --entrypoint id "$IMAGE:$tag" -u)
  [ "$uid" = "10001" ] || { red "FAILED: image runs as uid $uid, expected 10001"; return 1; }
  local out; out=$(docker run --rm "$IMAGE:$tag" 2>&1)   # capture first: grep -q + pipefail = false failure
  case "$out" in *"JWT_SECRET must be set"*) ;; *) red "FAILED: image must refuse to start without a secret"; return 1;; esac
  green "non-root + fail-fast OK"

  stage "6/6 publish/deploy (optional)"
  if [ $DEPLOY = 1 ]; then
    minikube image load "$IMAGE:$tag" && helm upgrade --install deploy-api deploy/helm/deploy-api \
      -n deploy-api --create-namespace -f deploy/helm/deploy-api/values-minikube.yaml --set image.tag="$tag" --wait --timeout 150s >/dev/null \
      || { red "FAILED: deploy to minikube"; return 1; }
    green "deployed $tag to minikube"
  else echo "skipped (--deploy)"; fi
  if [ $PUSH = 1 ]; then
    : "${REGISTRY:?set REGISTRY, e.g. docker.io/yourname}"
    docker tag "$IMAGE:$tag" "$REGISTRY/$IMAGE:$tag" && docker push "$REGISTRY/$IMAGE:$tag" \
      || { red "FAILED: push"; return 1; }
    green "pushed $REGISTRY/$IMAGE:$tag"
  else echo "push skipped (--push)"; fi

  green "PIPELINE PASSED in $((SECONDS-start))s  ->  $IMAGE:$tag"
}

if [ $WATCH = 0 ]; then pipeline; exit $?; fi

echo "watching ${WATCHED[*]} (Ctrl-C to stop)"
last=""
while true; do
  now=$(fingerprint)
  if [ "$now" != "$last" ]; then
    last=$now
    printf '\n\033[36m[%s] change detected\033[0m\n' "$(date +%T)"
    pipeline || red "waiting for next change..."
  fi
  sleep 2
done

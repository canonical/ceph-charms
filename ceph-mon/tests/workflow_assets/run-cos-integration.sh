#!/usr/bin/env bash
# ==============================================================================
# run-cos-integration.sh
# Quincy-adapted COS integration test for ceph-mon (grafana-agent).
#
# Mirrors the main-branch cos-integration-test GitHub workflow job, but:
#   - Builds ceph-mon locally (jammy) and deploys it from the local artifact,
#     exactly like the GH workflow. ceph-osd comes from Charmhub quincy/edge.
#   - Deploys the COS agent on ubuntu@22.04 instead of ubuntu@24.04.
#
# Usage:
#   bash ceph-mon/tests/workflow_assets/run-cos-integration.sh
#
# Env vars (all optional):
#   AGENT      - grafana-agent (default; only supported value in this version)
#   REPO_ROOT  - path to the ceph-charms checkout (default: /home/ubuntu/src/ceph-charms)
# ==============================================================================
set -euo pipefail

# --- Configuration ---
REPO_ROOT="${REPO_ROOT:-/home/ubuntu/src/ceph-charms}"
ASSETS="$REPO_ROOT/ceph-mon/tests/workflow_assets"
AGENT="${AGENT:-grafana-agent}"
CEPH_MODEL="ceph-cos-test"
COS_MODEL_NAME="cos-lite"
K8S_CLOUD="ck8s-cloud"
LXD_CONTROLLER="lxd"
LOGDIR="$REPO_ROOT/logs"
RUN_LOG="$LOGDIR/cos-integration-run.log"

mkdir -p "$LOGDIR"

# --- Agent-specific parameters ---
case "$AGENT" in
  grafana-agent)
    AGENT_DEPLOY="juju deploy grafana-agent --base ubuntu@22.04"
    AGENT_INTEGRATE="juju integrate grafana-agent ceph-mon && juju integrate grafana-agent:juju-info ceph-mon:juju-info"
    AGENT_WAIT="juju wait-for application grafana-agent --query='forEach(units, unit => unit.workload-status==\"active\" && unit.agent-status==\"idle\")' --timeout=20m"
    LOKI_APPLICATION="grafana-agent"
    LOKI_LOG_FILTER="ceph"
    ;;
  *)
    echo "ERROR: unsupported agent '$AGENT' (this script only supports grafana-agent)" >&2
    exit 1
    ;;
esac

cd "$REPO_ROOT"

# --- Helpers ---
log()    { echo -e "\n========== $(date -u '+%Y-%m-%dT%H:%M:%SZ') | $* =========="; }
section(){ log "STEP: $*"; echo "----- $* -----" >> "$RUN_LOG"; }

# --- Log collection trap (on failure) ---
collect_logs() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo ""
    log "FAILURE (exit $rc) — collecting diagnostic logs"
    set +e
    juju status -m "${K8S_CLOUD}:${COS_MODEL_NAME}" -o "$LOGDIR/cos-lite-status.yaml" 2>&1 | tee -a "$RUN_LOG" || true
    juju debug-log -m "${K8S_CLOUD}:${COS_MODEL_NAME}" --replay --tail 100 --no-tail > "$LOGDIR/cos-lite-debug-log.txt" 2>&1 || true
    juju status -m "${LXD_CONTROLLER}:${CEPH_MODEL}" -o "$LOGDIR/ceph-status.yaml" 2>&1 | tee -a "$RUN_LOG" || true
    juju debug-log -m "${LXD_CONTROLLER}:${CEPH_MODEL}" --replay --tail 100 --no-tail > "$LOGDIR/ceph-debug-log.txt" 2>&1 || true
    lxc exec k8s-node -- k8s kubectl get pods -n "$COS_MODEL_NAME" -o wide > "$LOGDIR/k8s-pods.txt" 2>&1 || true
    lxc exec k8s-node -- k8s kubectl get events -n "$COS_MODEL_NAME" --sort-by='.lastTimestamp' > "$LOGDIR/k8s-events.txt" 2>&1 || true
    lxc exec k8s-node -- k8s kubectl describe nodes > "$LOGDIR/k8s-nodes.txt" 2>&1 || true
    log "Logs collected in $LOGDIR"
  fi
}
trap collect_logs EXIT

# Redirect all output to the run log (and stdout)
exec > >(tee -a "$RUN_LOG") 2>&1

log "COS Integration Test — Quincy / jammy / ${AGENT}"
log "REPO_ROOT=$REPO_ROOT  AGENT=$AGENT  CEPH_MODEL=$CEPH_MODEL  COS_MODEL=${K8S_CLOUD}:${COS_MODEL_NAME}"

# ==============================================================================
# Step 0: Clean up any previous test run (teardown controllers/VMs/models)
#         Set CLEAN_FIRST=0 to skip (e.g. to reuse a running k8s/COS setup).
# ==============================================================================
if [ "${CLEAN_FIRST:-1}" = "1" ]; then
  log "STEP: 0/11 — Clean up previous run (CLEAN_FIRST=1)"
  bash "$ASSETS/teardown-cos-integration.sh" || true
else
  log "STEP: 0/11 — Skipped cleanup (CLEAN_FIRST=0)"
fi

# ==============================================================================
# Step 1: Pre-flight checks
# ==============================================================================
section "1/11 — Pre-flight checks"
for cmd in juju lxc jq curl; do
  command -v "$cmd" >/dev/null 2>&1 || { echo "ERROR: $cmd not found"; exit 1; }
  echo "  ok: $(command -v "$cmd")"
done

# ==============================================================================
# Step 2: Cleanup Docker (if present)
# ==============================================================================
section "2/11 — Cleanup Docker (if present)"
bash "$ASSETS/ci_helpers.sh" cleanup_docker || true

# ==============================================================================
# Step 3: Setup LXD VM (k8s-node)
# ==============================================================================
section "3/11 — Setup LXD VM (k8s-node)"
bash "$ASSETS/k8s/01-setup-lxd-vm.sh"

# ==============================================================================
# Step 4: Deploy k8s
# ==============================================================================
section "4/11 — Deploy k8s"
bash "$ASSETS/k8s/02-deploy-k8s.sh"

# ==============================================================================
# Step 5: Add Juju cloud & bootstrap k8s controller
# ==============================================================================
section "5/11 — Add Juju cloud & bootstrap k8s controller"
bash "$ASSETS/k8s/03-juju-add-cloud.sh"

# ==============================================================================
# Step 6: Deploy COS Lite
# ==============================================================================
section "6/11 — Deploy COS Lite"
bash "$ASSETS/k8s/04-deploy-cos-lite.sh"

# ==============================================================================
# Step 7: Bootstrap localhost LXD controller
# ==============================================================================
section "7/11 — Bootstrap localhost LXD controller"
if juju show-controller "$LXD_CONTROLLER" >/dev/null 2>&1; then
  echo "  Controller '$LXD_CONTROLLER' already exists — skipping bootstrap"
else
  juju bootstrap localhost "$LXD_CONTROLLER"
fi

# ==============================================================================
# Step 8: Build ceph-mon charm & deploy Ceph cluster over LXD (jammy)
# ==============================================================================
section "8/11 — Build ceph-mon charm & deploy Ceph cluster over LXD"
echo "  --> Building ceph-mon charm from source (jammy)"
rm -f ./ceph-mon.charm ceph-mon/*.charm
tox -c ceph-mon -e build
# rename.sh places the charm at repo-root/ceph-mon.charm (where the bundle expects it)
if [ ! -f ./ceph-mon.charm ]; then
  echo "ERROR: build did not produce ./ceph-mon.charm" >&2
  ls -la ceph-mon/*.charm 2>/dev/null || true
  exit 1
fi
echo "  --> Built charm: $(ls -la ./ceph-mon.charm)"

juju add-model "$CEPH_MODEL"
juju deploy "$ASSETS/ceph-cos.yaml"
juju wait-for application ceph-mon --query='forEach(units, unit => unit.workload-status=="active" && unit.agent-status=="idle")' --timeout=20m
juju wait-for application ceph-osd --query='forEach(units, unit => unit.workload-status=="active" && unit.agent-status=="idle")' --timeout=20m
juju status

# ==============================================================================
# Step 9: Integrate Ceph with COS via grafana-agent
# ==============================================================================
section "9/11 — Integrate Ceph with COS via ${AGENT}"

echo "  --> Offering COS endpoints on ${K8S_CLOUD}:${COS_MODEL_NAME}"
juju switch "${K8S_CLOUD}:${COS_MODEL_NAME}"
juju offer prometheus:receive-remote-write
juju offer loki:logging
juju offer grafana:grafana-dashboard

echo "  --> Switching to ${LXD_CONTROLLER}:${CEPH_MODEL}"
juju switch "${LXD_CONTROLLER}:${CEPH_MODEL}"

echo "  --> Deploying COS agent (${AGENT}) on ubuntu@22.04"
eval "$AGENT_DEPLOY"

echo "  --> Integrating ${AGENT} with ceph-mon"
eval "$AGENT_INTEGRATE"

echo "  --> Consuming cross-controller offers from ${K8S_CLOUD}"
juju consume "${K8S_CLOUD}:admin/${COS_MODEL_NAME}.prometheus"
juju consume "${K8S_CLOUD}:admin/${COS_MODEL_NAME}.loki"
juju consume "${K8S_CLOUD}:admin/${COS_MODEL_NAME}.grafana"

echo "  --> Integrating ${AGENT} with consumed offers"
juju integrate "$AGENT" prometheus
juju integrate "$AGENT" loki
juju integrate "$AGENT" grafana

echo "  --> Waiting for ceph-mon to settle"
juju wait-for application ceph-mon --query='forEach(units, unit => unit.workload-status=="active" && unit.agent-status=="idle")' --timeout=20m

echo "  --> Waiting for ${AGENT} to settle"
eval "$AGENT_WAIT"

# ==============================================================================
# Step 10: Verify Prometheus metrics, Grafana dashboards, Loki logs
# ==============================================================================
section "10/11 — Verify COS integration (metrics, dashboards, logs)"
COS_MODEL="${K8S_CLOUD}:${COS_MODEL_NAME}" \
EXPECTED_DASHBOARDS_FILE="$ASSETS/expected_dashboard.txt" \
LOKI_APPLICATION="$LOKI_APPLICATION" \
LOKI_LOG_FILTER="$LOKI_LOG_FILTER" \
bash "$ASSETS/k8s/05-verify-cos.sh"

# ==============================================================================
# Step 11: Show final Juju status
# ==============================================================================
section "11/11 — Show final Juju status"
juju status -m "${LXD_CONTROLLER}:${CEPH_MODEL}"
juju status -m "${K8S_CLOUD}:${COS_MODEL_NAME}"
echo "==> k8s pods in ${COS_MODEL_NAME} namespace:"
lxc exec k8s-node -- k8s kubectl get pods -n "$COS_MODEL_NAME" -o wide || true

log "COS INTEGRATION TEST PASSED ✓"

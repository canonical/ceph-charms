#!/bin/bash
# ==============================================================================
# 05-verify-cos.sh — Verify Ceph metrics in Prometheus, dashboards in Grafana,
#                     and logs in Loki
# ==============================================================================
#
# GitHub Actions integration notes:
# ---------------------------------
# - Runs after COS Lite and Ceph are deployed and integrated.
# - Requires juju, curl, and jq on the runner/host.
# - COS_MODEL is the Juju model where COS Lite is deployed.
# - EXPECTED_DASHBOARDS_FILE points to the file listing expected dashboard titles.
# - POLL_ATTEMPTS and POLL_INTERVAL control retry behaviour.
#
# Workflow example:
#   - name: Verify COS integration
#     run: bash ceph-mon/tests/workflow_assets/k8s/05-verify-cos.sh
#     env:
#       COS_MODEL: ck8s-cloud:cos-lite
#       EXPECTED_DASHBOARDS_FILE: ./ceph-mon/tests/workflow_assets/expected_dashboard.txt
# ==============================================================================

set -euo pipefail

COS_MODEL="${COS_MODEL:-cos-lite}"
EXPECTED_DASHBOARDS_FILE="${EXPECTED_DASHBOARDS_FILE:-$(dirname "$0")/../assets/expected_dashboard.txt}"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-20}"
POLL_INTERVAL_PROM="${POLL_INTERVAL_PROM:-30}"
POLL_INTERVAL_GRAF="${POLL_INTERVAL_GRAF:-60}"
POLL_INTERVAL_LOKI="${POLL_INTERVAL_LOKI:-30}"
LOKI_APPLICATION="${LOKI_APPLICATION:-ceph-mon}"
LOKI_LOG_FILTER="${LOKI_LOG_FILTER:-}"

# --- Resolve the expected dashboards file ---
if [ ! -f "${EXPECTED_DASHBOARDS_FILE}" ]; then
  echo "ERROR: Expected dashboards file not found at ${EXPECTED_DASHBOARDS_FILE}" >&2
  exit 1
fi

# --- Switch to COS model ---
echo "==> Switching to Juju model '${COS_MODEL}'"
juju switch "${COS_MODEL}"

# ==============================================================================
# Resolve ingress URLs via traefik
# ==============================================================================
echo "==> Resolving COS service URLs via traefik ingress"

# Prometheus is exposed via traefik ingress-per-unit; Grafana via traefik-route.
# The get-admin-password action on grafana conveniently returns the external URL.
# For prometheus, the traefik show-proxied-endpoints action gives us the URL.
proxied_json=$(juju run traefik/0 show-proxied-endpoints --format json --wait 2m)
prom_url=$(echo "$proxied_json" |
  jq -r '."traefik/0".results."proxied-endpoints"' |
  jq -r '.["prometheus/0"].url')

if [[ -z "$prom_url" || "$prom_url" == "null" ]]; then
  echo "ERROR: Could not resolve Prometheus URL from traefik proxied-endpoints" >&2
  exit 1
fi
echo "    Prometheus URL: ${prom_url}"

get_admin_action=$(juju run grafana/0 get-admin-password --format json --wait 5m)
action_status=$(echo "$get_admin_action" | jq -r '."grafana/0".status')
if [[ "$action_status" != "completed" ]]; then
  echo "ERROR: Failed to fetch admin password from grafana: $get_admin_action" >&2
  exit 1
fi
grafana_pass=$(echo "$get_admin_action" | jq -r '."grafana/0".results."admin-password"')
graf_url=$(echo "$get_admin_action" | jq -r '."grafana/0".results.url')

if [[ -z "$graf_url" || "$graf_url" == "null" ]]; then
  echo "ERROR: Could not resolve Grafana URL from get-admin-password action" >&2
  exit 1
fi
echo "    Grafana URL: ${graf_url}"

loki_url=$(echo "$proxied_json" |
  jq -r '."traefik/0".results."proxied-endpoints"' |
  jq -r '.["loki/0"].url')

if [[ -z "$loki_url" || "$loki_url" == "null" ]]; then
  echo "ERROR: Could not resolve Loki URL from traefik proxied-endpoints" >&2
  exit 1
fi
echo "    Loki URL: ${loki_url}"

# ==============================================================================
# Verify Prometheus metrics
# ==============================================================================
echo "==> Verifying Prometheus metrics"

for i in $(seq 1 "${POLL_ATTEMPTS}"); do
  curl_output=$(curl -s "${prom_url}/api/v1/query?query=ceph_health_detail")
  prom_status=$(echo "$curl_output" | jq -r '.status')
  result_count=$(echo "$curl_output" | jq '.data.result | length')
  if [[ "$prom_status" == "success" && "$result_count" -gt 0 ]]; then
    echo "    Ceph metrics found in Prometheus (${result_count} result(s))"
    break
  fi
  echo "    Waiting for ceph metrics in Prometheus (attempt ${i}/${POLL_ATTEMPTS})..."
  sleep "${POLL_INTERVAL_PROM}"
done

# Final assertion
curl_output=$(curl -s "${prom_url}/api/v1/query?query=ceph_health_detail")
prom_status=$(echo "$curl_output" | jq -r '.status')
result_count=$(echo "$curl_output" | jq '.data.result | length')
if [[ "$prom_status" != "success" || "$result_count" -eq 0 ]]; then
  echo "ERROR: Prometheus query for ceph_health_detail failed or returned no results: $curl_output" >&2
  exit 1
fi
echo "==> Prometheus metrics verification passed."

# ==============================================================================
# Verify Grafana dashboards
# ==============================================================================
echo "==> Verifying Grafana dashboards"

expected_dashboard_count=$(wc -l <"${EXPECTED_DASHBOARDS_FILE}")

for i in $(seq 1 "${POLL_ATTEMPTS}"); do
  curl -s -u "admin:${grafana_pass}" "${graf_url}/api/search" |
    jq '.[].title' | jq -s 'sort' >dashboards.json
  cat dashboards.json
  match_count=$(grep -F -c -f "${EXPECTED_DASHBOARDS_FILE}" dashboards.json || true)
  if [[ "$match_count" -eq "$expected_dashboard_count" ]]; then
    echo "    All expected dashboards present"
    break
  fi
  echo "    Waiting for dashboards (attempt ${i}/${POLL_ATTEMPTS}, ${match_count}/${expected_dashboard_count} matched)..."
  sleep "${POLL_INTERVAL_GRAF}"
done

# Final assertion
match_count=$(grep -F -c -f "${EXPECTED_DASHBOARDS_FILE}" dashboards.json || true)
if [[ "$match_count" -ne "$expected_dashboard_count" ]]; then
  echo "ERROR: Required dashboards still not present (${match_count}/${expected_dashboard_count})." >&2
  cat dashboards.json
  exit 1
fi
echo "==> Grafana dashboards verification passed."

# ==============================================================================
# Verify Loki logs
# ==============================================================================
echo "==> Verifying Loki logs from ${LOKI_APPLICATION} units"

loki_query="{juju_application=\"${LOKI_APPLICATION}\"}"
if [[ -n "${LOKI_LOG_FILTER}" ]]; then
  loki_query="${loki_query} |= \"${LOKI_LOG_FILTER}\""
fi

for i in $(seq 1 "${POLL_ATTEMPTS}"); do
  loki_output=$(curl -s -G "${loki_url}/loki/api/v1/query_range" \
    --data-urlencode "query=${loki_query}" \
    --data-urlencode "limit=1")
  loki_status=$(echo "$loki_output" | jq -r '.status')
  loki_result_count=$(echo "$loki_output" | jq '.data.result | length')
  if [[ "$loki_status" == "success" && "$loki_result_count" -gt 0 ]]; then
    echo "    ${LOKI_APPLICATION} logs found in Loki (${loki_result_count} stream(s))"
    break
  fi
  echo "    Waiting for ${LOKI_APPLICATION} logs in Loki (attempt ${i}/${POLL_ATTEMPTS})..."
  sleep "${POLL_INTERVAL_LOKI}"
done

# Final assertion
loki_output=$(curl -s -G "${loki_url}/loki/api/v1/query_range" \
  --data-urlencode "query=${loki_query}" \
  --data-urlencode "limit=1")
loki_status=$(echo "$loki_output" | jq -r '.status')
loki_result_count=$(echo "$loki_output" | jq '.data.result | length')
if [[ "$loki_status" != "success" || "$loki_result_count" -eq 0 ]]; then
  echo "ERROR: Loki query for ${LOKI_APPLICATION} logs failed or returned no results: $loki_output" >&2
  exit 1
fi
echo "==> Loki logs verification passed."

echo ""
echo "==> COS verification complete."

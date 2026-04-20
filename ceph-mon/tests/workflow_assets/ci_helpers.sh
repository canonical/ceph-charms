#!/usr/bin/env bash

WATCH_PIDS=()

function _logs_root() {
  echo "${GITHUB_WORKSPACE:-$(pwd)}/logs/cos"
}

function _capture_command() {
  local output_file="$1"
  shift

  {
    printf '$'
    printf ' %q' "$@"
    printf '\n'
    "$@"
  } >"${output_file}" 2>&1 || true
}

function _collect_common_state() {
  local destination="$1"

  mkdir -p "${destination}"
  _capture_command "${destination}/date.txt" date -u
  _capture_command "${destination}/snap-list.txt" snap list
  _capture_command "${destination}/juju-version.txt" juju version
  _capture_command "${destination}/juju-controllers.txt" juju controllers
  _capture_command "${destination}/juju-models.txt" juju models
  _capture_command "${destination}/microk8s-version.txt" sudo microk8s version
  _capture_command "${destination}/microk8s-status.txt" sudo microk8s status
  _capture_command "${destination}/k8s-nodes.txt" sudo microk8s kubectl get nodes -o wide
  _capture_command "${destination}/k8s-storageclass.txt" sudo microk8s kubectl get storageclass
  _capture_command "${destination}/k8s-pods.txt" sudo microk8s kubectl get pods -A -o wide
  _capture_command "${destination}/k8s-pvc-pv.txt" sudo microk8s kubectl get pvc,pv -A
  _capture_command "${destination}/df-h.txt" df -h
  _capture_command "${destination}/free-h.txt" free -h
  _capture_command "${destination}/id.txt" id
  _capture_command "${destination}/groups.txt" groups
  _capture_command "${destination}/lxd-version.txt" lxd --version
}

function _collect_controller_state() {
  local destination="$1"

  mkdir -p "${destination}"
  _capture_command "${destination}/k8s-all.txt" sudo microk8s kubectl get all -A -o wide
  _capture_command "${destination}/k8s-events.txt" sudo microk8s kubectl get events -A --sort-by=.lastTimestamp
  _capture_command "${destination}/k8s-describe-nodes.txt" sudo microk8s kubectl describe nodes
  _capture_command "${destination}/k8s-kube-system-pods.txt" sudo microk8s kubectl get pods -n kube-system -o wide
  _capture_command "${destination}/k8s-describe-pvc.txt" sudo microk8s kubectl describe pvc -A
  _capture_command "${destination}/juju-status.txt" juju status

  if sudo microk8s kubectl get namespace controller-k8s >/dev/null 2>&1; then
    _capture_command "${destination}/controller-k8s-all.txt" sudo microk8s kubectl get all -n controller-k8s -o wide
    _capture_command "${destination}/controller-k8s-describe-statefulset.txt" sudo microk8s kubectl describe statefulset -n controller-k8s controller
    _capture_command "${destination}/controller-k8s-describe-pod.txt" sudo microk8s kubectl describe pod -n controller-k8s controller-0
    _capture_command "${destination}/controller-k8s-logs.txt" sudo microk8s kubectl logs -n controller-k8s controller-0 --all-containers=true
  fi
}

function _start_bootstrap_watchers() {
  local destination="$1"

  mkdir -p "${destination}/watchers" "${destination}/snapshots"

  sudo microk8s kubectl get events -A --watch-only >"${destination}/watchers/k8s-events-watch.log" 2>&1 &
  WATCH_PIDS+=("$!")

  sudo microk8s kubectl get pods -A -w >"${destination}/watchers/k8s-pods-watch.log" 2>&1 &
  WATCH_PIDS+=("$!")

  (
    while true; do
      local timestamp snapshot_dir
      timestamp=$(date -u +%Y%m%dT%H%M%SZ)
      snapshot_dir="${destination}/snapshots/${timestamp}"
      mkdir -p "${snapshot_dir}"
      _capture_command "${snapshot_dir}/k8s-pods.txt" sudo microk8s kubectl get pods -A -o wide
      _capture_command "${snapshot_dir}/k8s-pvc-pv.txt" sudo microk8s kubectl get pvc,pv -A
      _capture_command "${snapshot_dir}/k8s-statefulsets.txt" sudo microk8s kubectl get statefulsets -A
      sleep 10
    done
  ) &
  WATCH_PIDS+=("$!")
}

function _stop_bootstrap_watchers() {
  local pid

  for pid in "${WATCH_PIDS[@]}"; do
    kill "${pid}" >/dev/null 2>&1 || true
    wait "${pid}" >/dev/null 2>&1 || true
  done

  WATCH_PIDS=()
}

function capture_baseline_diagnostics() {
  _collect_common_state "$(_logs_root)/baseline"
}

function collect_cos_diagnostics() {
  local context log_root
  context="${1:-manual}"
  log_root="$(_logs_root)"

  mkdir -p "${log_root}"
  _collect_common_state "${log_root}/failure/${context}/common"
  _collect_controller_state "${log_root}/failure/${context}/controller"
}

function install_deps() {
  date
  sudo apt-get -qq install jq
  sudo snap install juju
  sudo snap install microk8s --channel 1.32-strict/stable
  mkdir -p ~/.local/share/juju
  juju bootstrap localhost lxd
  date
}

function cleanup_docker() {
  sudo apt purge docker* --yes
  sudo apt purge containerd* --yes
  sudo apt autoremove --yes
  sudo rm -rf /run/containerd
}

function bootstrap_k8s() {
  sudo microk8s enable hostpath-storage
  sudo microk8s status --wait-ready
}

function bootstrap_k8s_controller() {
  local bootstrap_log_dir bootstrap_rc

  set -euxo pipefail

  capture_baseline_diagnostics
  bootstrap_log_dir="$(_logs_root)/bootstrap"
  mkdir -p "${bootstrap_log_dir}"

  sudo microk8s kubectl config view --raw | juju add-k8s localk8s --client

  _start_bootstrap_watchers "${bootstrap_log_dir}"

  set +e
  juju bootstrap localk8s k8s --debug 2>&1 | tee "${bootstrap_log_dir}/juju-bootstrap.log"
  bootstrap_rc=${PIPESTATUS[0]}
  set -e

  if [[ ${bootstrap_rc} -ne 0 ]]; then
    collect_cos_diagnostics bootstrap-step
  fi

  _stop_bootstrap_watchers

  return "${bootstrap_rc}"
}

function deploy_cos() {
  set -eux
  juju add-model cos
  juju deploy cos-lite --trust

  juju offer prometheus:receive-remote-write
  juju offer grafana:grafana-dashboard
  juju offer loki:logging

  juju wait-for application prometheus --query='name=="prometheus" && (status=="active" || status=="idle")' --timeout=10m
  juju wait-for application grafana --query='name=="grafana" && (status=="active" || status=="idle")' --timeout=10m
  juju wait-for application loki --query='name=="loki" && (status=="active" || status=="idle")' --timeout=10m

  juju status
}

function deploy_ceph() {
  date
  mv ~/artifacts/ceph-mon.charm ./ceph-mon.charm
  juju switch lxd
  juju add-model ceph-cos-test || true
  juju deploy ./ceph-mon/tests/workflow_assets/ceph-cos.yaml
  juju wait-for application ceph-mon --query='name=="ceph-mon" && (status=="active" || status=="idle")' --timeout=10m
  juju wait-for application ceph-osd --query='name=="ceph-osd" && (status=="active" || status=="idle")' --timeout=10m
  juju status
  date
}

function wait_grafana_agent() {
  set -eux
  date
  juju switch lxd

  # wait for grafana-agent to be ready for integration
  juju wait-for application grafana-agent --query='name=="grafana-agent" && (status=="blocked" || status=="idle")' --timeout=20m
  
  # Integrate with cos services
  juju integrate grafana-agent k8s:cos.prometheus
  juju integrate grafana-agent k8s:cos.grafana
  juju integrate grafana-agent k8s:cos.loki

  juju wait-for unit grafana-agent/0 --query='workload-message=="tracing: off"' --timeout=20m
}

function check_http_endpoints_up() {
  set -ux

  juju switch k8s
  prom_addr=$(juju status --format json | jq '.applications.prometheus.address' | tr -d "\"")
  graf_addr=$(juju status --format json | jq '.applications.grafana.address' | tr -d "\"")

  for i in $(seq 1 20); do
    prom_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$prom_addr:9090/graph")
    grafana_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$graf_addr:3000/login")
    if [[ $prom_http_code -eq 200 && $grafana_http_code -eq 200 ]]; then
      echo "Prometheus and Grafana HTTP endpoints are up"
      break
    fi
    echo "."
    sleep 30s
  done

  prom_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$prom_addr:9090/graph")
  if [[ $prom_http_code -ne 200 ]]; then
    echo "Prometheus HTTP endpoint not up: HTTP($prom_http_code)"
    exit 1
  fi

  grafana_http_code=$(curl -s -o /dev/null -w "%{http_code}" "http://$graf_addr:3000/login")
  if [[ $grafana_http_code -ne 200 ]]; then
    echo "Grafana HTTP endpoint not up: HTTP($grafana_http_code)"
    exit 1
  fi
}

function verify_o11y_services() {
  set -eux
  date
  juju switch k8s
  prom_addr=$(juju status --format json | jq '.applications.prometheus.address' | tr -d "\"")
  graf_addr=$(juju status --format json | jq '.applications.grafana.address' | tr -d "\"")

  # verify prometheus metrics are populated
  curl_output=$(curl "http://${prom_addr}:9090/api/v1/query?query=ceph_health_detail")
  prom_status=$(echo $curl_output | jq '.status' | tr -d "\"")
  if [[ "$prom_status" != "success" ]]; then
    echo "Prometheus query for ceph_health_detail returned $curl_output"
    exit 1
  fi

  get_admin_action=$(juju run grafana/0 get-admin-password --format json --wait 5m)
  action_status=$(echo $get_admin_action | jq '."grafana/0".status' | tr -d "\"")
  if [[ $action_status != "completed" ]]; then
    echo "Failed to fetch admin password from grafana: $get_admin_action"
    exit 1
  fi

  grafana_pass=$(echo $get_admin_action | jq '."grafana/0".results."admin-password"' | tr -d "\"")

  # check if expected dashboards are populated in grafana
  expected_dashboard_count=$(wc -l < ./ceph-mon/tests/workflow_assets/expected_dashboard.txt)
  for i in $(seq 1 20); do
    curl http://admin:${grafana_pass}@${graf_addr}:3000/api/search| jq '.[].title' | jq -s 'sort' > dashboards.json
    cat ./dashboards.json 

    # compare the dashboard outputs
    match_count=$(grep -F -c -f ./ceph-mon/tests/workflow_assets/expected_dashboard.txt dashboards.json || true) 
    if [[ $match_count -eq $expected_dashboard_count ]]; then
      echo "Dashboards match expectations"
      break 
    fi
    echo "."
    sleep 1m
  done
  
  match_count=$(grep -F -c -f ./ceph-mon/tests/workflow_assets/expected_dashboard.txt dashboards.json || true) 
  if [[ $match_count -ne $expected_dashboard_count ]]; then
    echo "Required dashboards still not present."
    cat ./dashboards.json
    exit 1
  fi
}

run="${1}"
shift

$run "$@"

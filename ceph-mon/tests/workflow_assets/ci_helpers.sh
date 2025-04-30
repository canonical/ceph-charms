#!/usr/bin/env bash

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
  set -eux
  sudo microk8s kubectl config view --raw | juju add-k8s localk8s --client
 
  juju bootstrap localk8s k8s --debug
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

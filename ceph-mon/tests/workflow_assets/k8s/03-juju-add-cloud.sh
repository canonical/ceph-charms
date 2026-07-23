#!/bin/bash
# ==============================================================================
# 03-juju-add-cloud.sh — Register k8s as a Juju cloud and bootstrap a controller
# ==============================================================================
#
# GitHub Actions integration notes:
# ---------------------------------
# - Requires juju snap pre-installed on the runner.
# - KUBECONFIG_PATH must point to the kubeconfig exported by 02-deploy-k8s.sh.
# - JUJU_CLOUD_NAME is the name registered in Juju — used by subsequent scripts
#   to add models and deploy charms.
# - Bootstrap can be slow (~3-5 min). In CI, set an appropriate step timeout.
#
# Workflow example:
#   - name: Add Juju cloud & bootstrap
#     run: bash 03-juju-add-cloud.sh
#     timeout-minutes: 10
#     env:
#       KUBECONFIG_PATH: ./kubeconfig.yaml
#       JUJU_CLOUD_NAME: ck8s-cloud
# ==============================================================================

set -euo pipefail

KUBECONFIG_PATH="${KUBECONFIG_PATH:-$(pwd)/kubeconfig.yaml}"
JUJU_CLOUD_NAME="${JUJU_CLOUD_NAME:-ck8s-cloud}"

if [ ! -f "${KUBECONFIG_PATH}" ]; then
    echo "ERROR: kubeconfig not found at ${KUBECONFIG_PATH}. Run 02-deploy-k8s.sh first." >&2
    exit 1
fi

# --- Register the k8s cluster as a Juju cloud (client-side only) ---
echo "==> Adding k8s cluster as Juju cloud '${JUJU_CLOUD_NAME}'"
KUBECONFIG="${KUBECONFIG_PATH}" juju add-k8s "${JUJU_CLOUD_NAME}" --client

# --- Bootstrap a Juju controller on the cloud ---
echo "==> Bootstrapping Juju controller on '${JUJU_CLOUD_NAME}'"
KUBECONFIG="${KUBECONFIG_PATH}" juju bootstrap "${JUJU_CLOUD_NAME}" --config controller-service-type=loadbalancer

echo "==> Juju controller ready."
juju controllers

#!/usr/bin/env bash

function cleanup_docker() {
  sudo apt purge docker* --yes
  sudo apt purge containerd* --yes
  sudo apt autoremove --yes
  sudo rm -rf /run/containerd
}

run="${1}"
shift

$run "$@"

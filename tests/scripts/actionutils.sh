#!/usr/bin/env bash

function cleaript() {
    # Docker can inject rules causing firewall conflicts
    sudo iptables -P FORWARD ACCEPT  || true
    sudo ip6tables -P FORWARD ACCEPT || true
    sudo iptables -F FORWARD  || true
    sudo ip6tables -F FORWARD || true

}

function cacheimgs() {
    local base="${1?missing}"
    juju add-model dummy
    juju add-machine --base "$base"
    sleep 10
    juju add-machine --base "$base" --constraints "virt-type=virtual-machine" 
    while [ "$(juju machines | egrep -wc 'started')" -ne 2 ]; do
        sleep 2
    done
    juju destroy-model --force --timeout 20s  --no-prompt dummy
    sleep 5
}

function setup_functest() {
    sudo apt -y install tox
    if [ ! -d "$HOME/.local/share/juju" ]; then
        sudo snap install juju --channel=3.6/stable
        mkdir -p ~/.local/share/juju
        juju bootstrap \
             --auto-upgrade=false \
             --model-default=tests/configs/model-defaults.yaml \
             localhost localhost
    fi
    sudo snap install --classic juju-crashdump
    cp tests/configs/dot.zaza.yaml ~/.zaza.yaml
}

run="${1}"
shift

$run "$@"

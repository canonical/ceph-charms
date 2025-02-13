#!/usr/bin/env bash

function cleaript() {
    # Docker can inject rules causing firewall conflicts
    sudo iptables -P FORWARD ACCEPT  || true
    sudo ip6tables -P FORWARD ACCEPT || true
    sudo iptables -F FORWARD  || true
    sudo ip6tables -F FORWARD || true

}

function cacheimgs() {
    lxc launch $1 ctemp 2>&1 >/dev/null
    lxc launch $1 vmtemp --vm -c limits.cpu=2 -c limits.memory=4GiB -d root,size=25GiB 2>&1 >/dev/null
    lxc stop ctemp
    lxc delete ctemp
    sleep 60
    lxc stop vmtemp
    lxc delete vmtemp
}

run="${1}"
shift

$run "$@"

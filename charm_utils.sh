#!/bin/bash

function build() {
  local charm="${1?missing}"

  # remove old and copy fresh dependencies into lib dir.
  rm -rf $charm/lib/charms.ceph*
  cp -r ./charms.ceph* $charm/lib/

  cd $charm
  tox -e build
}

function copy_libs() {
  local charm="${1?missing}"

  # remove old and copy fresh dependencies into lib dir.
  rm -rf $charm/lib/charms.ceph*
  cp -r ./charms.ceph* $charm/lib/
}

function clean() {
  local charm="${1?missing}"

  # remove old and copy fresh dependencies into lib dir.
  rm -rf $charm/lib/charms.ceph*
  cd $charm
  charmcraft clean
}

run="${1}"
shift

$run "$@"

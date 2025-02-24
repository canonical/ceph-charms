#!/bin/bash

function copy_libs() {
  local charm="${1?missing}"

   # remove old copy of local libraries may fail if lib folder does not exist. 
  rm -rf $charm/lib/charms.ceph* || true
  # create a lib folder in case it does not exist
  mkdir -p $charm/lib
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

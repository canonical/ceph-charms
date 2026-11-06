#!/bin/bash
# Build a NON-SHIPPING test kernel and its crypto evidence module.
set -euo pipefail
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 KERNEL_REPO NEW_BUILD_DIRECTORY (JOBS defaults to 2)" >&2
    exit 2
fi
repo=$(realpath "$1")
destination=$(realpath -m "$2")
here=$(dirname "$(realpath "$0")")
case "$destination/" in
    "$repo/"*) echo "Build directory must be outside the kernel checkout" >&2; exit 2 ;;
esac

# Refuse reuse: case 07 later instruments this archive, not the original checkout.
mkdir -p "$(dirname "$destination")"
mkdir "$destination"
source="$destination/source"
output="$destination/output"
module="$destination/module"
mkdir "$source" "$output" "$module"
git -C "$repo" archive --format=tar HEAD > "$destination/source.tar"
tar -xf "$destination/source.tar" -C "$source"

# Keep the build recipe here and the reasons for individual settings in the fragment.
make_args=(-C "$source" "O=$output" "-j${JOBS:-2}" KBUILD_BUILD_VERSION=1)
make "${make_args[@]}" x86_64_defconfig
"$source/scripts/kconfig/merge_config.sh" -m -O "$output" "$output/.config" "$here/kernel.config"
make "${make_args[@]}" olddefconfig
make "${make_args[@]}" bindeb-pkg KDEB_PKGVERSION=1 KDEB_CHANGELOG_DIST=noble

# A full build supplies Module.symvers; modules_prepare alone lacks symbol CRCs.
cp "$here/Makefile" "$here/cephx_sru_crypto.c" "$module/"
make "${make_args[@]}" "M=$module" modules
release=$(cat "$output/include/config/kernel.release")
image="$destination/linux-image-${release}_1_amd64.deb"

# Record exactly what setup_client.py installs and the Python tests later check.
revision=$(git get-tar-commit-id < "$destination/source.tar")
image_sha=$(sha256sum "$image" | cut -d' ' -f1)
module_sha=$(sha256sum "$module/cephx_sru_crypto.ko" | cut -d' ' -f1)
config_sha=$(sha256sum "$output/.config" | cut -d' ' -f1)
jq -n --arg revision "$revision" --arg release "$release" \
    --arg image "$image" --arg image_sha "$image_sha" \
    --arg module "$module/cephx_sru_crypto.ko" --arg module_sha "$module_sha" \
    --arg config "$output/.config" --arg config_sha "$config_sha" \
    '{revision: $revision, SRU_EXPECTED_KERNEL: $release, case: "06",
      image: {path: $image, sha256: $image_sha},
      module: {path: $module, sha256: $module_sha},
      config: {path: $config, sha256: $config_sha}}' > "$destination/artifacts.json"
printf 'Artifacts: %s\n' "$destination/artifacts.json"

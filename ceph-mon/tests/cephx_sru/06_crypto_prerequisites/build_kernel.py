#!/usr/bin/env python3
"""Build a NON-SHIPPING crypto-selftest kernel without changing its checkout."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent


def run(*args, **kwargs):
    print("+", *map(str, args), flush=True)
    return subprocess.run(list(map(str, args)), check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path)
    parser.add_argument("--config", type=Path, help="Optional base; default is a trimmed amd64 VM defconfig")
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--resume-build", action="store_true",
                        help="Retry an incomplete package build using its existing archived source/config/objects")
    args = parser.parse_args()
    repo, destination = args.repo.resolve(), args.destination.resolve()
    if destination == repo or repo in destination.parents:
        parser.error("Destination must be outside the kernel checkout")
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    config = args.config.resolve() if args.config else None
    revision = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    source, output, module = (destination / name for name in ("source", "output", "module"))
    archive = destination / "source.tar"
    make = ["make", "-C", source, f"O={output}", f"-j{args.jobs}", "KBUILD_BUILD_VERSION=1"]
    if args.resume_build:
        if ((destination / "artifacts.json").exists()
                or not all(path.is_dir() for path in (source, output, module))):
            parser.error("--resume-build requires an incomplete existing build")
        with archive.open("rb") as stream:
            archived_revision = subprocess.check_output(["git", "get-tar-commit-id"], stdin=stream, text=True).strip()
        if archived_revision != revision:
            parser.error("Kernel HEAD differs from the retained source archive")
    else:
        destination.mkdir(parents=True, exist_ok=False)
        for directory in (source, output, module):
            directory.mkdir()
        with archive.open("wb") as stream:
            run("git", "-C", repo, "archive", "--format=tar", revision, stdout=stream)
        run("tar", "-xf", archive, "-C", source)
        if config:
            shutil.copyfile(config, output / ".config")
        else:
            run(*make, "x86_64_defconfig")
            # Keep EFI, virtio disk/network/console, ext4, systemd/Juju facilities.
            trim = ["DRM", "SOUND", "USB_SUPPORT", "WLAN", "WIRELESS", "PCCARD",
                    "MACINTOSH_DRIVERS", "AGP", "MEDIA_SUPPORT", "NFS_FS", "NFSD",
                    "9P_FS", "NET_9P", "PROFILING", "HIBERNATION", "SCHED_MC_PRIO",
                    "X86_INTEL_PSTATE", "CPU_FREQ", "ETHERNET"]
            run(source / "scripts/config", "--file", output / ".config",
                *(word for option in trim for word in ("--disable", option)))
        # Never import Ubuntu's signing paths or spend disk/RAM on debug packages.
        run(source / "scripts/config", "--file", output / ".config",
            "--set-str", "LOCALVERSION", "-cephx-sru06", "--disable", "LOCALVERSION_AUTO",
            "--set-str", "SYSTEM_TRUSTED_KEYS", "", "--set-str", "SYSTEM_REVOCATION_KEYS", "",
            "--disable", "MODULE_SIG", "--disable", "MODULE_SIG_FORCE", "--disable", "MODULE_SIG_ALL",
            "--disable", "DEBUG_INFO", "--disable", "DEBUG_INFO_BTF",
            "--disable", "DEBUG_INFO_DWARF_TOOLCHAIN_DEFAULT", "--enable", "DEBUG_INFO_NONE",
            "--enable", "DEBUG_KERNEL", "--enable", "MODULES", "--enable", "MODULE_UNLOAD",
            "--enable", "MODVERSIONS",
            "--enable", "CRYPTO_MANAGER", "--disable", "CRYPTO_MANAGER_DISABLE_TESTS",
            "--enable", "CRYPTO_MANAGER_EXTRA_TESTS", "--module", "CRYPTO_KRB5",
            "--enable", "CRYPTO_KRB5_SELFTESTS", "--module", "CEPH_LIB",
            "--enable", "DEBUG_FS", "--enable", "VIRTIO_PCI", "--enable", "VIRTIO_BLK",
            "--enable", "VIRTIO_NET", "--enable", "VIRTIO_CONSOLE", "--enable", "SCSI_VIRTIO",
            "--enable", "EXT4_FS", "--enable", "EFI", "--enable", "EFI_STUB")
        run(*make, "olddefconfig")
    if not config and not args.resume_build:
        final_config = (output / ".config").read_text()
        for line in ("# CONFIG_CPU_FREQ is not set", "# CONFIG_ETHERNET is not set",
                     "CONFIG_VIRTIO_NET=y"):
            if line + "\n" not in final_config:
                raise RuntimeError(f"VM config requires {line}; inspect Kconfig selectors")
    # Unrelated Noble source defect: this allocator has one TU-local caller,
    # like neighboring lsm_ipc_alloc, but was accidentally declared global.
    # Fix the definition, not -Werror; never patch the original kernel checkout.
    security = (source / "security/security.c").read_bytes()
    if b"\nint lsm_key_alloc(struct key *key)\n" in security:
        run("patch", "--batch", "--fuzz=0", "-p1", "-i", HERE / "build-prerequisite.patch", cwd=source)
    elif b"\nstatic int lsm_key_alloc(struct key *key)\n" not in security:
        raise RuntimeError("Unexpected lsm_key_alloc definition; inspect the source")
    run(*make, "bindeb-pkg", "KDEB_PKGVERSION=1", "KDEB_CHANGELOG_DIST=noble")
    release = (output / "include/config/kernel.release").read_text().strip()
    for filename in ("Makefile", "cephx_sru_crypto.c"):
        shutil.copyfile(HERE / filename, module / filename)
    # Full build retains Module.symvers, including CONFIG_MODVERSIONS CRCs.
    run(*make, f"M={module}", "modules")
    image = next(destination.glob(f"linux-image-{release}_*_amd64.deb"))
    evidence = module / "cephx_sru_crypto.ko"
    def artifact(path):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        return {"path": str(path), "sha256": digest}
    manifest = {"revision": revision, "SRU_EXPECTED_KERNEL": release,
                "image": artifact(image), "module": artifact(evidence),
                "config": artifact(output / ".config"), "case": "06",
                "build_prerequisite": artifact(HERE / "build-prerequisite.patch")}
    (destination / "artifacts.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Artifacts:", destination / "artifacts.json")


if __name__ == "__main__":
    main()

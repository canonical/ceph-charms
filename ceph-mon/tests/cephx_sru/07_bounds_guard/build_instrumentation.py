#!/usr/bin/env python3
"""Incrementally build NON-SHIPPING libceph in case 06's archived source/output."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent


def run(*args, **kwargs):
    print("+", *map(str, args), flush=True)
    subprocess.run(list(map(str, args)), check=True, **kwargs)


def artifact(path):
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path), "sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel-build", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=2)
    args = parser.parse_args()
    base, destination = args.kernel_build.resolve(), args.destination.resolve()
    source, output = base / "source", base / "output"
    manifest = json.loads((base / "artifacts.json").read_text())
    if manifest["case"] != "06" or args.jobs < 1:
        parser.error("Require a completed case-06 build and positive --jobs")
    if (source / ".git").exists() or (source / ".git").is_file():
        parser.error("Never patch a checkout or worktree; require the archived source")
    for name in ("image", "config"):
        if artifact(Path(manifest[name]["path"])) != manifest[name]:
            parser.error(f"Base {name} changed since case-06 build")
    # Refuse to patch anything except the exact auth_x.c from the source archive.
    with tarfile.open(base / "source.tar") as archive:
        original = archive.extractfile("net/ceph/auth_x.c").read()
    if (source / "net/ceph/auth_x.c").read_bytes() != original:
        parser.error("Archived auth_x.c is already modified; use a fresh case-06 build")
    destination.mkdir(parents=True, exist_ok=False)
    run("patch", "--batch", "--fuzz=0", "-p1", "-i", HERE / "bounds-instrumentation.patch", cwd=source)
    shutil.copyfile(HERE / "cephx_sru_bounds_test.inc", source / "net/ceph/cephx_sru_bounds_test.inc")
    make = ["make", "-C", source, f"O={output}", f"-j{args.jobs}", "KBUILD_BUILD_VERSION=1"]
    # A normal incremental modules build updates Module.symvers for the test export.
    run(*make, "modules")
    module = destination / "module"
    module.mkdir()
    for filename in ("Makefile", "cephx_sru_bounds.c"):
        shutil.copyfile(HERE / filename, module / filename)
    run(*make, f"M={module}", "modules")
    libceph = destination / "libceph.ko"
    shutil.copyfile(output / "net/ceph/libceph.ko", libceph)
    manifest.update({"case": "07", "module": artifact(module / "cephx_sru_bounds.ko"),
                     "libceph": artifact(libceph),
                     "instrumentation": artifact(HERE / "bounds-instrumentation.patch"),
                     "test_body": artifact(HERE / "cephx_sru_bounds_test.inc")})
    (destination / "artifacts.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Artifacts:", destination / "artifacts.json")


if __name__ == "__main__":
    main()

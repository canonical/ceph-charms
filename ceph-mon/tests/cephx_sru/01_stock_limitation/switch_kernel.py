#!/usr/bin/env python3
"""Select an already installed stock kernel, or restore the recorded candidate."""

import argparse
import json
from pathlib import Path
import re
import shlex
import time

from juju.errors import JujuError
import zaza
from zaza import model

from helpers import local, remote


# Run in the guest, where python3-apt exposes the signed archive metadata.
STOCK_PROVENANCE = r'''
import apt
import pathlib
import subprocess
import sys

release, version = sys.argv[1:]
cache = apt.Cache()
for name, source in ((f"linux-image-{release}", "linux-signed"),
                     (f"linux-modules-{release}", "linux")):
    installed = cache[name].installed
    assert installed is not None, f"{name} is not installed"
    assert installed.version == version, f"Unexpected version for {name}"
    assert installed.architecture == "amd64", f"Unexpected architecture for {name}"
    assert installed.source_name == source, f"Unexpected source for {name}"
    assert any(origin.trusted and origin.origin == "Ubuntu"
               and origin.archive in ("noble", "noble-updates", "noble-security")
               and origin.component == "main" for origin in installed.origins), \
        f"{name} has no trusted Ubuntu Noble archive provenance"
    verification = subprocess.check_output(["dpkg", "--verify", name], text=True)
    assert not verification.strip(), f"Installed {name} files differ from the package: {verification}"
    print(f"Ubuntu stock package: {name}={installed.version}; source={installed.source_name}")
modules = set(pathlib.Path(path).resolve() for path in subprocess.check_output(
    ["dpkg-query", "-L", f"linux-modules-{release}"], text=True).splitlines())
for module in ("rbd", "ceph", "libceph"):
    path = pathlib.Path(subprocess.check_output(
        ["modinfo", "-k", release, "-n", module], text=True).strip()).resolve()
    assert path in modules, f"{module} is not supplied by the stock modules package"
'''


def stock_provenance(run, release, version):
    """Reject candidate version suffixes, local packages and replaced modules."""
    if not re.fullmatch(r"6\.8\.0-([0-9]+)-generic", release):
        raise ValueError("The stock control requires an explicit Noble GA generic release")
    abi = release.removeprefix("6.8.0-").removesuffix("-generic")
    if not re.fullmatch(rf"6\.8\.0-{abi}\.[0-9]+", version):
        raise ValueError("Supply an unmodified Ubuntu GA package version, not a candidate build")
    return run("python3", "-c", STOCK_PROVENANCE, release, version, timeout=120)


def restart_client(target, release):
    """Use Juju's machine identity and LXC only to restart the client VM."""
    status = model.get_status(model_name=target)
    machine = status.applications["ubuntu"]["units"]["ubuntu/0"]["machine"]
    instance = status.machines[machine]["instance-id"]
    local("lxc", "--force-local", "--project", "default", "stop", instance,
          "--timeout=90", timeout=120)
    local("lxc", "--force-local", "--project", "default", "start", instance, timeout=300)
    model.block_until_machine_status_is(machine, "started", model_name=target, timeout=300)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            if remote(target, "ubuntu/0", "uname -r", timeout=15).strip() == release:
                return
        except (OSError, RuntimeError, TimeoutError, JujuError):
            pass
        time.sleep(3)
    raise RuntimeError(f"Client did not boot {release}; restore with the candidate environment")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("stock", "restore"))
    parser.add_argument("--environment", required=True, type=Path,
                        help="Path to environment.json created by setup_lab.py, not the stock-test record")
    parser.add_argument("--release", help="Exact installed stock uname -r release")
    parser.add_argument("--version", help="Exact installed Ubuntu stock package version")
    parser.add_argument("--output", type=Path, help="New stock environment JSON")
    args = parser.parse_args()
    expected = json.loads(args.environment.read_text())
    if expected.get("SRU_KERNEL_KIND") == "stock":
        parser.error(
            "--environment points to a stock-test record. For both 'stock' and "
            "'restore', pass the environment.json created by setup_lab.py; "
            "it identifies the candidate kernel to return to."
        )
    target = expected["model"]
    candidate = expected["SRU_EXPECTED_KERNEL"]
    if not re.fullmatch(r"[0-9][a-z0-9.+-]*", candidate):
        parser.error("Invalid candidate release")

    def run(*command, timeout=90):
        return remote(target, "ubuntu/0", shlex.join(str(arg) for arg in command),
                      timeout=timeout)

    if args.operation == "stock":
        if not all((args.release, args.version, args.output)):
            parser.error("stock requires --release, --version and --output")
        if args.output.exists() or args.output.resolve() == args.environment.resolve():
            parser.error("Stock output must be a new file, not the candidate environment")
        if args.release == candidate:
            parser.error("Stock and candidate releases must differ")
        stock_provenance(run, args.release, args.version)
        release = args.release
        stock = dict(expected)
        stock.update(SRU_EXPECTED_KERNEL=release, kernel_package=args.version,
                     SRU_KERNEL_KIND="stock", SRU_STOCK_KERNEL_VERSION=args.version,
                     SRU_CANDIDATE_KERNEL=candidate)
        stock.pop("image_sha256", None)
        stock.pop("modules_sha256", None)
        # Save before reboot so an interrupted switch still has an explicit record.
        args.output.write_text(json.dumps(stock, indent=2) + "\n")
    else:
        if args.release or args.version or args.output:
            parser.error("restore uses only --environment")
        release = candidate
        packages = run("dpkg-query", "-W", "-f=${Package}\\t${Version}\\n",
                       f"linux-image*{release}").splitlines()
        images = {tuple(line.split("\t")) for line in packages}
        version = expected["kernel_package"]
        if not any((name, version) in images for name in (
                f"linux-image-{release}", f"linux-image-unsigned-{release}")):
            raise RuntimeError("Installed candidate image does not match its recorded version")
        if run("dpkg-query", "-W", "-f=${Version}",
               f"linux-modules-{release}").strip() != version:
            raise RuntimeError("Installed candidate modules do not match their recorded version")
        run("test", "-f", f"/boot/vmlinuz-{release}")

    setting = f'GRUB_DEFAULT="Advanced options for Ubuntu>Ubuntu, with Linux {release}"\n'
    remote(target, "ubuntu/0",
           f"printf %s {shlex.quote(setting)} > /etc/default/grub.d/99-cephx-sru.cfg\nupdate-grub",
           timeout=120)
    restart_client(target, release)
    print(f"Client now runs {release}; candidate environment remains {args.environment}")


if __name__ == "__main__":
    try:
        main()
    finally:
        zaza.clean_up_libjuju_thread()

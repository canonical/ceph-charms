#!/usr/bin/env python3
"""Create a fresh, retained Juju lab for host-side CephX SRU tests."""

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import time
import uuid

from juju.controller import Controller
from juju.errors import JujuError
import yaml
import zaza
from zaza import controller as zaza_controller
from zaza import model

from helpers import local, pgs_active_clean, remote

HERE = Path(__file__).resolve().parent
VERSION = "19.2.6-0ubuntu0.24.04.1"
SOURCE = "ppa:johnramsden/noble-caracal-ceph-squid-sru"


async def create_and_deploy(controller_name, model_name, output,
                            version=VERSION, source=SOURCE):
    """Create on the named controller without switching the current model."""
    controller = Controller()
    lab = None
    try:
        await controller.connect(controller_name)
        if await controller.get_cloud() != "localhost":
            raise RuntimeError("Setup supports a controller on the local LXD cloud only")
        config = yaml.safe_load((HERE / "model-config.yaml").read_text())
        config["cloudinit-userdata"] = config["cloudinit-userdata"].replace(
            VERSION, version)
        bundle = yaml.safe_load((HERE / "bundle.yaml").read_text())
        for application in ("ceph-mon", "ceph-osd", "ceph-fs"):
            bundle["applications"][application]["options"]["source"] = source
        # A duplicate name must fail before deployment or any guest mutations.
        lab = await controller.add_model(model_name, config=config)
        output.mkdir(parents=True)
        with tempfile.TemporaryDirectory(prefix="cephx-sru-bundle-") as directory:
            bundle_path = Path(directory) / "bundle.yaml"
            bundle_path.write_text(yaml.safe_dump(bundle))
            await lab.deploy(str(bundle_path))
    finally:
        try:
            if lab is not None:
                await lab.disconnect()
        finally:
            await controller.disconnect()


def copy_admin_files(target):
    """Transfer credentials by SCP, never through controller-retained actions."""
    stage = f"/home/ubuntu/.cephx-sru-{uuid.uuid4().hex}"
    with tempfile.TemporaryDirectory(prefix="cephx-sru-") as directory, ExitStack() as cleanup:
        for unit in ("ceph-mon/0", "ubuntu/0"):
            cleanup.callback(remote, target, unit, shlex.join(["rm", "-rf", "--", stage]))
            remote(target, unit, shlex.join([
                "install", "-d", "-o", "ubuntu", "-g", "ubuntu", "-m", "700", stage,
            ]))
        for filename in ("ceph.conf", "ceph.client.admin.keyring"):
            source = f"/etc/ceph/{filename}"
            staged = f"{stage}/{filename}"
            destination = Path(directory) / filename
            remote(target, "ceph-mon/0", shlex.join([
                "install", "-o", "ubuntu", "-g", "ubuntu", "-m", "600", source, staged,
            ]))
            model.scp_from_unit("ceph-mon/0", staged, str(destination), model_name=target)
            destination.chmod(0o600)
            model.scp_to_unit("ubuntu/0", str(destination), staged, model_name=target)
            remote(target, "ubuntu/0", shlex.join([
                "install", "-o", "root", "-g", "root", "-m", "600", staged, source,
            ]))


def main(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--model", required=True, help="A new, disposable model")
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--modules", required=True, type=Path)
    parser.add_argument("--image-sha256", required=True)
    parser.add_argument("--modules-sha256", required=True)
    parser.add_argument("--ceph-version", default=VERSION)
    parser.add_argument("--ceph-source", default=SOURCE)
    args = parser.parse_args(arguments)
    if args.ceph_version not in (VERSION, "19.2.3-0ubuntu0.24.04.3"):
        parser.error("Only the pinned SRU and pre-fix Squid versions are supported")
    if args.ceph_source not in (SOURCE, "distro"):
        parser.error("Only the pinned SRU PPA or Ubuntu distro source is supported")
    for name in (args.controller, args.model):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            parser.error("Controller and model must be simple lowercase names")
    target = f"{args.controller}:{args.model}"
    output = Path.home() / "cephx-sru-labs" / f"{args.controller}-{args.model}"
    if output.exists():
        parser.error(f"Output already exists: {output}; choose a fresh model name")

    # Verify the supplied pair before creating a model or changing any guest.
    metadata = []
    for path, digest in ((args.image, args.image_sha256),
                         (args.modules, args.modules_sha256)):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
            parser.error("Expected SHA-256 values must contain 64 hexadecimal digits")
        with path.open("rb") as artifact:
            if hashlib.file_digest(artifact, "sha256").hexdigest() != digest.lower():
                parser.error(f"Artifact checksum mismatch: {path}")
        metadata.append(local(
            "dpkg-deb", "--show",
            "--showformat=${Package}\t${Version}\t${Architecture}\n",
            str(path.resolve()),
        ).strip().split("\t"))
    image, modules = metadata
    if len(image) != 3 or len(modules) != 3:
        parser.error("Kernel packages must have package, version and architecture metadata")
    release = modules[0].removeprefix("linux-modules-")
    if (modules[0] != f"linux-modules-{release}"
            or not re.fullmatch(r"[0-9][a-z0-9.+-]*", release)
            or image[0] not in (f"linux-image-{release}", f"linux-image-unsigned-{release}")
            or image[1:] != modules[1:] or image[2] != "amd64"):
        parser.error("Supply matching amd64 kernel image and modules packages")

    zaza.sync_wrapper(create_and_deploy, timeout=900)(
        args.controller, args.model, output, args.ceph_version, args.ceph_source)
    # Public Zaza cache refresh: libjuju add_model leaves models.yaml unchanged.
    previous = {name: os.environ.get(name) for name in ("JUJU_CONTROLLER", "JUJU_MODEL")}
    try:
        os.environ["JUJU_CONTROLLER"] = args.controller
        # A caller's qualified JUJU_MODEL must not select a different controller.
        os.environ.pop("JUJU_MODEL", None)
        zaza_controller.go_list_models()
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    model.wait_for_application_states(
        model_name=target, timeout=1800,
        states={
            application: {
                "num-expected-units": count,
                "workload-status-message-regex": ".*",
            }
            for application, count in (
                ("ceph-mon", 1), ("ceph-osd", 2), ("ceph-fs", 1), ("ubuntu", 1),
            )
        },
    )

    def guest(unit, script, timeout=600):
        return remote(target, unit, script, timeout=timeout)

    # Package selection is pinned in cloud-init before the charms install Ceph.
    for unit, packages in (("ceph-mon/0", "ceph-mon ceph-mgr ceph-common"),
                           ("ceph-osd/0", "ceph-osd ceph-common"),
                           ("ceph-osd/1", "ceph-osd ceph-common"),
                           ("ceph-fs/0", "ceph-mds ceph-common")):
        versions = guest(unit, "dpkg-query -W -f='${Version}\\n' " + packages)
        if any(version != args.ceph_version for version in versions.splitlines()):
            raise RuntimeError(f"Wrong Ceph packages on {unit}")
    repository = (f"add-apt-repository -y {args.ceph_source}"
                  if args.ceph_source != "distro" else "apt-get update")
    guest("ubuntu/0", f"""
export DEBIAN_FRONTEND=noninteractive
{repository}
apt-get install -y --no-install-recommends --no-remove ceph-common={args.ceph_version}
""")

    # This identity comes from the model just created, not an arbitrary cluster.
    fsid = guest("ceph-mon/0", "ceph fsid").strip()
    if args.ceph_version == VERSION:
        guest("ceph-mon/0", """
ceph mon set auth_allowed_ciphers aes,aes256k
ceph config set mon mon_auth_allow_insecure_key true
""")
    guest("ceph-mon/0", """
ceph config set mon auth_allow_insecure_global_id_reclaim false
ceph osd pool create sru-rbd 8 8
rbd pool init sru-rbd
""")
    copy_admin_files(target)

    # Only the dedicated client receives the candidate packages.
    for path, filename in ((args.image, "image.deb"), (args.modules, "modules.deb")):
        model.scp_to_unit("ubuntu/0", str(path.resolve()), f"/home/ubuntu/{filename}",
                          model_name=target)
    guest("ubuntu/0", f"""
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends --no-remove /home/ubuntu/image.deb /home/ubuntu/modules.deb
printf '%s\\n' 'GRUB_DEFAULT="Advanced options for Ubuntu>Ubuntu, with Linux {release}"' > /etc/default/grub.d/99-cephx-sru.cfg
update-grub
""")
    status = model.get_status(model_name=target)
    machine = status.applications["ubuntu"]["units"]["ubuntu/0"]["machine"]
    instance = status.machines[machine]["instance-id"]
    local("lxc", "--force-local", "--project", "default", "stop", instance,
          "--timeout=90", timeout=120)
    local("lxc", "--force-local", "--project", "default", "config", "set", instance,
          "security.secureboot=false")
    local("lxc", "--force-local", "--project", "default", "start", instance, timeout=300)
    model.block_until_machine_status_is(machine, "started", model_name=target, timeout=300)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            if guest("ubuntu/0", "uname -r", timeout=15).strip() == release:
                break
        except (OSError, RuntimeError, TimeoutError, JujuError):
            pass
        time.sleep(3)
    else:
        raise RuntimeError("Client did not boot the candidate kernel within 300 seconds")

    # The new model is dedicated to this run: wait for its pools and filesystem.
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        pgs = json.loads(guest("ceph-mon/0", "ceph pg ls -f json"))["pg_stats"]
        fs = json.loads(guest("ceph-mon/0", "ceph fs get ceph-fs -f json"))["mdsmap"]
        if (pgs_active_clean(pgs)
                and any(mds["state"] == "up:active" for mds in fs["info"].values())):
            break
        time.sleep(3)
    else:
        raise RuntimeError("The test pools and filesystem did not become ready")

    # One informational record, not a registry, receipt, or resumable state machine.
    environment = output / "environment.json"
    environment.write_text(json.dumps({
        "model": target, "SRU_EXPECTED_KERNEL": release, "SRU_EXPECTED_FSID": fsid,
        "SRU_CEPH_PACKAGE": args.ceph_version, "SRU_RBD_POOL": "sru-rbd", "SRU_CEPHFS": "ceph-fs",
        "SRU_DEPLOYED_CEPH_PACKAGE": args.ceph_version,
        "SRU_DEPLOYED_CEPH_SOURCE": args.ceph_source,
        "kernel_package": image[1], "image_sha256": args.image_sha256,
        "modules_sha256": args.modules_sha256,
    }, indent=2) + "\n")
    print(f"Environment: {environment}; model retained for host-side tests", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        raise SystemExit(f"Setup failed: {error}\nAny created model is retained; inspect it or destroy and recreate it.")
    finally:
        zaza.clean_up_libjuju_thread()

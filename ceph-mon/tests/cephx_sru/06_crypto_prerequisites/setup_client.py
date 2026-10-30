#!/usr/bin/env python3
"""Boot one disposable Juju amd64 VM with a case-06/07 test kernel, no Ceph."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import time

from juju.controller import Controller
from juju.errors import JujuError
import zaza
from zaza import controller as zaza_controller
from zaza import model

from helpers import local, remote


async def deploy(controller_name, model_name):
    controller, lab = Controller(), None
    try:
        await controller.connect(controller_name)
        if await controller.get_cloud() != "localhost":
            raise RuntimeError("Only the local LXD cloud is supported")
        lab = await controller.add_model(model_name)
        await lab.deploy("ch:ubuntu", application_name="ubuntu", channel="latest/stable",
                         revision=79, series="noble", num_units=1,
                         constraints="arch=amd64 cores=2 mem=4G root-disk=16G virt-type=virtual-machine")
    finally:
        if lab is not None:
            await lab.disconnect()
        await controller.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--model", required=True, help="A fresh disposable model")
    parser.add_argument("--artifacts", type=Path, required=True)
    args = parser.parse_args()
    for name in (args.controller, args.model):
        if not re.fullmatch(r"[a-z][a-z0-9-]*", name):
            parser.error("Controller and model must be simple lowercase names")
    artifacts = json.loads(args.artifacts.read_text())
    image = Path(artifacts["image"]["path"])
    with image.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != artifacts["image"]["sha256"]:
            parser.error("Kernel image checksum mismatch")
    release = artifacts["SRU_EXPECTED_KERNEL"]
    if not re.fullmatch(r"[0-9][a-z0-9.+-]*", release) or "cephx-sru06" not in release:
        parser.error("Require the explicitly non-shipping test kernel release")
    metadata = local("dpkg-deb", "--show", "--showformat=${Package}\t${Architecture}\n", image).strip()
    if metadata != f"linux-image-{release}\tamd64":
        parser.error("Expected the matching upstream-built amd64 image including modules")
    target = f"{args.controller}:{args.model}"
    output = Path.home() / "cephx-sru-labs" / f"{args.controller}-{args.model}"
    output.mkdir(parents=True, exist_ok=False)
    zaza.sync_wrapper(deploy, timeout=900)(args.controller, args.model)
    previous = {key: os.environ.get(key) for key in ("JUJU_CONTROLLER", "JUJU_MODEL")}
    try:
        os.environ["JUJU_CONTROLLER"] = args.controller
        os.environ.pop("JUJU_MODEL", None)
        zaza_controller.go_list_models()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    model.wait_for_application_states(model_name=target, timeout=1800,
                                      states={"ubuntu": {"num-expected-units": 1,
                                                         "workload-status-message-regex": ".*"}})
    model.scp_to_unit("ubuntu/0", str(image), "/home/ubuntu/cephx-test-kernel.deb", model_name=target)
    remote(target, "ubuntu/0", f"""
export DEBIAN_FRONTEND=noninteractive
apt-get install -y --no-install-recommends --no-remove /home/ubuntu/cephx-test-kernel.deb
printf '%s\\n' {shlex.quote('GRUB_DEFAULT="Advanced options for Ubuntu>Ubuntu, with Linux ' + release + '"')} > /etc/default/grub.d/99-cephx-sru.cfg
update-grub
""", timeout=900)
    status = model.get_status(model_name=target)
    machine = status.applications["ubuntu"]["units"]["ubuntu/0"]["machine"]
    instance = status.machines[machine]["instance-id"]
    local("lxc", "--force-local", "--project", "default", "stop", instance, "--timeout=90", timeout=120)
    local("lxc", "--force-local", "--project", "default", "config", "set", instance, "security.secureboot=false")
    local("lxc", "--force-local", "--project", "default", "start", instance, timeout=300)
    model.block_until_machine_status_is(machine, "started", model_name=target, timeout=300)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        try:
            if remote(target, "ubuntu/0", "uname -r", timeout=15).strip() == release:
                break
        except (OSError, RuntimeError, TimeoutError, JujuError):
            pass
        time.sleep(3)
    else:
        raise RuntimeError("Client did not boot the supplied test kernel")
    environment = output / "environment.json"
    environment.write_text(json.dumps({"model": target, "SRU_EXPECTED_KERNEL": release,
                                       "SRU_KERNEL_ARTIFACTS": str(args.artifacts.resolve())}, indent=2) + "\n")
    print("Environment:", environment, "; model retained")


if __name__ == "__main__":
    try:
        main()
    finally:
        zaza.clean_up_libjuju_thread()

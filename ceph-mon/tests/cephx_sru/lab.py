"""Read-only candidate-lab prerequisites shared by host-side SRU test classes."""

import json
import os
from pathlib import Path
import shlex
import unittest

import zaza

from helpers import ADMIN, remote


class LabTestCase(unittest.TestCase):
    """Use an explicitly recorded lab, never the operator's current Juju model."""

    @classmethod
    def script(cls, command, timeout=90):
        return remote(cls.expected["model"], "ubuntu/0", command, timeout=timeout)

    @classmethod
    def run_command(cls, *args, timeout=90):
        return cls.script(shlex.join(str(arg) for arg in args), timeout=timeout)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Zaza owns a background event loop; close it even when setup fails.
        cls.addClassCleanup(zaza.clean_up_libjuju_thread)
        cls.expected = json.loads(Path(os.environ["SRU_ENVIRONMENT"]).read_text())
        for field in ("model", "SRU_EXPECTED_KERNEL", "SRU_EXPECTED_FSID",
                      "SRU_CEPH_PACKAGE", "SRU_RBD_POOL", "SRU_CEPHFS"):
            assert cls.expected.get(field), f"Missing expected lab field: {field}"
        cls.pool = cls.expected["SRU_RBD_POOL"]
        cls.filesystem = cls.expected["SRU_CEPHFS"]
        run = cls.run_command

        kernel = run("uname", "-r").strip()
        assert kernel == cls.expected["SRU_EXPECTED_KERNEL"], "Wrong client kernel"
        release = run("python3", "-c", "import platform; print(platform.freedesktop_os_release()['VERSION_ID'])")
        assert release.strip() == "24.04", "The client must run Ubuntu 24.04"
        for module in ("rbd", "libceph", "ceph"):
            vermagic = run("modinfo", "-F", "vermagic", module).split()[0]
            assert vermagic == kernel, f"{module} does not match the client kernel"
        package = run("dpkg-query", "-W", "-f=${Version}", "ceph-common").strip()
        assert package == cls.expected["SRU_CEPH_PACKAGE"], "Wrong client package"
        assert run("ceph", "--version").startswith("ceph version 19.2.6 ")

        status = json.loads(run("ceph", *ADMIN, "status", "--format", "json"))
        assert status["fsid"] == cls.expected["SRU_EXPECTED_FSID"], "Wrong cluster"
        versions = json.loads(run("ceph", *ADMIN, "versions", "--format", "json"))
        for daemon in ("mon", "mgr", "osd", "mds"):
            assert versions.get(daemon), f"No running {daemon} version evidence"
        assert versions["overall"] and all(
            version.startswith("ceph version 19.2.6 ") for version in versions["overall"]
        ), "Not all running daemons are Ceph 19.2.6"

        filesystem = json.loads(run("ceph", *ADMIN, "fs", "get", cls.filesystem,
                                    "--format", "json"))["mdsmap"]
        assert filesystem["fs_name"] == cls.filesystem
        assert filesystem["in"] and any(
            mds["state"] == "up:active" for mds in filesystem["info"].values()
        ), "The test filesystem must have an active MDS"
        monmap = json.loads(run("ceph", *ADMIN, "mon", "dump", "--format", "json"))
        osds = json.loads(run("ceph", *ADMIN, "osd", "ls", "--format", "json"))
        daemons = [f"mon.{mon['name']}" for mon in monmap["mons"]]
        daemons += [f"osd.{osd}" for osd in osds]
        daemons += [f"mds.{mds['name']}" for mds in filesystem["info"].values()]
        for daemon in daemons:
            auth = run("ceph", *ADMIN, "config", "show", daemon,
                       "auth_service_required").strip()
            assert auth == "cephx", f"{daemon} does not require CephX authentication"

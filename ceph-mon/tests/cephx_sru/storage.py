"""Small storage lifecycles shared by the numbered host-side SRU cases."""

from contextlib import ExitStack, contextmanager
import hashlib
import json
import shlex
from uuid import uuid4

from helpers import ADMIN, CONF


EXPECTED_SHA256 = hashlib.sha256(bytes(range(256)) * 4096).hexdigest()
PAYLOAD_PROGRAM = (
    "import pathlib, sys; "
    "pathlib.Path(sys.argv[1]).write_bytes(bytes(range(256)) * 4096)"
)


class _Payload:
    def __init__(self, test, kind, key_type, cleanup):
        self.test = test
        self.run = test.run_command
        self.name = f"sru-{kind}-{key_type}-{uuid4().hex}"
        self.entity = f"client.{self.name}"
        self.private = self.run("mktemp", "-d", f"/tmp/cephx-sru-{kind}.XXXXXXXXXX").strip()
        cleanup.callback(self.run, "rmdir", "--", self.private)
        test.assertEqual(self.run("stat", "-c", "%u:%a", self.private).strip(), "0:700")
        self.keyring = f"{self.private}/client.keyring"
        self.expected = f"{self.private}/expected.bin"
        self.actual = f"{self.private}/actual.bin"
        self.run("install", "-m", "600", "/dev/null", self.keyring)
        cleanup.callback(self.run, "rm", "-f", "--", self.keyring, self.expected, self.actual)
        self.mode = None

    def write(self):
        self.run("python3", "-c", PAYLOAD_PROGRAM, self.expected)
        self.run("dd", f"if={self.expected}", f"of={self.path}", "bs=4096",
                 "count=256", "oflag=direct", "conv=fsync", "status=none")

    def read_compare(self):
        self.run("dd", f"if={self.path}", f"of={self.actual}", "bs=4096",
                 "count=256", "iflag=direct,fullblock", "status=none")
        self.run("cmp", "--", self.expected, self.actual)
        self.test.assertEqual(self.run("sha256sum", "--", self.actual).split()[0],
                              EXPECTED_SHA256, "Kernel storage readback differs")


class RBDVolume(_Payload):
    def __init__(self, test, key_type, cleanup):
        super().__init__(test, "rbd", key_type, cleanup)
        self.device = None
        self.image = f"{test.pool}/{self.name}"
        self.run("ceph", *ADMIN, "auth", "get-or-create", self.entity,
                 "--key-type", key_type, "mon", "profile rbd",
                 "osd", f"profile rbd pool={test.pool}", "-o", self.keyring)
        cleanup.callback(self.run, "ceph", *ADMIN, "auth", "del", self.entity)
        self.run("rbd", *ADMIN, "create", self.image, "--size", "16M",
                 "--image-format", "2", "--image-feature", "layering")
        cleanup.callback(self.run, "rbd", *ADMIN, "rm", self.image)
        cleanup.callback(self.disconnect)

    def connect(self, mode="crc"):
        if self.device is not None:
            raise RuntimeError("Disconnect the RBD device before remapping")
        self.device = self.run(
            "rbd", "--conf", CONF, "--name", self.entity, "--keyring", self.keyring,
            "device", "map", "--device-type", "krbd", "--options",
            f"noshare,ms_mode={mode},mount_timeout=30", self.image,
        ).strip()
        self.path = self.device
        self.mode = mode
        self.run("test", "-b", self.device)

    def disconnect(self):
        if self.device is not None:
            self.run("rbd", "device", "unmap", "--device-type", "krbd", self.device)
            self.device = None


class CephFSVolume(_Payload):
    def __init__(self, test, key_type, cleanup):
        super().__init__(test, "cephfs", key_type, cleanup)
        self.connected = False
        self.directory_created = False
        self.secret = f"{self.private}/client.secret"
        self.mountpoint = f"{self.private}/cephfs"
        self.directory = f"{self.mountpoint}/{self.name}"
        self.path = f"{self.directory}/payload.bin"
        self.source = f"{self.name}@{test.expected['SRU_EXPECTED_FSID']}.{test.filesystem}=/"
        self.run("install", "-m", "600", "/dev/null", self.secret)
        cleanup.callback(self.run, "rm", "-f", "--", self.secret)
        self.run("ceph", *ADMIN, "auth", "get-or-create", self.entity,
                 "--key-type", key_type, "mon", f"allow r fsname={test.filesystem}",
                 "mds", f"allow rw fsname={test.filesystem}",
                 "osd", f"allow rw tag cephfs data={test.filesystem}", "-o", self.keyring)
        cleanup.callback(self.run, "ceph", *ADMIN, "auth", "del", self.entity)
        test.script(shlex.join(["ceph-authtool", self.keyring, "--name", self.entity,
                                "--print-key"]) + " > " + shlex.quote(self.secret))
        self.run("mkdir", "--", self.mountpoint)
        cleanup.callback(self.run, "rmdir", "--", self.mountpoint)
        cleanup.callback(self.disconnect)
        cleanup.callback(self.remove_payload)

    def connect(self, mode="crc"):
        if self.connected:
            raise RuntimeError("Unmount CephFS before remounting")
        self.run("mount.ceph", self.source, self.mountpoint, "-o",
                 f"conf={CONF},secretfile={self.secret},noshare,ms_mode={mode},mount_timeout=30")
        self.connected = True
        self.mode = mode
        mounts = json.loads(self.run("findmnt", "--mountpoint", self.mountpoint,
                                     "--json", "--output", "TARGET,SOURCE,FSTYPE"))["filesystems"]
        self.test.assertEqual(mounts, [{"target": self.mountpoint, "source": self.source,
                                       "fstype": "ceph"}])
        if not self.directory_created:
            self.run("mkdir", "--", self.directory)
            self.directory_created = True

    def disconnect(self):
        if self.connected:
            self.run("umount", self.mountpoint)
            self.connected = False

    def remove_payload(self):
        if self.directory_created:
            if not self.connected:
                self.connect(self.mode)
            self.run("rm", "-f", "--", self.path)
            self.run("rmdir", "--", self.directory)
            self.directory_created = False


@contextmanager
def rbd_volume(test, key_type):
    with ExitStack() as cleanup:
        yield RBDVolume(test, key_type, cleanup)


@contextmanager
def cephfs_volume(test, key_type):
    with ExitStack() as cleanup:
        yield CephFSVolume(test, key_type, cleanup)

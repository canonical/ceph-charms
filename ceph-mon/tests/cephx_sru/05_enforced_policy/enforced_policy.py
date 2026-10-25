"""Destructive policy cutover on a recorded fresh or genuinely upgraded lab."""

from contextlib import ExitStack
import json
from uuid import uuid4

from helpers import ADMIN, RemoteCommandError
from lab import LabTestCase
from storage import RBDVolume, cephfs_volume, rbd_volume


# Keep the key and complete mapping request inside the guest. Unlike the rbd
# CLI, sysfs enters the kernel without authenticating through librados first.
KERNEL_RBD_MAP = """
import configparser, pathlib, sys
keyring, entity, monitors, pool, image = sys.argv[1:]
keys = configparser.ConfigParser(interpolation=None)
keys.read(keyring)
key = keys.get(entity, 'key')
options = f'name={entity.removeprefix("client.")},secret={key},noshare,ms_mode=crc,mount_timeout=30'
pathlib.Path('/sys/bus/rbd/add_single_major').write_text(f'{monitors} {options} {pool} {image}')
devices = [p for p in pathlib.Path('/sys/bus/rbd/devices').iterdir()
           if (p / 'name').read_text().strip() == image
           and (p / 'pool').read_text().strip() == pool]
assert len(devices) == 1, 'Expected one kernel mapping for the scoped image'
print('/dev/rbd' + devices[0].name)
"""


class EnforcedPolicyTest(LabTestCase):
    def _connect(self, volume):
        if not isinstance(volume, RBDVolume):
            volume.connect()
            return
        self.assertIsNone(volume.device, "Disconnect before remapping")
        volume.device = self.run_command(
            "python3", "-c", KERNEL_RBD_MAP, volume.keyring, volume.entity,
            self.monitors, self.pool, volume.name).strip()
        volume.path = volume.device
        volume.mode = "crc"
        self.run_command("udevadm", "settle")
        self.run_command("test", "-b", volume.device)

    def test_enforced_policy(self):
        origin = self.expected.get("SRU_POLICY_ORIGIN")
        self.assertIn(origin, ("fresh", "upgraded"),
                      "Run 05_enforced_policy/setup_policy.py first")
        self.assertEqual(self.expected.get("SRU_POLICY_ROTATED"), True)
        if origin == "upgraded":
            before = self.expected["SRU_POLICY_BEFORE"]
            self.assertEqual(before["package"], "19.2.3-0ubuntu0.24.04.3")
            self.assertEqual(before["fsid"], self.expected["SRU_EXPECTED_FSID"])
            self.assertTrue(before["versions"]["overall"])
            self.assertTrue(all(version.startswith("ceph version 19.2.3 ")
                                for version in before["versions"]["overall"]))
        monmap = json.loads(self.run_command("ceph", *ADMIN, "mon", "dump", "-f", "json"))
        self.monitors = ",".join(addr["addr"] for mon in monmap["mons"]
                                 for addr in mon["public_addrs"]["addrvec"] if addr["type"] == "v2")
        self.assertTrue(self.monitors, "Require messenger-v2 monitor addresses")
        self.run_command("modprobe", "rbd")
        # Four independent, scoped credentials are created before enforcement.
        # Successful baseline I/O proves a later failure is not bad caps/storage.
        with ExitStack() as cleanup:
            legacy = [cleanup.enter_context(factory(self, "aes"))
                      for factory in (rbd_volume, cephfs_volume)]
            secure = [cleanup.enter_context(factory(self, "aes256k"))
                      for factory in (rbd_volume, cephfs_volume)]
            for volume in legacy + secure:
                self._connect(volume)
                volume.write()
                volume.read_compare()
                if volume in legacy and hasattr(volume, "remove_payload"):
                    volume.remove_payload()
                volume.disconnect()
            self.run_command("ceph", *ADMIN, "mon", "set",
                             "auth_allowed_ciphers", "aes256k")
            self.run_command("ceph", *ADMIN, "config", "set", "mon",
                             "mon_auth_allow_insecure_key", "false")
            monmap = json.loads(self.run_command("ceph", *ADMIN, "mon", "dump",
                                                "--format", "json"))
            self.assertEqual([cipher["name"] for cipher in monmap["auth_allowed_ciphers"]],
                             ["aes256k"])
            self.assertEqual(monmap["auth_service_cipher"]["name"], "aes256k")
            for volume in legacy:
                marker = f"CEPHX_SRU05_REJECT_{uuid4().hex}"
                self.script(f"printf '%s\\n' {marker} > /dev/kmsg")
                with self.assertRaises(RemoteCommandError) as caught:
                    self._connect(volume)
                error = caught.exception
                self.assertNotIn(str(error.code), ("124", "137"),
                                 "A timeout is not proof of authentication rejection")
                kernel = self.run_command(
                    "python3", "-c",
                    "import subprocess, sys; "
                    "print(subprocess.check_output(['dmesg', '--raw'], text=True).split(sys.argv[1], 1)[1])",
                    marker)
                self.assertRegex(kernel, r"libceph: auth protocol 'cephx' msgr authentication failed: -13",
                                 "Require fresh kernel authentication rejection, not userspace failure")
                if hasattr(volume, "device"):
                    self.assertIsNone(volume.device)
                    mappings = json.loads(self.run_command(
                        "rbd", "device", "list", "--format", "json"))
                    self.assertFalse(any(item["image"] == volume.name for item in mappings),
                                     "Rejected RBD image nevertheless has a kernel mapping")
                else:
                    self.assertFalse(volume.connected)
                    with self.assertRaises(RemoteCommandError) as not_mounted:
                        self.run_command("findmnt", "--mountpoint", volume.mountpoint,
                                         "--noheadings")
                    self.assertEqual(str(not_mounted.exception.code), "1")
                    self.assertFalse(not_mounted.exception.stdout.strip())
            for volume in secure:
                # Reconnect forces new authentication and reads the pre-cutover data.
                self._connect(volume)
                volume.read_compare()
                volume.write()
                volume.read_compare()
                volume.disconnect()
            status = json.loads(self.run_command("ceph", *ADMIN, "status", "-f", "json"))
            self.assertEqual(status["fsid"], self.expected["SRU_EXPECTED_FSID"])
            self.assertEqual(status["osdmap"]["num_up_osds"],
                             status["osdmap"]["num_osds"])
            self.assertGreater(status["osdmap"]["num_osds"], 0)

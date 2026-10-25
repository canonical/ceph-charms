"""Bullet 03: aes256k I/O with live, identity-correlated messenger evidence."""

import json

from helpers import ADMIN
from lab import LabTestCase
from storage import cephfs_volume, rbd_volume


# client_options hides the secret in the kernel; only the matching client is read.
KERNEL_CLIENT = """
import json, pathlib, sys
name, fsid = sys.argv[1:]
clients = []
for directory in pathlib.Path('/sys/kernel/debug/ceph').glob(f'{fsid}.client*'):
    options = (directory / 'client_options').read_text().strip().split(',')
    if f'name={name}' in options:
        clients.append({'directory': directory.name, 'options': options})
print(json.dumps(clients))
"""


class MessengerTest(LabTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.run_command("mountpoint", "-q", "/sys/kernel/debug")
        # Discover daemon IDs from the running cluster: mon dump lists monitors,
        # osd ls lists data daemons, and fs get lists this filesystem's MDSs.
        monmap = json.loads(cls.run_command("ceph", *ADMIN, "mon", "dump",
                                             "--format", "json"))
        assert "aes256k" in {
            cipher["name"] for cipher in monmap["auth_allowed_ciphers"]
        }, "The cluster must allow aes256k credentials"
        osds = json.loads(cls.run_command("ceph", *ADMIN, "osd", "ls",
                                          "--format", "json"))
        mdsmap = json.loads(cls.run_command("ceph", *ADMIN, "fs", "get",
                                            cls.filesystem, "--format", "json"))["mdsmap"]
        # Each pair is (daemon ID, client-facing messenger instance name).
        # Daemon IDs come from the maps above; instance names are fixed by
        # Ceph 19.2.6's dump interface. For example, ("osd.0", "client") queries
        # OSD 0's client-facing transport, not its daemon-to-daemon transport.
        # These instance names are unrelated to the legacy/crc/secure modes.
        cls.daemons = [(f"mon.{mon['name']}", "mon") for mon in monmap["mons"]]
        cls.daemons += [(f"osd.{osd}", "client") for osd in osds]
        cls.daemons += [(f"mds.{mds['name']}", "mds")
                        for mds in mdsmap["info"].values()
                        if mds["state"] == "up:active"]

    def test_rbd_legacy(self):
        self._compare(rbd_volume, "legacy", {"mon", "osd"})

    def test_rbd_crc(self):
        self._compare(rbd_volume, "crc", {"mon", "osd"})

    def test_rbd_secure(self):
        self._compare(rbd_volume, "secure", {"mon", "osd"})

    def test_cephfs_legacy(self):
        self._compare(cephfs_volume, "legacy", {"mon", "osd", "mds"})

    def test_cephfs_crc(self):
        self._compare(cephfs_volume, "crc", {"mon", "osd", "mds"})

    def test_cephfs_secure(self):
        self._compare(cephfs_volume, "secure", {"mon", "osd", "mds"})

    def _compare(self, volume, mode, required_peers):
        # The six test methods above explicitly supply mode and required_peers;
        # we are testing those choices, not discovering modes from the cluster.
        # volume is the RBD or CephFS context manager: it creates a fresh
        # identity/storage resource and cleans them up after these checks.
        # aes256k selects the authentication key type; mode independently
        # selects how the messenger transports messages.
        with volume(self, "aes256k") as resource:
            # connect passes mode as this mapping/mount's ms_mode; it does
            # not alter cluster policy, which must permit the requested mode.
            resource.connect(mode)
            # A successful mount/map alone does not prove data access works.
            # Keep this client connected after I/O so we can inspect its sessions.
            resource.write()
            resource.read_compare()
            # KERNEL_CLIENT reads debugfs, the kernel's diagnostic filesystem.
            # Match the cluster ID (FSID) and fresh credential name so another
            # cluster, mount, or mapping cannot supply evidence for this test.
            clients = json.loads(self.run_command(
                "python3", "-c", KERNEL_CLIENT, resource.name,
                self.expected["SRU_EXPECTED_FSID"],
            ))
            self.assertEqual(len(clients), 1,
                             "Need one live kernel client for this scoped identity")
            client = clients[0]
            # The directory is named <fsid>.client<global_id>. The monitor
            # assigns global_id to this authenticated client; every daemon
            # reports that same ID for its connections to this client.
            # Unlike the credential name, it distinguishes separate clients
            # using the same credentials. It is not a PID or RBD device number.
            global_id = int(client["directory"].rsplit(".client", 1)[1])
            # Check effective kernel state, not just the requested options.
            # With nocephx_sign_messages absent (NOMSGSIGN clear), the v1
            # handshake requires MSG_AUTH, the feature used for message signing.
            self.assertNotIn("nocephx_sign_messages", client["options"],
                             "Kernel CephX signing must remain enabled")
            seen = set()
            # self.daemons comes from setUpClass, not from the requested mode.
            # The ID prefix gives the role: mon = monitor/authentication,
            # osd = object data, mds = filesystem metadata. RBD needs mon/osd;
            # CephFS also needs mds. required_peers selects those roles.
            for daemon, messenger in self.daemons:
                kind = daemon.split(".", 1)[0]
                if kind not in required_peers:
                    continue
                dump = json.loads(self.run_command(
                    "ceph", *ADMIN, "tell", daemon, "messenger", "dump",
                    messenger, "connections", "anon_conns", "--format", "json",
                ))["messenger"]
                # Dumps have two connection collections. Include anon_conns
                # because monitor client sessions can appear only there.
                # The collection name alone says nothing about authentication.
                connections = [entry["async_connection"] for entry in dump["connections"]]
                connections += dump["anon_conns"]
                for connection in connections:
                    # Ignore daemon-to-daemon traffic, other clients, and stale
                    # disconnected entries before inspecting protocol evidence.
                    peer = connection["peer"]
                    if (peer["type"] != "client" or peer["global_id"] != global_id
                            or not connection["status"]["connected"]):
                        continue
                    # Monitor reports can omit the credential name even for
                    # authenticated sessions, so global_id is our shared key.
                    # OSD/MDS reports also name the identity; check both there.
                    if kind != "mon":
                        self.assertEqual(peer["entity_name"]["id"], resource.name,
                                         "Daemon identity does not match the kernel client")
                    protocol = connection["protocol"]
                    print(f"{resource.entity} global_id={global_id} {daemon} "
                          f"{messenger}: {json.dumps(protocol, sort_keys=True)}", flush=True)
                    # protocol describes the live negotiated version: legacy
                    # must use v1, while crc/secure must use v2. An established
                    # connection is called OPENED in v1 and READY in v2.
                    if mode == "legacy":
                        self.assertEqual(set(protocol), {"v1"},
                                         "Requested legacy but did not negotiate messenger v1")
                        self.assertEqual(protocol["v1"]["state"], "OPENED")
                        # Together with the client's signing requirement and
                        # completed I/O, this establishes v1 signing/checking;
                        # it is not a capture of individual message signatures.
                        signing = self.run_command(
                            "ceph", *ADMIN, "config", "show", daemon,
                            "cephx_sign_messages",
                        ).strip()
                        self.assertEqual(signing, "true",
                                         f"{daemon} has disabled v1 signing/checking")
                    else:
                        # READY means the handshake completed. con_mode is the
                        # daemon's negotiated mode, not our requested ms_mode.
                        self.assertEqual(set(protocol), {"v2"})
                        self.assertEqual(protocol["v2"]["state"], "READY")
                        self.assertEqual(protocol["v2"]["con_mode"], mode,
                                         "Daemon reports a different negotiated mode")
                        # crypto.rx/tx are receive/transmit ciphers as seen by
                        # this daemon. crc still authenticates with CephX but
                        # carries plaintext payloads (PLAIN); secure must
                        # encrypt traffic in both directions.
                        for direction in ("rx", "tx"):
                            cipher = protocol["v2"]["crypto"][direction]
                            if mode == "crc":
                                self.assertEqual(cipher, "PLAIN")
                            else:
                                self.assertTrue(cipher, "Missing live cipher evidence")
                                self.assertNotEqual(cipher, "PLAIN",
                                                    "Secure mode lacks an active cipher")
                    seen.add(kind)
            # Require evidence for every role, not every daemon: this workload
            # need not contact every OSD. Without this check, an empty/mismatched
            # dump could skip all assertions above and incorrectly pass.
            self.assertEqual(seen, required_peers,
                             "Missing live identity-correlated daemon connection evidence")

"""Bullet 03: aes256k I/O with live, identity-correlated messenger evidence."""

import json

from helpers import ADMIN
from lab import LabTestCase
from storage import cephfs_volume, rbd_volume


# client_options hides the secret in the kernel; only the matching client
# is read.
KERNEL_CLIENT = """
import json, pathlib, sys
name, fsid = sys.argv[1:]
clients = []
root = pathlib.Path('/sys/kernel/debug/ceph')
for directory in root.glob(f'{fsid}.client*'):
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
        # Discover running monitor, OSD and filesystem MDS identifiers.
        monmap = json.loads(
            cls.run_command("ceph", *ADMIN, "mon", "dump", "--format", "json")
        )
        assert "aes256k" in {
            cipher["name"] for cipher in monmap["auth_allowed_ciphers"]
        }, "The cluster must allow aes256k credentials"
        # V1 signing uses the session key, not the client's credential.
        assert (
            monmap["auth_service_cipher"]["name"] == "aes256k"
        ), "Require aes256k service tickets to exercise stronger-key signing"
        osds = json.loads(
            cls.run_command("ceph", *ADMIN, "osd", "ls", "--format", "json")
        )
        mdsmap = json.loads(
            cls.run_command(
                "ceph", *ADMIN, "fs", "get", cls.filesystem, "--format", "json"
            )
        )["mdsmap"]
        # Ceph 19.2.6 names client-facing messenger instances mon/client/mds.
        # OSD "client" excludes daemon-to-daemon traffic; these names are not
        # modes.
        cls.daemons = [(f"mon.{mon['name']}", "mon") for mon in monmap["mons"]]
        cls.daemons += [(f"osd.{osd}", "client") for osd in osds]
        cls.daemons += [
            (f"mds.{mds['name']}", "mds")
            for mds in mdsmap["info"].values()
            if mds["state"] == "up:active"
        ]

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
        # Key type and transport mode are independent. A fresh scoped identity
        # lets us attribute live protocol evidence to this comparison alone.
        with volume(self, "aes256k") as resource:
            # Request a per-client mode without changing cluster policy, then
            # keep the client connected after I/O to inspect what it
            # negotiated.
            resource.connect(mode)
            resource.write()
            resource.read_compare()
            # Match FSID and credential: unrelated clients must not supply
            # the global_id used for daemon evidence below.
            clients = json.loads(
                self.run_command(
                    "python3",
                    "-c",
                    KERNEL_CLIENT,
                    resource.name,
                    self.expected["SRU_EXPECTED_FSID"],
                )
            )
            self.assertEqual(
                len(clients),
                1,
                "Need one live kernel client for this scoped identity",
            )
            client = clients[0]
            # debugfs names clients <fsid>.client<global_id>. This monitor ID
            # distinguishes sessions sharing a credential, unlike a PID or
            # RBD device number.
            global_id = int(client["directory"].rsplit(".client", 1)[1])
            # Check effective kernel state, not just the requested options.
            # With nocephx_sign_messages absent (NOMSGSIGN clear), the v1
            # handshake requires MSG_AUTH, the feature used for message
            # signing.
            self.assertNotIn(
                "nocephx_sign_messages",
                client["options"],
                "Kernel CephX signing must remain enabled",
            )
            seen = set()
            # Require every workload role, but not every daemon: I/O need not
            # contact every OSD. CephFS additionally needs a live MDS session.
            for daemon, messenger in self.daemons:
                kind = daemon.split(".", 1)[0]
                if kind not in required_peers:
                    continue
                dump = json.loads(
                    self.run_command(
                        "ceph",
                        *ADMIN,
                        "tell",
                        daemon,
                        "messenger",
                        "dump",
                        messenger,
                        "connections",
                        "anon_conns",
                        "--format",
                        "json",
                    )
                )["messenger"]
                # Dumps have two connection collections. Include anon_conns
                # because monitor client sessions can appear only there.
                # The collection name alone says nothing about authentication.
                connections = [
                    entry["async_connection"] for entry in dump["connections"]
                ]
                connections += dump["anon_conns"]
                for connection in connections:
                    # Ignore daemon-to-daemon traffic, other clients, and stale
                    # disconnected entries before inspecting protocol evidence.
                    peer = connection["peer"]
                    if (
                        peer["type"] != "client"
                        or peer["global_id"] != global_id
                        or not connection["status"]["connected"]
                    ):
                        continue
                    # Monitor reports can omit the credential name even for
                    # authenticated sessions, so global_id is our shared key.
                    # OSD/MDS reports also name the identity; check both there.
                    if kind != "mon":
                        self.assertEqual(
                            peer["entity_name"]["id"],
                            resource.name,
                            "Daemon identity does not match the kernel client",
                        )
                    protocol = connection["protocol"]
                    print(
                        f"{resource.entity} global_id={global_id} {daemon} "
                        f"{messenger}: {json.dumps(protocol, sort_keys=True)}",
                        flush=True,
                    )
                    # protocol describes the live negotiated version: legacy
                    # must use v1, while crc/secure must use v2. An established
                    # connection is called OPENED in v1 and READY in v2.
                    if mode == "legacy":
                        self.assertEqual(
                            set(protocol),
                            {"v1"},
                            "Legacy request did not negotiate messenger v1",
                        )
                        self.assertEqual(protocol["v1"]["state"], "OPENED")
                        # Together with the client's signing requirement and
                        # completed I/O, this establishes v1 signing/checking;
                        # it is not a capture of individual message signatures.
                        signing = self.run_command(
                            "ceph",
                            *ADMIN,
                            "config",
                            "show",
                            daemon,
                            "cephx_sign_messages",
                        ).strip()
                        self.assertEqual(
                            signing,
                            "true",
                            f"{daemon} has disabled v1 signing/checking",
                        )
                    else:
                        # READY means the handshake completed. con_mode is the
                        # daemon's negotiated mode, not our requested ms_mode.
                        self.assertEqual(set(protocol), {"v2"})
                        self.assertEqual(protocol["v2"]["state"], "READY")
                        self.assertEqual(
                            protocol["v2"]["con_mode"],
                            mode,
                            "Daemon reports a different negotiated mode",
                        )
                        # crypto.rx/tx are receive/transmit ciphers as seen by
                        # this daemon. crc still authenticates with CephX but
                        # carries plaintext payloads (PLAIN); secure must
                        # encrypt traffic in both directions.
                        for direction in ("rx", "tx"):
                            cipher = protocol["v2"]["crypto"][direction]
                            if mode == "crc":
                                self.assertEqual(cipher, "PLAIN")
                            else:
                                self.assertTrue(
                                    cipher, "Missing live cipher evidence"
                                )
                                self.assertNotEqual(
                                    cipher,
                                    "PLAIN",
                                    "Secure mode lacks an active cipher",
                                )
                    seen.add(kind)
            # Require every role, not every daemon: an empty or mismatched
            # dump must not skip all the protocol assertions and pass.
            self.assertEqual(
                seen,
                required_peers,
                "Missing live identity-correlated daemon connection evidence",
            )

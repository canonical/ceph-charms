"""Bullet 04: persistent aes256k data and live kernel-client session recovery."""

from contextlib import ExitStack, contextmanager
import json
import re
import shlex
import time

import zaza.model

from helpers import ADMIN, pgs_active_clean, remote
from lab import LabTestCase
from storage import cephfs_volume, rbd_volume


TTL = 60
TTL_OPTIONS = ("auth_mon_ticket_ttl", "auth_service_ticket_ttl")
DEBUG_CONTROL = "/sys/kernel/debug/dynamic_debug/control"

# Only these two nonsecret debug sites are enabled, not the whole auth module.
DEBUG_SITES = r'''
import json, pathlib, re
sites = []
for line in pathlib.Path('/sys/kernel/debug/dynamic_debug/control').read_text().splitlines():
    match = re.match(r'net/ceph/auth_x.c:(\d+) \[libceph\]process_one_ticket =(\S+) "(.*)"$', line)
    if match and any(text in match[3] for text in ('expires=%llu renew_after=', 'got ticket service %d')):
        sites.append({'line': int(match[1]), 'flags': match[2]})
print(json.dumps(sites))
'''

# Filter on the guest: unrelated kernel messages never enter action results.
JOURNAL = r'''
import json, re, subprocess, sys
cursor = sys.argv[1]
args = ['journalctl', '-k', '--no-pager', '--output=json']
args += ['--after-cursor=' + cursor] if cursor else ['--lines=1']
result = subprocess.run(args, text=True, capture_output=True, check=True, timeout=20)
events = []
for line in result.stdout.splitlines():
    record = json.loads(line)
    cursor = record['__CURSOR']
    message = record.get('MESSAGE', '')
    if 'libceph' in message and re.search(r'expires=\d+ renew_after=\d+|got ticket service \d+ \([^)]+\) secret_id -?\d+ len \d+', message):
        events.append({'time': int(record['__REALTIME_TIMESTAMP']) / 1000000, 'message': message})
assert cursor, 'No readable kernel journal cursor'
print(json.dumps({'cursor': cursor, 'events': events}))
'''

CLIENTS = r'''
import json, pathlib
root = pathlib.Path('/sys/kernel/debug/ceph')
assert root.is_dir(), 'Missing Ceph kernel debugfs directory'
print(json.dumps(sorted(path.name for path in root.iterdir() if path.is_dir())))
'''


class SessionRecoveryTest(LabTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.osds = json.loads(cls.run_command("ceph", *ADMIN, "osd", "ls", "--format", "json"))
        assert len(cls.osds) == 2, "Use the dedicated two-OSD lab, not a production cluster"
        info = cls._mdsmap()["info"]
        assert len(info) == 1, "This disruptive case requires exactly one MDS"
        mds = next(iter(info.values()))
        assert mds["state"] == "up:active"
        cls.mds_name = mds["name"]

        # Resolve actual Ceph metadata hostnames to actual Juju unit hostnames.
        # Neither OSD IDs nor MDS names are assumed to match charm unit numbers.
        status = zaza.model.get_status(model_name=cls.expected["model"])
        hosts = {}
        for application in status.applications.values():
            for unit in application.get("units", {}):
                hostname = remote(cls.expected["model"], unit, "hostname").strip()
                hosts.setdefault(hostname, []).append(unit)
        cls.daemon_units = {}
        daemons = [("osd", str(osd)) for osd in cls.osds] + [("mds", cls.mds_name)]
        for kind, name in daemons:
            metadata = json.loads(cls.run_command("ceph", *ADMIN, kind, "metadata", name,
                                                  "--format", "json"))
            units = hosts.get(metadata["hostname"], [])
            assert len(units) == 1, f"Cannot uniquely place {kind}.{name} on a Juju unit"
            unit = units[0]
            cls.daemon_units[(kind, name)] = unit
            remote(cls.expected["model"], unit,
                   shlex.join(["systemctl", "is-active", f"ceph-{kind}@{name}.service"]))
        cls._wait_ready()

    @classmethod
    def _mdsmap(cls):
        return json.loads(cls.run_command("ceph", *ADMIN, "fs", "get", cls.filesystem,
                                          "--format", "json", timeout=30))["mdsmap"]

    @classmethod
    def _wait_ready(cls, osd=None, old_up_from=None, old_mds_gid=None):
        deadline = time.monotonic() + 180
        while True:
            dump = json.loads(cls.run_command("ceph", *ADMIN, "osd", "dump",
                                             "--format", "json", timeout=30))
            osds = {entry["osd"]: entry for entry in dump["osds"]}
            ready = set(osds) == set(cls.osds) and all(
                entry["up"] == 1 and entry["in"] == 1 for entry in osds.values())
            if osd is not None:
                ready = ready and osds[osd]["up_from"] > old_up_from
            pgs = json.loads(cls.run_command("ceph", *ADMIN, "pg", "ls",
                                            "--format", "json", timeout=30))
            ready = ready and pgs_active_clean(pgs["pg_stats"])
            active = [mds for mds in cls._mdsmap()["info"].values()
                      if mds["name"] == cls.mds_name and mds["state"] == "up:active"]
            ready = ready and len(active) == 1
            if old_mds_gid is not None:
                ready = ready and len(active) == 1 and active[0]["gid"] != old_mds_gid
            if ready:
                return
            assert time.monotonic() < deadline, "OSD/PG/MDS readiness did not recover within 180s"
            time.sleep(3)

    def test_rbd_remap(self):
        with rbd_volume(self, "aes256k") as volume:
            volume.connect()
            volume.write()
            volume.disconnect()
            volume.connect()
            volume.read_compare()

    def test_cephfs_remount(self):
        with cephfs_volume(self, "aes256k") as volume:
            volume.connect()
            volume.write()
            volume.disconnect()
            volume.connect()
            volume.read_compare()

    def test_rbd_live_recovery(self):
        with self._short_tickets(), self._ticket_debug() as cursor:
            with rbd_volume(self, "aes256k") as volume:
                volume.connect()
                volume.write()
                client = self._single_client()
                path = volume.path
                self._await_renewal(cursor, {4, 32}, client)
                volume.read_compare()
                for osd in self.osds:
                    self._restart_osd(osd)
                    self.assertEqual(volume.path, path)
                    self.assertEqual(self._single_client(), client)
                    volume.read_compare()

    def test_cephfs_live_recovery(self):
        with self._short_tickets(), self._ticket_debug() as cursor:
            with cephfs_volume(self, "aes256k") as volume:
                volume.connect()
                volume.write()
                client = self._single_client()
                path = volume.path
                self._await_renewal(cursor, {2, 4, 32}, client)
                volume.read_compare()
                for osd in self.osds:
                    self._restart_osd(osd)
                    self.assertEqual(volume.path, path)
                    self.assertEqual(self._single_client(), client)
                    volume.read_compare()
                self._restart_mds()
                # A fresh directory operation requires the recovered MDS session;
                # reading an already cached dentry alone would not prove that.
                probe = f"{volume.directory}/mds-reconnect"
                self.run_command("mkdir", "--", probe)
                self.run_command("rmdir", "--", probe)
                self.assertEqual(volume.path, path)
                self.assertEqual(self._single_client(), client)
                volume.read_compare()

    def _restart_service(self, kind, name):
        unit = self.daemon_units[(kind, name)]
        service = f"ceph-{kind}@{name}.service"
        command = shlex.join(["systemctl", "show", "--property=InvocationID", "--value", service])
        before = remote(self.expected["model"], unit, command).strip()
        self.assertRegex(before, r"^[0-9a-f]{32}$", "No running systemd daemon invocation")
        remote(self.expected["model"], unit,
               shlex.join(["systemctl", "restart", service]), timeout=90)
        remote(self.expected["model"], unit,
               shlex.join(["systemctl", "is-active", service]))
        after = remote(self.expected["model"], unit, command).strip()
        self.assertRegex(after, r"^[0-9a-f]{32}$")
        self.assertNotEqual(after, before, f"{service} did not actually restart")

    def _restart_osd(self, osd):
        dump = json.loads(self.run_command("ceph", *ADMIN, "osd", "dump", "--format", "json"))
        previous = next(entry["up_from"] for entry in dump["osds"] if entry["osd"] == osd)
        self._restart_service("osd", str(osd))
        self._wait_ready(osd=osd, old_up_from=previous)

    def _restart_mds(self):
        previous = next(mds["gid"] for mds in self._mdsmap()["info"].values()
                        if mds["name"] == self.mds_name)
        self._restart_service("mds", self.mds_name)
        self._wait_ready(old_mds_gid=previous)

    def _wait_ttls(self, expected):
        deadline = time.monotonic() + 60
        while True:
            values = {(daemon, option): float(self.run_command(
                "ceph", *ADMIN, "config", "show", daemon, option, timeout=20).strip())
                for daemon, option in expected}
            if values == expected:
                return
            self.assertLess(time.monotonic(), deadline,
                            "Monitor ticket TTLs did not reach the requested/restored values")
            time.sleep(2)

    @contextmanager
    def _short_tickets(self):
        monmap = json.loads(self.run_command("ceph", *ADMIN, "mon", "dump", "--format", "json"))
        daemons = [f"mon.{mon['name']}" for mon in monmap["mons"]]
        snapshot = (
            "import json, subprocess, sys; "
            "entries = json.loads(subprocess.check_output(sys.argv[1:], text=True, timeout=30)); "
            "print(json.dumps([entry for entry in entries "
            "if entry['name'] in ('auth_mon_ticket_ttl', 'auth_service_ticket_ttl')]))"
        )
        config = json.loads(self.run_command("python3", "-c", snapshot, "ceph", *ADMIN,
                                             "config", "dump", "--format", "json"))
        originals = {(daemon, option): float(self.run_command(
            "ceph", *ADMIN, "config", "show", daemon, option).strip())
            for daemon in daemons for option in TTL_OPTIONS}
        with ExitStack() as restore:
            # Runtime propagation is checked after all persistent entries restore.
            restore.callback(self._wait_ttls, originals)
            for daemon, option in originals:
                entries = [entry for entry in config if entry["section"] == daemon
                           and entry["name"] == option and not entry.get("mask")]
                self.assertLessEqual(len(entries), 1, "Ambiguous monitor TTL override")
                if entries:
                    restore.callback(self.run_command, "ceph", *ADMIN, "config", "set",
                                     daemon, option, entries[0]["value"])
                else:
                    restore.callback(self.run_command, "ceph", *ADMIN, "config", "rm", daemon, option)
                self.run_command("ceph", *ADMIN, "config", "set", daemon, option, str(TTL))
            self._wait_ttls({key: float(TTL) for key in originals})
            yield

    def _single_client(self):
        clients = json.loads(self.run_command("python3", "-c", CLIENTS))
        self.assertEqual(len(clients), 1,
                         "Renewal attribution requires exactly one kernel Ceph client")
        self.assertTrue(clients[0].startswith(f"{self.expected['SRU_EXPECTED_FSID']}.client"))
        return clients[0]

    @contextmanager
    def _ticket_debug(self):
        self.assertEqual(self.run_command("findmnt", "-n", "-T", "/sys/kernel/debug",
                                          "-o", "FSTYPE").strip(), "debugfs",
                         "Mount debugfs in the dedicated client before running this case")
        self.run_command("modprobe", "libceph")
        self.assertEqual(json.loads(self.run_command("python3", "-c", CLIENTS)), [],
                         "Unmount/unmap existing kernel Ceph clients before renewal tests")
        sites = json.loads(self.run_command("python3", "-c", DEBUG_SITES))
        self.assertEqual(len(sites), 2, "Candidate lacks the two required CephX dynamic-debug sites")
        with ExitStack() as restore:
            for site in sites:
                prefix = f"file net/ceph/auth_x.c line {site['line']} "
                restore.callback(self.script, "printf '%s\\n' " + shlex.quote(prefix + "=" + site["flags"])
                                 + " > " + DEBUG_CONTROL)
                self.script("printf '%s\\n' " + shlex.quote(prefix + "+p") + " > " + DEBUG_CONTROL)
            yield json.loads(self.run_command("python3", "-c", JOURNAL, ""))["cursor"]

    def _await_renewal(self, cursor, services, client):
        deadline = time.monotonic() + 180
        first = {}
        renewed = set()
        pending = None
        while True:
            self.assertEqual(self._single_client(), client,
                             "The original live kernel client must survive ticket renewal")
            batch = json.loads(self.run_command("python3", "-c", JOURNAL, cursor, timeout=30))
            cursor = batch["cursor"]
            for event in batch["events"]:
                message = event["message"]
                expiry = re.search(r"expires=(\d+) renew_after=(\d+)", message)
                if expiry:
                    pending = tuple(map(int, expiry.groups()))
                    continue
                ticket = re.search(r"got ticket service (\d+) \([^)]+\) secret_id -?\d+ len (\d+)", message)
                if not ticket:
                    continue
                self.assertIsNotNone(pending, "Lost expiry evidence before successful ticket installation")
                service = int(ticket[1])
                expires, renew_after = pending
                pending = None
                if service not in services:
                    continue
                self.assertGreater(int(ticket[2]), 0, "Empty installed ticket")
                if service not in first:
                    self.assertGreater(expires, event["time"])
                    self.assertLessEqual(expires - event["time"], TTL + 5,
                                         "Initial ticket did not use the shortened TTL")
                    self.assertLess(renew_after, expires)
                    first[service] = (expires, renew_after)
                elif expires > first[service][0] and event["time"] >= first[service][1]:
                    renewed.add(service)
                    print(f"Confirmed kernel client {client} service {service} ticket renewal: "
                          f"expiry {first[service][0]} -> {expires}", flush=True)
            if renewed == services:
                return
            self.assertLess(time.monotonic(), deadline,
                            f"No installed renewal tickets within 180s: initial={first}, renewed={renewed}")
            time.sleep(3)

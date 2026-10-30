"""Command execution for the host-side SRU harness; never send secrets via actions."""

import shlex
import subprocess

import zaza.model


CONF = "/etc/ceph/ceph.conf"
ADMIN = ["--conf", CONF, "--name", "client.admin", "--keyring",
         "/etc/ceph/ceph.client.admin.keyring"]


class RemoteCommandError(RuntimeError):
    """Keep nonsecret command evidence so negative tests can check the failure."""

    def __init__(self, unit, result):
        self.code = result.get("Code")
        self.stdout = result.get("Stdout", "")
        self.stderr = result.get("Stderr", "")
        super().__init__(f"{unit}: command failed with exit {self.code}")


def local(*args, timeout=600):
    """Run a host utility (artifact inspection or LXD), not a Juju CLI wrapper."""
    command = [str(arg) for arg in args]
    print("+", shlex.join(command), flush=True)
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    print(result.stdout, end="", flush=True)
    print(result.stderr, end="", flush=True)
    result.check_returncode()
    return result.stdout


def remote(model_name, unit_name, command, timeout=90):
    """Execute a nonsecret root command through Zaza and check its result."""
    print(f"+ {unit_name}: {command}", flush=True)
    # Bound execution in the guest too, rather than only timing out the caller.
    bounded = f"timeout --kill-after=5s {timeout}s bash -ceu {shlex.quote(command)}"
    result = zaza.model.run_on_unit(unit_name, bounded, model_name=model_name,
                                    timeout=timeout + 15)
    print(result.get("Stdout", ""), end="", flush=True)
    print(result.get("Stderr", ""), end="", flush=True)
    print(f"exit={result.get('Code')}", flush=True)
    if str(result.get("Code")) != "0":
        raise RemoteCommandError(unit_name, result)
    return result.get("Stdout", "")


def pgs_active_clean(stats):
    """Ceph PG state is a set of flags; healthy groups may also be scrubbing."""
    return bool(stats) and all(
        {"active", "clean"} <= set(pg["state"].split("+")) for pg in stats
    )

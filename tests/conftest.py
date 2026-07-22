# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Pytest + jubilant fixtures for integration testing."""

from collections.abc import Iterator
import subprocess

import jubilant
import pytest

# Light constraints for the core (non-slow) tests. PR/main CI runs these on a
# single self-hosted runner, so keep the per-machine footprint small; the
# 3 MON + 3 OSD + RGW stack has historically run within these limits.
MODEL_CONSTRAINTS_CORE = (
    "virt-type=virtual-machine",
    "mem=4G",
    "root-disk=16G",
)

# Heavier constraints for slow deployment scenarios that add charms on top of
# the core stack or run multi-site topologies (dashboard, fs, nfs, nvme, proxy,
# rbd-mirror). Scheduled CI runs these on a longer-timeout job.
MODEL_CONSTRAINTS_SLOW = (
    "virt-type=virtual-machine",
    "cores=4",
    "mem=8G",
    "root-disk=40G",
)


def _model_constraints(request: pytest.FixtureRequest) -> tuple[str, ...]:
    """Return model constraints sized for the requesting module's test scope.

    Modules marked ``slow`` (module- or class-level) get the heavier scenario
    constraints; core tests get the light CI baseline. The ``juju`` fixture is
    module-scoped, so the first selected item determines the shared model's
    constraints -- slow plan-only tests that reuse a core module's model are
    unaffected by the light baseline since they perform no deployment.
    """
    if request.node.get_closest_marker("slow"):
        return MODEL_CONSTRAINTS_SLOW
    return MODEL_CONSTRAINTS_CORE


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register custom pytest options."""
    parser.addoption(
        "--keep-models",
        action="store_true",
        default=False,
        help="Do not destroy temporary Juju models created by integration tests.",
    )
    parser.addoption(
        "--keep-terraform-workspace",
        action="store_true",
        default=False,
        help="Preserve generated Terraform workspace directories for debugging.",
    )


def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo) -> None:
    """Stop the session when an abort_on_fail test fails."""
    if call.when != "call" or not call.excinfo:
        return
    if call.excinfo.errisinstance(pytest.skip.Exception):
        return
    if item.get_closest_marker("abort_on_fail"):
        item.session.shouldstop = "abort_on_fail marker triggered"


def _set_model_constraints(model_name: str, constraints: tuple[str, ...]) -> None:
    """Set model constraints suitable for Ceph integration tests."""
    subprocess.run(
        [
            "juju",
            "set-model-constraints",
            "-m",
            model_name,
            *constraints,
        ],
        check=True,
    )


@pytest.fixture(scope="module")
def juju(request: pytest.FixtureRequest) -> Iterator[jubilant.Juju]:
    """Provide a temporary Juju model managed by Jubilant."""
    keep_models = bool(request.config.getoption("--keep-models"))
    with jubilant.temp_model(keep=keep_models) as model:
        model.wait_timeout = 20 * 60

        if not model.model:
            raise ValueError("Juju model name unavailable")
        _set_model_constraints(model.model, _model_constraints(request))

        yield model
        if request.session.testsfailed:
            debug_log = model.debug_log(limit=1000)
            if debug_log:
                print(debug_log, end="")

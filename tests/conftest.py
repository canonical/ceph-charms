# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Pytest + jubilant fixtures for integration testing."""

from collections.abc import Iterator
import subprocess

import jubilant
import pytest

# Baseline machine constraints keep integration runs predictable on CI runners
# while still meeting Ceph charm minimum requirements.
MODEL_CONSTRAINTS = (
    "virt-type=virtual-machine",
    "mem=4G",
    "root-disk=16G",
)


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


def _set_model_constraints(model_name: str) -> None:
    """Set model constraints suitable for Ceph integration tests."""
    subprocess.run(
        [
            "juju",
            "set-model-constraints",
            "-m",
            model_name,
            *MODEL_CONSTRAINTS,
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
        _set_model_constraints(model.model)

        yield model
        if request.session.testsfailed:
            debug_log = model.debug_log(limit=1000)
            if debug_log:
                print(debug_log, end="")

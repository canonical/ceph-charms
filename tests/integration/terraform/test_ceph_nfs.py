# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Terraform deployment smoke test for ceph-nfs."""

from __future__ import annotations

import jubilant
import pytest

from tests import helpers
from tests.integration.terraform.helpers import (
    TerraformController,
    assert_component_names,
    workspace_main,
)

pytestmark = pytest.mark.slow

EXPECTED_APPS = {
    "ceph-mon",
    "ceph-osd",
    "ceph-radosgw",
    "ceph-fs",
    "ceph-nfs",
}


@pytest.fixture(scope="module")
def terraform_controller(terraform_controller_factory) -> TerraformController:
    return terraform_controller_factory(
        workspace_main(),
        {
            "ceph_fs": {},
            "ceph_nfs": {"units": 1},
        },
        "ceph-terraform-nfs-",
    )


@pytest.fixture(scope="module")
def applied_stack(
    terraform_controller: TerraformController,
    juju: jubilant.Juju,
) -> TerraformController:
    terraform_controller.apply()
    helpers.wait_for_apps_active(juju, *sorted(EXPECTED_APPS), timeout=60 * 60)
    return terraform_controller


def test_plan(terraform_controller: TerraformController) -> None:
    terraform_controller.plan(out="ceph-nfs.tfplan")
    terraform_controller.show_plan("ceph-nfs.tfplan")


def test_deployment_and_outputs(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    status = juju.status()
    assert helpers.relation_exists(
        status, app="ceph-mon", endpoint="client", related_app="ceph-nfs"
    )
    assert helpers.relation_exists(
        status, app="ceph-nfs", endpoint="ceph-client", related_app="ceph-mon"
    )
    assert_component_names(applied_stack, EXPECTED_APPS)

    state = applied_stack.state_list()
    assert "module.ceph.module.ceph_fs[0].juju_application.ceph_fs" in state
    assert "module.ceph.module.ceph_nfs[0].juju_application.ceph_nfs" in state


def test_ceph_nfs_smoke(applied_stack: TerraformController, juju: jubilant.Juju) -> None:
    service = juju.exec(
        "systemctl is-active nfs-ganesha",
        unit="ceph-nfs/leader",
        wait=5 * 60,
    )
    service.raise_on_failure()
    assert service.stdout.strip() == "active"

    listed = juju.run("ceph-nfs/leader", "list-shares", wait=5 * 60)
    listed.raise_on_failure()
    assert "exports" in listed.results


def test_no_terraform_drift(applied_stack: TerraformController) -> None:
    applied_stack.assert_no_changes()

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Terraform deployment smoke test for ceph-fs."""

from __future__ import annotations

import json

import jubilant
import pytest

from tests import helpers
from tests.integration.terraform.helpers import (
    TerraformController,
    assert_component_names,
    workspace_main,
)

pytestmark = pytest.mark.slow

EXPECTED_APPS = {"ceph-mon", "ceph-osd", "ceph-radosgw", "ceph-fs"}


@pytest.fixture(scope="module")
def terraform_controller(terraform_controller_factory) -> TerraformController:
    return terraform_controller_factory(
        workspace_main(),
        {"ceph_fs": {}},
        "ceph-terraform-fs-",
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
    terraform_controller.plan(out="ceph-fs.tfplan")
    terraform_controller.show_plan("ceph-fs.tfplan")


def test_deployment_and_outputs(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    status = juju.status()
    assert helpers.relation_exists(
        status, app="ceph-mon", endpoint="mds", related_app="ceph-fs"
    )
    assert helpers.relation_exists(
        status, app="ceph-fs", endpoint="ceph-mds", related_app="ceph-mon"
    )
    assert_component_names(applied_stack, EXPECTED_APPS)
    assert "module.ceph.module.ceph_fs[0].juju_application.ceph_fs" in (
        applied_stack.state_list()
    )


def test_ceph_fs_smoke(applied_stack: TerraformController, juju: jubilant.Juju) -> None:
    task = juju.exec("ceph fs ls --format=json", unit="ceph-mon/leader", wait=5 * 60)
    task.raise_on_failure()
    filesystems = json.loads(task.stdout)
    assert filesystems, "ceph-fs did not create a filesystem"


def test_no_terraform_drift(applied_stack: TerraformController) -> None:
    applied_stack.assert_no_changes()

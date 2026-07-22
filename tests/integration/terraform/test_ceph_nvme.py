# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Terraform deployment smoke test for ceph-nvme."""

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

EXPECTED_APPS = {"ceph-mon", "ceph-osd", "ceph-radosgw", "ceph-nvme"}
POOL_NAME = "terraform-nvme"
IMAGE_NAME = "smoke-image"


@pytest.fixture(scope="module")
def terraform_controller(terraform_controller_factory) -> TerraformController:
    return terraform_controller_factory(
        workspace_main(),
        {
            "ceph_nvme": {
                "units": 2,
                "constraints": "cores=8 mem=16G root-disk=40G",
                "config": {
                    "nr-hugepages": "0",
                    "cpuset": "4",
                },
            }
        },
        "ceph-terraform-nvme-",
    )


@pytest.fixture(scope="module")
def applied_stack(
    terraform_controller: TerraformController,
    juju: jubilant.Juju,
) -> TerraformController:
    terraform_controller.apply()
    helpers.wait_for_apps_active(juju, *sorted(EXPECTED_APPS), timeout=90 * 60)
    return terraform_controller


def test_plan(terraform_controller: TerraformController) -> None:
    terraform_controller.plan(out="ceph-nvme.tfplan")
    terraform_controller.show_plan("ceph-nvme.tfplan")


def test_deployment_and_outputs(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    status = juju.status()
    assert helpers.relation_exists(
        status, app="ceph-mon", endpoint="client", related_app="ceph-nvme"
    )
    assert helpers.relation_exists(
        status, app="ceph-nvme", endpoint="ceph-client", related_app="ceph-mon"
    )
    assert len(status.apps["ceph-nvme"].units) == 2
    assert_component_names(applied_stack, EXPECTED_APPS)
    assert "module.ceph.module.ceph_nvme[0].juju_application.ceph_nvme" in (
        applied_stack.state_list()
    )


def test_ceph_nvme_smoke(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    created_pool = juju.run(
        "ceph-mon/leader",
        "create-pool",
        {"name": POOL_NAME, "app-name": "rbd"},
        wait=10 * 60,
    )
    created_pool.raise_on_failure()

    endpoint_nqn: str | None = None
    try:
        created_image = juju.exec(
            f"rbd create --size 64 {POOL_NAME}/{IMAGE_NAME}",
            unit="ceph-mon/leader",
            wait=5 * 60,
        )
        created_image.raise_on_failure()

        endpoint = juju.run(
            "ceph-nvme/leader",
            "create-endpoint",
            {"rbd-pool": POOL_NAME, "rbd-image": IMAGE_NAME, "units": "1"},
            wait=15 * 60,
        )
        endpoint.raise_on_failure()
        endpoint_nqn = endpoint.results.get("nqn")
        assert endpoint_nqn

        listed = juju.run("ceph-nvme/leader", "list-endpoints", wait=5 * 60)
        listed.raise_on_failure()
        assert endpoint_nqn in json.dumps(listed.results)
    finally:
        if endpoint_nqn:
            deleted = juju.run(
                "ceph-nvme/leader",
                "delete-endpoint",
                {"nqn": endpoint_nqn},
                wait=10 * 60,
            )
            deleted.raise_on_failure()
        deleted_pool = juju.run(
            "ceph-mon/leader",
            "delete-pool",
            {"name": POOL_NAME},
            wait=10 * 60,
        )
        deleted_pool.raise_on_failure()


def test_no_terraform_drift(applied_stack: TerraformController) -> None:
    applied_stack.assert_no_changes()

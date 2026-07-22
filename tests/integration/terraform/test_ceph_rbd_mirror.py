# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Terraform deployment smoke test for a two-site RBD mirror topology."""

from __future__ import annotations

import json

import jubilant
import pytest

from tests import helpers
from tests.integration.terraform.helpers import (
    COMPONENT_MODULE_SOURCE,
    TerraformController,
    render_workspace_template,
)

pytestmark = pytest.mark.slow

CORE_APPS = {
    "ceph-mon-a",
    "ceph-osd-a",
    "ceph-radosgw-a",
    "ceph-mon-b",
    "ceph-osd-b",
    "ceph-radosgw-b",
}
MIRROR_APPS = {"ceph-rbd-mirror-a", "ceph-rbd-mirror-b"}
POOL_NAME = "terraform-mirror"


def _mirrors_ready_for_pool_setup(status: jubilant.Status) -> bool:
    for app_name in MIRROR_APPS:
        app = status.apps.get(app_name)
        if app is None or app.app_status.current not in {"active", "waiting"}:
            return False
        if not app.units or any(unit.juju_status.current != "idle" for unit in app.units.values()):
            return False
    return True


@pytest.fixture(scope="module")
def terraform_controller(terraform_controller_factory) -> TerraformController:
    workspace = render_workspace_template(
        "rbd-mirror.tf.tmpl",
        COMPONENT_MODULE_SOURCE=COMPONENT_MODULE_SOURCE,
    )
    return terraform_controller_factory(
        workspace,
        prefix="ceph-terraform-rbd-mirror-",
    )


@pytest.fixture(scope="module")
def applied_stack(
    terraform_controller: TerraformController,
    juju: jubilant.Juju,
) -> TerraformController:
    terraform_controller.apply()
    helpers.wait_for_apps_active(juju, *sorted(CORE_APPS), timeout=90 * 60)
    juju.wait(
        _mirrors_ready_for_pool_setup,
        error=lambda status: jubilant.any_error(status, *MIRROR_APPS),
        timeout=30 * 60,
        successes=3,
    )
    return terraform_controller


def test_plan(terraform_controller: TerraformController) -> None:
    terraform_controller.plan(out="ceph-rbd-mirror.tfplan")
    terraform_controller.show_plan("ceph-rbd-mirror.tfplan")


def test_two_site_relations(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    status = juju.status()
    expected = (
        ("ceph-mon-a", "rbd-mirror", "ceph-rbd-mirror-a"),
        ("ceph-rbd-mirror-a", "ceph-local", "ceph-mon-a"),
        ("ceph-mon-b", "rbd-mirror", "ceph-rbd-mirror-b"),
        ("ceph-rbd-mirror-b", "ceph-local", "ceph-mon-b"),
        ("ceph-mon-b", "rbd-mirror", "ceph-rbd-mirror-a"),
        ("ceph-rbd-mirror-a", "ceph-remote", "ceph-mon-b"),
        ("ceph-mon-a", "rbd-mirror", "ceph-rbd-mirror-b"),
        ("ceph-rbd-mirror-b", "ceph-remote", "ceph-mon-a"),
    )
    for app, endpoint, related_app in expected:
        assert helpers.relation_exists(
            status,
            app=app,
            endpoint=endpoint,
            related_app=related_app,
        )

    state = applied_stack.state_list()
    assert "juju_integration.mirror_a_remote" in state
    assert "juju_integration.mirror_b_remote" in state
    assert (
        "module.site_a.module.ceph_rbd_mirror[0].juju_application.ceph_rbd_mirror"
        in state
    )
    assert (
        "module.site_b.module.ceph_rbd_mirror[0].juju_application.ceph_rbd_mirror"
        in state
    )


def test_ceph_rbd_mirror_smoke(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    for suffix in ("a", "b"):
        pool = juju.run(
            f"ceph-mon-{suffix}/leader",
            "create-pool",
            {"name": POOL_NAME, "app-name": "rbd"},
            wait=10 * 60,
        )
        pool.raise_on_failure()

    try:
        for suffix in ("a", "b"):
            refreshed = juju.run(
                f"ceph-rbd-mirror-{suffix}/leader",
                "refresh-pools",
                wait=10 * 60,
            )
            refreshed.raise_on_failure()

        helpers.wait_for_apps_active(juju, *sorted(MIRROR_APPS), timeout=30 * 60)

        for suffix in ("a", "b"):
            status = juju.run(
                f"ceph-rbd-mirror-{suffix}/leader",
                "status",
                {"verbose": True, "format": "json", "pools": POOL_NAME},
                wait=10 * 60,
            )
            status.raise_on_failure()
            output = json.loads(status.results["output"])
            assert POOL_NAME in output
    finally:
        for suffix in ("a", "b"):
            deleted = juju.run(
                f"ceph-mon-{suffix}/leader",
                "delete-pool",
                {"name": POOL_NAME},
                wait=10 * 60,
            )
            deleted.raise_on_failure()


def test_no_terraform_drift(applied_stack: TerraformController) -> None:
    applied_stack.assert_no_changes()

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Terraform deployment smoke test for ceph-proxy."""

from __future__ import annotations

import json

import jubilant
import pytest

from tests import helpers
from tests.integration.terraform.helpers import (
    COMPONENT_MODULE_SOURCE,
    REPO_ROOT,
    TerraformController,
    render_workspace_template,
)

pytestmark = pytest.mark.slow

SOURCE_APPS = {"ceph-mon", "ceph-osd", "ceph-radosgw"}
PROXY_APPS = {"ceph-proxy", "proxy-ceph-fs"}


@pytest.fixture(scope="module")
def terraform_controller(terraform_controller_factory) -> TerraformController:
    workspace = render_workspace_template(
        "proxy.tf.tmpl",
        COMPONENT_MODULE_SOURCE=COMPONENT_MODULE_SOURCE,
        CEPH_FS_MODULE_SOURCE=f"{REPO_ROOT}//ceph-fs/terraform",
    )
    return terraform_controller_factory(
        workspace,
        {"proxy_config": None},
        "ceph-terraform-proxy-",
    )


@pytest.fixture(scope="module")
def source_stack(
    terraform_controller: TerraformController,
    juju: jubilant.Juju,
) -> TerraformController:
    terraform_controller.apply(plan_file="source.tfplan")
    helpers.wait_for_apps_active(juju, *sorted(SOURCE_APPS), timeout=60 * 60)
    return terraform_controller


@pytest.fixture(scope="module")
def applied_stack(
    source_stack: TerraformController,
    juju: jubilant.Juju,
) -> TerraformController:
    fsid_task = juju.exec("ceph fsid", unit="ceph-mon/leader", wait=5 * 60)
    fsid_task.raise_on_failure()
    key_task = juju.exec(
        "ceph auth get-key client.admin",
        unit="ceph-mon/leader",
        wait=5 * 60,
    )
    key_task.raise_on_failure()

    status = juju.status()
    monitor_hosts = " ".join(
        unit.public_address
        for unit in status.apps["ceph-mon"].units.values()
        if unit.public_address
    )
    assert monitor_hosts, "Unable to determine ceph-mon addresses"

    admin_key = key_task.stdout.strip()
    source_stack.add_redaction(admin_key)
    source_stack.write_tfvars(
        {
            "proxy_config": {
                "fsid": fsid_task.stdout.strip(),
                "admin-key": admin_key,
                "auth-supported": "cephx",
                "monitor-hosts": monitor_hosts,
            }
        }
    )
    source_stack.apply(plan_file="proxy.tfplan")
    helpers.wait_for_apps_active(
        juju,
        *sorted(SOURCE_APPS | PROXY_APPS),
        timeout=60 * 60,
    )
    return source_stack


def test_initial_plan(terraform_controller: TerraformController) -> None:
    terraform_controller.plan(out="ceph-proxy-source.tfplan")
    terraform_controller.show_plan("ceph-proxy-source.tfplan")


def test_proxy_deployment_and_relations(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    status = juju.status()
    assert helpers.relation_exists(
        status,
        app="ceph-proxy",
        endpoint="mds",
        related_app="proxy-ceph-fs",
    )
    assert helpers.relation_exists(
        status,
        app="proxy-ceph-fs",
        endpoint="ceph-mds",
        related_app="ceph-proxy",
    )
    assert not any(
        relation.related_app == "ceph-mon"
        for relations in status.apps["ceph-proxy"].relations.values()
        for relation in relations
    )

    state = applied_stack.state_list()
    assert "module.source.module.ceph_proxy[0].juju_application.ceph_proxy" in state
    assert "module.proxy_ceph_fs[0].juju_application.ceph_fs" in state
    assert "juju_integration.proxy_to_ceph_fs[0]" in state


def test_ceph_proxy_smoke(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    task = juju.exec("ceph fs ls --format=json", unit="ceph-mon/leader", wait=5 * 60)
    task.raise_on_failure()
    filesystems = json.loads(task.stdout)
    assert filesystems, "ceph-fs did not provision a filesystem through ceph-proxy"


def test_no_terraform_drift(applied_stack: TerraformController) -> None:
    applied_stack.assert_no_changes()

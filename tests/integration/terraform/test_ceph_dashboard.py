# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Terraform deployment smoke test for ceph-dashboard."""

from __future__ import annotations

import base64
from pathlib import Path
import subprocess

import jubilant
import pytest

from tests import helpers
from tests.integration.terraform.helpers import (
    TerraformController,
    assert_component_names,
    workspace_main,
)

pytestmark = pytest.mark.slow

EXPECTED_APPS = {"ceph-mon", "ceph-osd", "ceph-radosgw", "ceph-dashboard"}
DASHBOARD_USER = "terraform-smoke"


def _generate_certificate(directory: Path) -> dict[str, str]:
    key_path = directory / "dashboard.key"
    cert_path = directory / "dashboard.crt"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-subj",
            "/CN=ceph-dashboard",
            "-addext",
            "subjectAltName=DNS:ceph-dashboard",
            "-keyout",
            str(key_path),
            "-out",
            str(cert_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    certificate = cert_path.read_bytes()
    return {
        "ssl_cert": base64.b64encode(certificate).decode(),
        "ssl_key": base64.b64encode(key_path.read_bytes()).decode(),
        "ssl_ca": base64.b64encode(certificate).decode(),
        "public-hostname": "ceph-dashboard",
    }


@pytest.fixture(scope="module")
def terraform_controller(
    terraform_controller_factory,
    tmp_path_factory: pytest.TempPathFactory,
) -> TerraformController:
    tls_config = _generate_certificate(tmp_path_factory.mktemp("dashboard-tls"))
    controller = terraform_controller_factory(
        workspace_main(),
        {"ceph_dashboard": {"config": tls_config}},
        "ceph-terraform-dashboard-",
    )
    for key in ("ssl_cert", "ssl_key", "ssl_ca"):
        controller.add_redaction(tls_config[key])
    return controller


@pytest.fixture(scope="module")
def applied_stack(
    terraform_controller: TerraformController,
    juju: jubilant.Juju,
) -> TerraformController:
    terraform_controller.apply()
    helpers.wait_for_apps_active(juju, *sorted(EXPECTED_APPS), timeout=60 * 60)
    return terraform_controller


def test_plan(terraform_controller: TerraformController) -> None:
    terraform_controller.plan(out="ceph-dashboard.tfplan")
    terraform_controller.show_plan("ceph-dashboard.tfplan")


def test_deployment_and_outputs(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    status = juju.status()
    assert helpers.relation_exists(
        status, app="ceph-mon", endpoint="dashboard", related_app="ceph-dashboard"
    )
    assert helpers.relation_exists(
        status, app="ceph-dashboard", endpoint="dashboard", related_app="ceph-mon"
    )

    mon = status.apps["ceph-mon"]
    dashboard_units = sum(
        subordinate.startswith("ceph-dashboard/")
        for unit in mon.units.values()
        for subordinate in unit.subordinates
    )
    assert dashboard_units == len(mon.units)

    assert_component_names(applied_stack, EXPECTED_APPS)
    assert "module.ceph.module.ceph_dashboard[0].juju_application.ceph_dashboard" in (
        applied_stack.state_list()
    )


def test_ceph_dashboard_smoke(
    applied_stack: TerraformController,
    juju: jubilant.Juju,
) -> None:
    added = juju.run(
        "ceph-dashboard/leader",
        "add-user",
        {"username": DASHBOARD_USER, "role": "administrator"},
        wait=10 * 60,
    )
    added.raise_on_failure()
    assert added.results.get("password")

    deleted = juju.run(
        "ceph-dashboard/leader",
        "delete-user",
        {"username": DASHBOARD_USER},
        wait=5 * 60,
    )
    deleted.raise_on_failure()


def test_no_terraform_drift(applied_stack: TerraformController) -> None:
    applied_stack.assert_no_changes()

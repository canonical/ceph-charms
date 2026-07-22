# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the top-level Terraform component module."""

from __future__ import annotations

import json
import logging
import subprocess
import time
from typing import Any

import jubilant
import pytest

from tests import helpers
from tests.integration.terraform.helpers import (
    COMPONENT_MODULE_SOURCE,
    TerraformController,
    planned_resource_addresses as _planned_resource_addresses,
    workspace_main as _workspace_main,
)

logger = logging.getLogger(__name__)

CORE_APPS = ("ceph-mon", "ceph-osd")
RGW_APP = "ceph-radosgw"

EXPECTED_UNITS = {
    "ceph-mon": 3,
    "ceph-osd": 3,
    "ceph-radosgw": 1,
}

LOOP_DEVICE_ERROR = "cannot find an unused loop device"
# During relation settling, RGW may transiently report these messages before the
# MON relation is fully established; treat them as retryable.
RGW_REJECTED_MESSAGES = {
    "Incomplete relations: mon",
    "Missing relations: mon",
}

RGW_ATTACH_TIMEOUT = 15 * 60
POLL_INTERVAL_SECONDS = 10

EXPECTED_STATE_RESOURCES = {
    "module.ceph.module.ceph_mon.juju_application.ceph_mon",
    "module.ceph.module.ceph_osd.juju_application.ceph_osd",
    "module.ceph.module.ceph_radosgw.juju_application.ceph_radosgw",
    "module.ceph.juju_integration.ceph_mon_to_ceph_osd",
    "module.ceph.juju_integration.ceph_mon_to_ceph_radosgw",
}

# Cover both in-model endpoint wiring and cross-model offer wiring for each
# optional relation variable.
OPTIONAL_INTEGRATION_CASES = (
    (
        "bootstrap-source-endpoint",
        "bootstrap_source",
        {"kind": "endpoint", "name": "vault", "endpoint": "ceph"},
        "module.ceph.juju_integration.ceph_mon_bootstrap_source[0]",
    ),
    (
        "bootstrap-source-offer",
        "bootstrap_source",
        {"kind": "offer", "url": "admin/vault.ceph"},
        "module.ceph.juju_integration.ceph_mon_bootstrap_source[0]",
    ),
    (
        "secrets-storage-endpoint",
        "secrets_storage",
        {"kind": "endpoint", "name": "vault", "endpoint": "secrets-storage"},
        "module.ceph.juju_integration.ceph_osd_secrets_storage[0]",
    ),
    (
        "secrets-storage-offer",
        "secrets_storage",
        {"kind": "offer", "url": "admin/vault.secrets-storage"},
        "module.ceph.juju_integration.ceph_osd_secrets_storage[0]",
    ),
)

OFFER_EXPOSURE_CASES = (
    (
        "charm-level-rgw-s3",
        "ceph_radosgw",
        {"offered_endpoints": ["s3"]},
        "module.ceph.module.ceph_radosgw.juju_offer.offers[\"s3\"]",
    ),
    (
        "component-level-mon-client",
        "expose_endpoints",
        ["ceph_mon_client"],
        "module.ceph.module.ceph_mon.juju_offer.offers[\"client\"]",
    ),
)

EXPOSE_ENDPOINTS_VALIDATION_ERROR = "expose_endpoints entries must be valid"
DISABLED_CHARM_EXPOSURE_ERROR = "expose_endpoints cannot reference an optional charm"
MODEL_TARGET_VALIDATION_ERROR = "Exactly one model target must be provided"
TEST_MODEL_UUID = "00000000-0000-0000-0000-000000000001"

# Opt-in charm wiring cases for the additional leaf modules. Each tuple is
# (var_name, expected_integration_address, expected_application_address).
ADDITIONAL_CHARM_INTEGRATION_CASES = (
    (
        "ceph_fs",
        "module.ceph.juju_integration.ceph_mon_to_ceph_fs[0]",
        "module.ceph.module.ceph_fs[0].juju_application.ceph_fs",
    ),
    (
        "ceph_nfs",
        "module.ceph.juju_integration.ceph_mon_to_ceph_nfs[0]",
        "module.ceph.module.ceph_nfs[0].juju_application.ceph_nfs",
    ),
    (
        "ceph_nvme",
        "module.ceph.juju_integration.ceph_mon_to_ceph_nvme[0]",
        "module.ceph.module.ceph_nvme[0].juju_application.ceph_nvme",
    ),
    (
        "ceph_rbd_mirror",
        "module.ceph.juju_integration.ceph_mon_to_ceph_rbd_mirror[0]",
        "module.ceph.module.ceph_rbd_mirror[0].juju_application.ceph_rbd_mirror",
    ),
    (
        "ceph_dashboard",
        "module.ceph.juju_integration.ceph_mon_to_ceph_dashboard[0]",
        "module.ceph.module.ceph_dashboard[0].juju_application.ceph_dashboard",
    ),
)

# Additional charm applications and their ceph-mon integrations must not be
# planned unless the corresponding object input is non-null.
ADDITIONAL_CHARM_DEFAULT_ABSENT = (
    "module.ceph.module.ceph_fs[0].juju_application.ceph_fs",
    "module.ceph.module.ceph_nfs[0].juju_application.ceph_nfs",
    "module.ceph.module.ceph_nvme[0].juju_application.ceph_nvme",
    "module.ceph.module.ceph_rbd_mirror[0].juju_application.ceph_rbd_mirror",
    "module.ceph.module.ceph_dashboard[0].juju_application.ceph_dashboard",
    "module.ceph.module.ceph_proxy[0].juju_application.ceph_proxy",
    "module.ceph.juju_integration.ceph_mon_to_ceph_fs[0]",
    "module.ceph.juju_integration.ceph_mon_to_ceph_nfs[0]",
    "module.ceph.juju_integration.ceph_mon_to_ceph_nvme[0]",
    "module.ceph.juju_integration.ceph_mon_to_ceph_rbd_mirror[0]",
    "module.ceph.juju_integration.ceph_mon_to_ceph_dashboard[0]",
)

CEPH_PROXY_APP_ADDRESS = "module.ceph.module.ceph_proxy[0].juju_application.ceph_proxy"
CEPH_FS_OFFER_ADDRESS = 'module.ceph.module.ceph_fs[0].juju_offer.offers["cephfs_share"]'


def _radosgw_related_to_mon(status: jubilant.Status) -> bool:
    return helpers.relation_exists(
        status,
        app=RGW_APP,
        endpoint="mon",
        related_app="ceph-mon",
    ) and helpers.relation_exists(
        status,
        app="ceph-mon",
        endpoint="radosgw",
        related_app=RGW_APP,
    )


def _relationship_summary(status: jubilant.Status) -> str:
    rgw_to_mon = helpers.relation_exists(
        status,
        app=RGW_APP,
        endpoint="mon",
        related_app="ceph-mon",
    )
    mon_to_rgw = helpers.relation_exists(
        status,
        app="ceph-mon",
        endpoint="radosgw",
        related_app=RGW_APP,
    )
    return (
        f"ceph-radosgw:mon->ceph-mon={rgw_to_mon}; "
        f"ceph-mon:radosgw->ceph-radosgw={mon_to_rgw}"
    )


def _status_diagnostics(status: jubilant.Status) -> str:
    app_summaries = [helpers.app_status_summary(status, app) for app in (*CORE_APPS, RGW_APP)]
    app_summaries.append(f"relations[{_relationship_summary(status)}]")
    return " | ".join(app_summaries)


def _wait_for_core_apps(
    juju: jubilant.Juju,
    *apps: str,
    timeout: int = helpers.DEFAULT_TIMEOUT,
) -> jubilant.Status:
    """Wait for core apps to become active and fail on storage exhaustion."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        status = juju.status()
        if jubilant.all_active(status, *apps):
            return status
        if helpers.has_storage_error(status, LOOP_DEVICE_ERROR):
            raise AssertionError("Runner has no free loop devices for ceph-osd storage directives")
        if jubilant.any_error(status):
            raise AssertionError(f"Juju reported an error status: {_status_diagnostics(status)}")
        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Timed out waiting for applications to become active: {apps}; "
        f"last status: {_status_diagnostics(juju.status())}"
    )


def _wait_for_radosgw_usable(
    juju: jubilant.Juju,
    timeout: int = RGW_ATTACH_TIMEOUT,
) -> jubilant.Status:
    """Wait until ceph-radosgw reports a stable active state."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        status = juju.status()
        rgw_status = status.apps.get(RGW_APP)

        if rgw_status is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        app_state = rgw_status.app_status.current
        app_message = rgw_status.app_status.message or ""

        if app_state == "error":
            raise AssertionError(
                f"{RGW_APP} entered error state; diagnostics: {_status_diagnostics(status)}"
            )

        if app_state != "active":
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if app_message in RGW_REJECTED_MESSAGES:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue

        if rgw_status.units:
            return status

        time.sleep(POLL_INTERVAL_SECONDS)

    raise TimeoutError(
        f"Timed out waiting for {RGW_APP} to become usable; "
        f"last status: {_status_diagnostics(juju.status())}"
    )


@pytest.fixture(scope="module")
def terraform_controller(terraform_controller_factory) -> TerraformController:
    """Provision the default component workspace for the core-stack tests."""
    return terraform_controller_factory(
        _workspace_main(COMPONENT_MODULE_SOURCE),
        prefix="ceph-terraform-core-",
    )


@pytest.fixture(scope="module")
def applied_stack(
    terraform_controller: TerraformController,
    juju: jubilant.Juju,
) -> jubilant.Status:
    """Apply the default Terraform stack and wait for expected readiness."""
    terraform_controller.apply()

    _wait_for_core_apps(juju, *CORE_APPS)
    return _wait_for_radosgw_usable(juju)


class TestTerraformModule:
    """Validate the default apply flow for the component Terraform module."""

    @pytest.mark.abort_on_fail
    def test_plan(self, terraform_controller: TerraformController) -> None:
        """terraform plan should succeed and produce a plan file."""
        terraform_controller.plan(out="tfplan")
        terraform_controller.show_plan("tfplan")

    @pytest.mark.abort_on_fail
    def test_apply_and_wait_for_active(self, applied_stack: jubilant.Status) -> None:
        """terraform apply should deploy MON/OSD and a usable RGW relation."""
        for app in CORE_APPS:
            assert app in applied_stack.apps, f"Application {app!r} missing from juju status"
            app_status = applied_stack.apps[app].app_status.current
            assert app_status == "active", f"Application {app!r} not active: {app_status!r}"
            actual_units = len(applied_stack.apps[app].units)
            expected_units = EXPECTED_UNITS[app]
            assert actual_units == expected_units, (
                f"Application {app!r} has {actual_units} units; expected {expected_units}"
            )

        assert RGW_APP in applied_stack.apps, f"Application {RGW_APP!r} missing from juju status"
        rgw_state = applied_stack.apps[RGW_APP].app_status.current
        assert rgw_state == "active", f"{RGW_APP} is not active: {rgw_state!r}"
        assert _radosgw_related_to_mon(applied_stack), "ceph-radosgw is not related to ceph-mon"
        rgw_units = len(applied_stack.apps[RGW_APP].units)
        assert rgw_units == EXPECTED_UNITS[RGW_APP], (
            f"Application {RGW_APP!r} has {rgw_units} units; expected {EXPECTED_UNITS[RGW_APP]}"
        )

    def test_state_and_outputs(
        self,
        terraform_controller: TerraformController,
        applied_stack: jubilant.Status,
    ) -> None:
        """Terraform state and outputs should include expected resources/keys."""
        assert applied_stack  # force apply fixture execution before output/state checks

        state_resources = terraform_controller.state_list()
        missing_resources = EXPECTED_STATE_RESOURCES - state_resources
        assert not missing_resources, f"Missing expected state resources: {missing_resources}"

        outputs = terraform_controller.outputs()
        for key in ("components", "components_map", "provides", "requires", "offers"):
            assert key in outputs, f"Missing output key {key!r}"
            assert "value" in outputs[key], f"Output {key!r} missing value"

        components = outputs["components"]["value"]
        assert isinstance(components, list)
        assert len(components) == 3
        component_names = {component["name"] for component in components}
        assert component_names == {"ceph-mon", "ceph-osd", "ceph-radosgw"}

        components_map = outputs["components_map"]["value"]
        assert set(components_map) == {"ceph_mon", "ceph_osd", "ceph_radosgw"}
        assert components_map["ceph_mon"]["units"] == EXPECTED_UNITS["ceph-mon"]
        assert components_map["ceph_osd"]["units"] == EXPECTED_UNITS["ceph-osd"]
        assert components_map["ceph_radosgw"]["units"] == EXPECTED_UNITS["ceph-radosgw"]

        osd_storage_directives = components_map["ceph_osd"]["storage_directives"] or {}
        assert set(osd_storage_directives) == {"osd-devices"}
        assert osd_storage_directives["osd-devices"] == "10G,1"

        provides = outputs["provides"]["value"]
        for key in ("ceph_mon_osd", "ceph_mon_radosgw", "ceph_radosgw_s3"):
            assert key in provides, f"Expected provides key {key!r} not found"

        requires = outputs["requires"]["value"]
        for key in ("ceph_osd_mon", "ceph_radosgw_mon", "ceph_mon_bootstrap_source"):
            assert key in requires, f"Expected requires key {key!r} not found"

        offers = outputs["offers"]["value"]
        assert offers == {}


class TestTerraformOptionalInputWiring:
    """Validate plan-time wiring for optional external integration inputs."""

    def test_optional_integrations_absent_by_default(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        terraform_controller.plan(out="default-no-optional.tfplan")
        addresses = _planned_resource_addresses(
            terraform_controller.show_plan_json("default-no-optional.tfplan")
        )
        assert "module.ceph.juju_integration.ceph_mon_bootstrap_source[0]" not in addresses
        assert "module.ceph.juju_integration.ceph_osd_secrets_storage[0]" not in addresses
        assert "module.ceph.module.ceph_mon.juju_offer.offers[\"client\"]" not in addresses
        assert "module.ceph.module.ceph_radosgw.juju_offer.offers[\"s3\"]" not in addresses

    @pytest.mark.parametrize(
        "scenario,var_name,var_value,expected_address",
        OPTIONAL_INTEGRATION_CASES,
    )
    def test_optional_integration_plan_wiring(
        self,
        terraform_controller: TerraformController,
        scenario: str,
        var_name: str,
        var_value: dict[str, str],
        expected_address: str,
    ) -> None:
        plan_file = f"optional-{scenario}.tfplan"
        terraform_controller.plan(
            out=plan_file,
            extra_args=["-var", f"{var_name}={json.dumps(var_value)}"],
        )
        addresses = _planned_resource_addresses(terraform_controller.show_plan_json(plan_file))
        assert expected_address in addresses

    @pytest.mark.parametrize(
        "scenario,var_name,var_value,expected_address",
        OFFER_EXPOSURE_CASES,
    )
    def test_offer_plan_wiring(
        self,
        terraform_controller: TerraformController,
        scenario: str,
        var_name: str,
        var_value: dict[str, Any] | list[str],
        expected_address: str,
    ) -> None:
        plan_file = f"offer-{scenario}.tfplan"
        terraform_controller.plan(
            out=plan_file,
            extra_args=["-var", f"{var_name}={json.dumps(var_value)}"],
        )
        addresses = _planned_resource_addresses(terraform_controller.show_plan_json(plan_file))
        assert expected_address in addresses

    def test_invalid_expose_endpoints_fails_validation(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            terraform_controller.plan(
                out="invalid-expose-endpoints.tfplan",
                extra_args=["-var", "expose_endpoints=[\"ceph_mon_not_real\"]"],
            )

        details = f"{exc_info.value.output or ''}\n{exc_info.value.stderr or ''}"
        assert EXPOSE_ENDPOINTS_VALIDATION_ERROR in details

    def test_missing_model_target_fails_validation(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            terraform_controller.plan(
                out="invalid-missing-model-target.tfplan",
                extra_args=[
                    "-var",
                    f"model_name={json.dumps('')}",
                    "-var",
                    f"model_uuid={json.dumps('')}",
                ],
            )

        details = f"{exc_info.value.output or ''}\n{exc_info.value.stderr or ''}"
        assert MODEL_TARGET_VALIDATION_ERROR in details

    def test_multiple_model_targets_fail_validation(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            terraform_controller.plan(
                out="invalid-multiple-model-targets.tfplan",
                extra_args=[
                    "-var",
                    f"model_uuid={json.dumps(TEST_MODEL_UUID)}",
                ],
            )

        details = f"{exc_info.value.output or ''}\n{exc_info.value.stderr or ''}"
        assert MODEL_TARGET_VALIDATION_ERROR in details


@pytest.mark.slow
class TestAdditionalCharmWiring:
    """Plan-time wiring for the opt-in ceph-fs/nfs/nvme/rbd-mirror/dashboard/proxy modules."""

    def test_additional_charms_absent_by_default(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        """Opt-in charms and their integrations must not be planned by default."""
        terraform_controller.plan(out="default-no-additional.tfplan")
        addresses = _planned_resource_addresses(
            terraform_controller.show_plan_json("default-no-additional.tfplan")
        )
        for address in ADDITIONAL_CHARM_DEFAULT_ABSENT:
            assert address not in addresses, f"{address} should not be planned by default"

    @pytest.mark.parametrize(
        "var_name,expected_integration,expected_app",
        ADDITIONAL_CHARM_INTEGRATION_CASES,
    )
    def test_additional_charm_integration_plan_wiring(
        self,
        terraform_controller: TerraformController,
        var_name: str,
        expected_integration: str,
        expected_app: str,
    ) -> None:
        """Enabling an opt-in charm should plan its app and ceph-mon integration."""
        plan_file = f"additional-{var_name}.tfplan"
        terraform_controller.plan(
            out=plan_file,
            extra_args=["-var", f"{var_name}={json.dumps({})}"],
        )
        addresses = _planned_resource_addresses(terraform_controller.show_plan_json(plan_file))
        assert expected_app in addresses, f"{expected_app} not planned when {var_name} is set"
        assert expected_integration in addresses, (
            f"{expected_integration} not planned when {var_name} is set"
        )

    def test_expose_disabled_charm_fails_validation(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        """An offer cannot be requested for an optional charm that is disabled."""
        with pytest.raises(subprocess.CalledProcessError) as exc_info:
            terraform_controller.plan(
                out="invalid-disabled-charm-offer.tfplan",
                extra_args=[
                    "-var",
                    f"expose_endpoints={json.dumps(['ceph_fs_cephfs_share'])}",
                ],
            )

        details = f"{exc_info.value.output or ''}\n{exc_info.value.stderr or ''}"
        assert DISABLED_CHARM_EXPOSURE_ERROR in details

    def test_ceph_proxy_deploys_without_integration(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        """ceph-proxy is a mon replacement and must not be wired to ceph-mon."""
        plan_file = "additional-ceph_proxy.tfplan"
        terraform_controller.plan(
            out=plan_file,
            extra_args=["-var", f"ceph_proxy={json.dumps({})}"],
        )
        addresses = _planned_resource_addresses(terraform_controller.show_plan_json(plan_file))
        assert CEPH_PROXY_APP_ADDRESS in addresses
        assert not any("ceph_mon_to_ceph_proxy" in addr for addr in addresses), (
            "ceph-proxy must not be wired to ceph-mon"
        )

    def test_expose_ceph_fs_offer(
        self,
        terraform_controller: TerraformController,
    ) -> None:
        """Deploying ceph-fs and exposing cephfs_share should publish a Juju offer."""
        plan_file = "additional-ceph-fs-offer.tfplan"
        terraform_controller.plan(
            out=plan_file,
            extra_args=[
                "-var",
                f"ceph_fs={json.dumps({})}",
                "-var",
                f"expose_endpoints={json.dumps(['ceph_fs_cephfs_share'])}",
            ],
        )
        addresses = _planned_resource_addresses(terraform_controller.show_plan_json(plan_file))
        assert CEPH_FS_OFFER_ADDRESS in addresses

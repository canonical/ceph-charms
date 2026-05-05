# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the top-level Terraform component module."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import jubilant
import pytest

from tests import helpers

logger = logging.getLogger(__name__)
pytestmark = pytest.mark.slow

REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())
COMPONENT_MODULE_SOURCE = f"{REPO_ROOT}//terraform"

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

TF_TIMEOUT_INIT = 10 * 60
TF_TIMEOUT_PLAN = 10 * 60
TF_TIMEOUT_SHOW = 5 * 60
TF_TIMEOUT_APPLY = 45 * 60
TF_TIMEOUT_DESTROY = 8 * 60
TF_TIMEOUT_DESTROY_RETRY = 3 * 60

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
MODEL_TARGET_VALIDATION_ERROR = "Exactly one model target must be provided"
TEST_MODEL_UUID = "00000000-0000-0000-0000-000000000001"


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


def _wait_for_core_apps_or_skip_storage_constraints(
    juju: jubilant.Juju,
    *apps: str,
    timeout: int = helpers.DEFAULT_TIMEOUT,
) -> jubilant.Status:
    """Wait for core apps to become active, skipping known host storage constraints."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        status = juju.status()
        if jubilant.all_active(status, *apps):
            return status
        if helpers.has_storage_error(status, LOOP_DEVICE_ERROR):
            pytest.skip("Runner has no free loop devices for ceph-osd storage directives")
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


def _collect_plan_addresses(module: dict[str, Any], addresses: set[str]) -> None:
    for resource in module.get("resources", []):
        address = resource.get("address")
        if isinstance(address, str):
            addresses.add(address)

    for child in module.get("child_modules", []):
        if isinstance(child, dict):
            _collect_plan_addresses(child, addresses)


def _planned_resource_addresses(plan_json: dict[str, Any]) -> set[str]:
    addresses: set[str] = set()
    planned_values = plan_json.get("planned_values", {})
    root_module = planned_values.get("root_module", {})
    if isinstance(root_module, dict):
        _collect_plan_addresses(root_module, addresses)
    return addresses


def _planned_output_value(plan_json: dict[str, Any], output_name: str) -> Any:
    planned_values = plan_json.get("planned_values", {})
    outputs = planned_values.get("outputs", {})
    if isinstance(outputs, dict):
        output = outputs.get(output_name, {})
        if isinstance(output, dict) and "value" in output:
            return output["value"]

    output_changes = plan_json.get("output_changes", {})
    if isinstance(output_changes, dict):
        output = output_changes.get(output_name, {})
        if isinstance(output, dict) and "after" in output:
            return output["after"]

    raise KeyError(f"Planned output {output_name!r} not found in terraform plan JSON")


def _workspace_main(module_source: str) -> str:
    # Keep test workspace minimal and self-contained so each module test run can
    # pass optional relation descriptors via plain JSON `-var` arguments.
    # The source is a package root + subdir (`<repo>//terraform`) so nested
    # leaf-module relative paths remain inside the same Terraform module package.
    return f'''
terraform {{
  required_version = ">= 1.6"

  required_providers {{
    juju = {{
      source  = "juju/juju"
      version = ">= 1.0.0"
    }}
  }}
}}

provider "juju" {{}}

variable "model_name" {{
  type    = string
  default = null
}}

variable "model_uuid" {{
  type    = string
  default = null
}}

variable "charm_channel" {{
  type    = string
  default = null
}}

variable "bootstrap_source" {{
  type    = any
  default = null
}}

variable "secrets_storage" {{
  type    = any
  default = null
}}

variable "expose_endpoints" {{
  type    = list(string)
  default = []
}}

variable "ceph_radosgw" {{
  type    = any
  default = {{}}
}}

module "ceph" {{
  source = {json.dumps(str(module_source))}

  model_name       = var.model_name
  model_uuid       = var.model_uuid
  bootstrap_source = var.bootstrap_source
  secrets_storage  = var.secrets_storage
  expose_endpoints = var.expose_endpoints

  ceph_mon = merge({{
    units = 3
    config = {{
      "expected-osd-count" = "3"
      "monitor-count"      = "3"
    }}
  }}, var.charm_channel == null ? {{}} : {{
    channel = var.charm_channel
  }})

  ceph_osd = merge({{
    units = 3
    storage_directives = {{
      "osd-devices" = "1G,1"
    }}
  }}, var.charm_channel == null ? {{}} : {{
    channel = var.charm_channel
  }})

  ceph_radosgw = merge(
    var.ceph_radosgw,
    var.charm_channel == null ? {{}} : {{
      channel = var.charm_channel
    }},
  )
}}

output "components" {{
  value = module.ceph.components
}}

output "components_map" {{
  value = module.ceph.components_map
}}

output "offers" {{
  value = module.ceph.offers
}}

output "provides" {{
  value = module.ceph.provides
}}

output "requires" {{
  value = module.ceph.requires
}}
'''


class TerraformController:
    """Convenience wrapper around Terraform CLI operations for this test suite."""

    def __init__(self, workspace: Path, env: dict[str, str]):
        self._workspace = workspace
        self._env = env

    @property
    def workspace(self) -> Path:
        return self._workspace

    def _run(
        self,
        *args: str,
        check: bool = True,
        timeout: int | None = None,
        log_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = ["terraform", *args]

        try:
            result = subprocess.run(
                command,
                cwd=self._workspace,
                env=self._env,
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout or ""
            stderr = exc.stderr or ""
            if stdout:
                logger.info(stdout)
            if stderr:
                logger.warning(stderr)
            logger.warning(
                "Terraform command timed out after %ss: %s",
                timeout,
                " ".join(command),
            )
            raise

        if log_output and result.stdout:
            logger.info(result.stdout)
        if log_output and result.stderr:
            logger.warning(result.stderr)
        if check and result.returncode != 0:
            raise subprocess.CalledProcessError(
                returncode=result.returncode,
                cmd=command,
                output=result.stdout,
                stderr=result.stderr,
            )
        return result

    def init(self) -> None:
        self._run("init", "-input=false", "-no-color", timeout=TF_TIMEOUT_INIT)

    def plan(self, *, out: str = "tfplan", extra_args: list[str] | None = None) -> None:
        args = ["plan", "-input=false", "-no-color", "-out", out]
        if extra_args:
            args.extend(extra_args)
        self._run(*args, timeout=TF_TIMEOUT_PLAN)

    def show_plan(self, plan_file: str = "tfplan") -> None:
        self._run("show", "-no-color", plan_file, timeout=TF_TIMEOUT_SHOW)

    def show_plan_json(self, plan_file: str = "tfplan") -> dict[str, Any]:
        result = self._run(
            "show",
            "-json",
            plan_file,
            timeout=TF_TIMEOUT_SHOW,
            log_output=False,
        )
        return json.loads(result.stdout)

    def apply(self) -> None:
        self.plan(out="tfplan")
        self._run(
            "apply",
            "-input=false",
            "-no-color",
            "-auto-approve",
            "tfplan",
            timeout=TF_TIMEOUT_APPLY,
        )

    def destroy(self) -> None:
        try:
            result = self._run(
                "destroy",
                "-input=false",
                "-no-color",
                "-auto-approve",
                check=False,
                timeout=TF_TIMEOUT_DESTROY,
            )
        except subprocess.TimeoutExpired:
            logger.warning("terraform destroy timed out")
            return

        if result.returncode == 0:
            return

        logger.warning("terraform destroy exited with %s; retrying once", result.returncode)

        try:
            retry_result = self._run(
                "destroy",
                "-input=false",
                "-no-color",
                "-auto-approve",
                check=False,
                timeout=TF_TIMEOUT_DESTROY_RETRY,
            )
        except subprocess.TimeoutExpired:
            logger.warning("terraform destroy retry timed out")
            return

        if retry_result.returncode != 0:
            logger.warning("terraform destroy retry exited with %s", retry_result.returncode)

    def state_list(self) -> set[str]:
        result = self._run("state", "list", timeout=TF_TIMEOUT_SHOW)
        return {line.strip() for line in result.stdout.splitlines() if line.strip()}

    def outputs(self) -> dict[str, Any]:
        result = self._run("output", "-json", timeout=TF_TIMEOUT_SHOW)
        return json.loads(result.stdout)


@pytest.fixture(scope="module")
def terraform_controller(
    juju: jubilant.Juju, request: pytest.FixtureRequest
) -> TerraformController:
    """Provision an isolated Terraform workspace tied to the temporary Juju model."""
    model_name = juju.model
    if not model_name:
        raise ValueError("Juju model name unavailable")

    workspace = Path(tempfile.mkdtemp(prefix="ceph-terraform-it-"))

    (workspace / "main.tf").write_text(_workspace_main(COMPONENT_MODULE_SOURCE))

    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    env["JUJU_MODEL"] = model_name
    env["TF_VAR_model_name"] = model_name

    controller = TerraformController(workspace, env)
    controller.init()

    keep_models = bool(request.config.getoption("--keep-models"))
    keep_workspace = bool(request.config.getoption("--keep-terraform-workspace"))

    yield controller

    if request.session.testsfailed:
        subprocess.run(["juju", "status", "-m", model_name], check=False)

    if keep_models:
        logger.info("Keeping model %s and Terraform resources for debugging", model_name)
    else:
        controller.destroy()

    if keep_models or keep_workspace:
        logger.info("Keeping Terraform workspace at %s", workspace)
    else:
        shutil.rmtree(workspace, ignore_errors=True)


@pytest.fixture(scope="module")
def applied_stack(terraform_controller: TerraformController, juju: jubilant.Juju) -> jubilant.Status:
    """Apply the default Terraform stack and wait for expected readiness."""
    terraform_controller.apply()

    _wait_for_core_apps_or_skip_storage_constraints(juju, *CORE_APPS)
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
        assert osd_storage_directives["osd-devices"] == "1G,1"

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

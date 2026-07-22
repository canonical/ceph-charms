# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared helpers for Terraform integration tests."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
from typing import Any

from tests import helpers

logger = logging.getLogger(__name__)

REPO_ROOT = helpers.find_repo_root(Path(__file__).resolve())
COMPONENT_MODULE_SOURCE = f"{REPO_ROOT}//terraform"

TF_TIMEOUT_INIT = 10 * 60
TF_TIMEOUT_PLAN = 15 * 60
TF_TIMEOUT_SHOW = 5 * 60
TF_TIMEOUT_APPLY = 90 * 60
TF_TIMEOUT_DESTROY = 20 * 60
TF_TIMEOUT_DESTROY_RETRY = 5 * 60


def collect_plan_addresses(module: dict[str, Any], addresses: set[str]) -> None:
    """Collect resource addresses recursively from a Terraform plan module."""
    for resource in module.get("resources", []):
        address = resource.get("address")
        if isinstance(address, str):
            addresses.add(address)

    for child in module.get("child_modules", []):
        if isinstance(child, dict):
            collect_plan_addresses(child, addresses)


def planned_resource_addresses(plan_json: dict[str, Any]) -> set[str]:
    """Return every resource address in a Terraform JSON plan."""
    addresses: set[str] = set()
    planned_values = plan_json.get("planned_values", {})
    root_module = planned_values.get("root_module", {})
    if isinstance(root_module, dict):
        collect_plan_addresses(root_module, addresses)
    return addresses


def planned_output_value(plan_json: dict[str, Any], output_name: str) -> Any:
    """Return a planned output value from either supported JSON location."""
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


def render_workspace_template(template_name: str, **replacements: str) -> str:
    """Render a checked-in HCL workspace template using literal tokens."""
    template_path = Path(__file__).parent / "workspaces" / template_name
    rendered = template_path.read_text()
    for token, value in replacements.items():
        rendered = rendered.replace(f"__{token}__", value)
    if "__" in rendered:
        raise ValueError(f"Unresolved token in Terraform template {template_name}")
    return rendered


def workspace_main(module_source: str = COMPONENT_MODULE_SOURCE) -> str:
    """Render the standard component-module test workspace."""
    # The source is a package root + subdir (``<repo>//terraform``), so nested
    # leaf-module relative paths remain inside one Terraform module package.
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

variable "juju_base" {{
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

variable "ceph_fs" {{
  type    = any
  default = null
}}

variable "ceph_nfs" {{
  type    = any
  default = null
}}

variable "ceph_nvme" {{
  type    = any
  default = null
}}

variable "ceph_rbd_mirror" {{
  type    = any
  default = null
}}

variable "ceph_dashboard" {{
  type    = any
  default = null
}}

variable "ceph_proxy" {{
  type    = any
  default = null
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
    }}, var.juju_base == null ? {{}} : {{
    base = var.juju_base
  }})

  ceph_osd = merge({{
    units = 3
    storage_directives = {{
      "osd-devices" = "10G,1"
    }}
    }}, var.charm_channel == null ? {{}} : {{
    channel = var.charm_channel
    }}, var.juju_base == null ? {{}} : {{
    base = var.juju_base
  }})

  ceph_radosgw = merge(
    var.ceph_radosgw,
    var.charm_channel == null ? {{}} : {{ channel = var.charm_channel }},
    var.juju_base == null ? {{}} : {{ base = var.juju_base }},
  )

  ceph_fs = var.ceph_fs == null ? null : merge(
    var.ceph_fs,
    var.charm_channel == null ? {{}} : {{ channel = var.charm_channel }},
    var.juju_base == null ? {{}} : {{ base = var.juju_base }},
  )
  ceph_nfs = var.ceph_nfs == null ? null : merge(
    var.ceph_nfs,
    var.charm_channel == null ? {{}} : {{ channel = var.charm_channel }},
    var.juju_base == null ? {{}} : {{ base = var.juju_base }},
  )
  ceph_nvme = var.ceph_nvme == null ? null : merge(
    var.ceph_nvme,
    var.charm_channel == null ? {{}} : {{ channel = var.charm_channel }},
    var.juju_base == null ? {{}} : {{ base = var.juju_base }},
  )
  ceph_rbd_mirror = var.ceph_rbd_mirror == null ? null : merge(
    var.ceph_rbd_mirror,
    var.charm_channel == null ? {{}} : {{ channel = var.charm_channel }},
    var.juju_base == null ? {{}} : {{ base = var.juju_base }},
  )
  ceph_dashboard = var.ceph_dashboard == null ? null : merge(
    var.ceph_dashboard,
    var.charm_channel == null ? {{}} : {{ channel = var.charm_channel }},
    var.juju_base == null ? {{}} : {{ base = var.juju_base }},
  )
  ceph_proxy = var.ceph_proxy == null ? null : merge(
    var.ceph_proxy,
    var.charm_channel == null ? {{}} : {{ channel = var.charm_channel }},
    var.juju_base == null ? {{}} : {{ base = var.juju_base }},
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
    """Convenience wrapper around Terraform CLI operations."""

    def __init__(self, workspace: Path, env: dict[str, str]):
        self._workspace = workspace
        self._env = env
        self._redactions: set[str] = set()

    @property
    def workspace(self) -> Path:
        return self._workspace

    def add_redaction(self, value: str) -> None:
        """Redact a secret and its non-empty lines from Terraform output."""
        self._redactions.update(line for line in value.splitlines() if line)

    def _redact(self, value: str) -> str:
        for secret in sorted(self._redactions, key=len, reverse=True):
            value = value.replace(secret, "<redacted>")
        return value

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
            raw_stdout = exc.stdout or ""
            raw_stderr = exc.stderr or ""
            stdout = self._redact(
                raw_stdout.decode(errors="replace")
                if isinstance(raw_stdout, bytes)
                else raw_stdout
            )
            stderr = self._redact(
                raw_stderr.decode(errors="replace")
                if isinstance(raw_stderr, bytes)
                else raw_stderr
            )
            if stdout:
                logger.info(stdout)
            if stderr:
                logger.warning(stderr)
            logger.warning(
                "Terraform command timed out after %ss: %s",
                timeout,
                " ".join(command),
            )
            exc.stdout = stdout
            exc.stderr = stderr
            raise

        result = subprocess.CompletedProcess(
            args=result.args,
            returncode=result.returncode,
            stdout=self._redact(result.stdout),
            stderr=self._redact(result.stderr),
        )

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

    def write_tfvars(self, values: dict[str, Any]) -> None:
        """Write scenario variables without exposing secrets in command lines."""
        path = self._workspace / "terraform.auto.tfvars.json"
        path.write_text(json.dumps(values, indent=2, sort_keys=True) + "\n")
        path.chmod(0o600)

    def init(self) -> None:
        self._run("init", "-input=false", "-no-color", timeout=TF_TIMEOUT_INIT)

    def plan(self, *, out: str = "tfplan", extra_args: list[str] | None = None) -> None:
        args = ["plan", "-input=false", "-no-color", "-out", out]
        if extra_args:
            args.extend(extra_args)
        self._run(*args, timeout=TF_TIMEOUT_PLAN)

    def assert_no_changes(self) -> None:
        """Assert that the applied Terraform workspace has converged."""
        result = self._run(
            "plan",
            "-input=false",
            "-no-color",
            "-detailed-exitcode",
            check=False,
            timeout=TF_TIMEOUT_PLAN,
        )
        if result.returncode == 2:
            raise AssertionError("Terraform reported changes after the smoke test")
        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                returncode=result.returncode,
                cmd=result.args,
                output=result.stdout,
                stderr=result.stderr,
            )

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

    def apply(self, *, plan_file: str = "tfplan") -> None:
        self.plan(out=plan_file)
        self._run(
            "apply",
            "-input=false",
            "-no-color",
            "-auto-approve",
            plan_file,
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


def terraform_environment(model_name: str) -> dict[str, str]:
    """Return the Terraform environment for an integration test model."""
    env = os.environ.copy()
    env["TF_IN_AUTOMATION"] = "1"
    env["JUJU_MODEL"] = model_name
    env["TF_VAR_model_name"] = model_name
    if juju_base := env.get("JUJU_BASE"):
        env.setdefault("TF_VAR_juju_base", juju_base)
    return env


def assert_component_names(controller: TerraformController, expected: set[str]) -> None:
    """Assert both component outputs contain exactly the expected applications."""
    outputs = controller.outputs()
    components = outputs["components"]["value"]
    assert {component["name"] for component in components} == expected

    components_map = outputs["components_map"]["value"]
    assert {component["name"] for component in components_map.values()} == expected

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tests for Terraform integration test configuration."""

import subprocess

import pytest

from tests.integration.terraform.helpers import (
    TerraformController,
    terraform_environment,
    workspace_main,
)


def test_workspace_forwards_juju_base_to_all_ceph_applications() -> None:
    """The generated root module should override every Ceph application base."""
    workspace = workspace_main("./terraform")

    assert 'variable "juju_base"' in workspace
    assert workspace.count("var.juju_base == null") == 9
    assert workspace.count("base = var.juju_base") == 9


def test_workspace_forwards_channel_to_all_ceph_applications() -> None:
    """The generated root module should override every Ceph application channel."""
    workspace = workspace_main("./terraform")

    assert 'variable "charm_channel"' in workspace
    assert workspace.count("var.charm_channel == null") == 9
    assert workspace.count("channel = var.charm_channel") == 9


def test_juju_base_environment_maps_to_terraform_variable(monkeypatch) -> None:
    """JUJU_BASE should be usable without exposing Terraform internals to callers."""
    monkeypatch.setenv("JUJU_BASE", "ubuntu@26.04")
    monkeypatch.delenv("TF_VAR_juju_base", raising=False)

    env = terraform_environment("controller:model")

    assert env["TF_VAR_juju_base"] == "ubuntu@26.04"


def test_timeout_redaction_handles_byte_output(tmp_path, monkeypatch) -> None:
    """Partial timeout output may be bytes even when subprocess text mode is enabled."""
    controller = TerraformController(tmp_path, {})
    controller.add_redaction("secret")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 1, output=b"secret", stderr=b"secret")

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
        controller._run("plan", timeout=1)

    assert exc_info.value.stdout == "<redacted>"
    assert exc_info.value.stderr == "<redacted>"


def test_explicit_terraform_juju_base_takes_precedence(monkeypatch) -> None:
    """An explicit Terraform variable should take precedence over the convenience input."""
    monkeypatch.setenv("JUJU_BASE", "ubuntu@26.04")
    monkeypatch.setenv("TF_VAR_juju_base", "ubuntu@24.04")

    env = terraform_environment("controller:model")

    assert env["TF_VAR_juju_base"] == "ubuntu@24.04"

# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Terraform-specific pytest fixtures."""

from __future__ import annotations

from collections.abc import Callable, Iterator
import logging
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

import jubilant
import pytest

from tests.integration.terraform.helpers import (
    TerraformController,
    terraform_environment,
)

logger = logging.getLogger(__name__)

TerraformControllerFactory = Callable[
    [str, dict[str, Any] | None, str], TerraformController
]


@pytest.fixture(scope="module")
def terraform_controller_factory(
    juju: jubilant.Juju,
    request: pytest.FixtureRequest,
) -> Iterator[TerraformControllerFactory]:
    """Create initialized Terraform workspaces tied to the module's Juju model."""
    model_name = juju.model
    if not model_name:
        raise ValueError("Juju model name unavailable")

    controllers: list[TerraformController] = []
    workspaces: list[Path] = []

    def create(
        main_tf: str,
        tfvars: dict[str, Any] | None = None,
        prefix: str = "ceph-terraform-it-",
    ) -> TerraformController:
        workspace = Path(tempfile.mkdtemp(prefix=prefix))
        workspaces.append(workspace)
        (workspace / "main.tf").write_text(main_tf)

        controller = TerraformController(workspace, terraform_environment(model_name))
        if tfvars is not None:
            controller.write_tfvars(tfvars)
        controller.init()
        controllers.append(controller)
        return controller

    yield create

    if request.session.testsfailed:
        subprocess.run(["juju", "status", "-m", model_name, "--relations"], check=False)

    keep_models = bool(request.config.getoption("--keep-models"))
    keep_workspace = bool(request.config.getoption("--keep-terraform-workspace"))

    if keep_models:
        logger.info("Keeping model %s and Terraform resources for debugging", model_name)
    else:
        for controller in reversed(controllers):
            controller.destroy()

    if keep_models or keep_workspace:
        for workspace in workspaces:
            logger.info("Keeping Terraform workspace at %s", workspace)
    else:
        for workspace in workspaces:
            shutil.rmtree(workspace, ignore_errors=True)

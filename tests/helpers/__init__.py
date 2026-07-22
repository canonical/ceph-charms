# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared helpers for repo-level integration tests."""

import shutil
import subprocess
import time
from pathlib import Path

import jubilant

DEFAULT_TIMEOUT = 60 * 30


def find_repo_root(start: Path) -> Path:
    """Locate the repository root from *start*."""
    search_start = start if start.is_dir() else start.parent

    if git_executable := shutil.which("git"):
        try:
            result = subprocess.run(
                [git_executable, "rev-parse", "--show-toplevel"],
                cwd=search_start,
                check=True,
                capture_output=True,
                text=True,
            )
            return Path(result.stdout.strip())
        except (subprocess.CalledProcessError, OSError):
            pass

    for path in (search_start, *search_start.parents):
        if (path / ".git").exists() and (path / "terraform").is_dir():
            return path

    raise FileNotFoundError("Could not find repository root")


def has_storage_error(status: jubilant.Status, message_substring: str) -> bool:
    """Return whether model storage contains *message_substring* in status messages."""
    for storage_info in status.storage.storage.values():
        message = storage_info.status.message or ""
        if message_substring in message:
            return True
    return False


def relation_exists(
    status: jubilant.Status,
    *,
    app: str,
    endpoint: str,
    related_app: str,
) -> bool:
    """Return whether app:endpoint is related to *related_app*."""
    app_status = status.apps.get(app)
    if not app_status:
        return False

    relations = app_status.relations.get(endpoint, [])
    return any(relation.related_app == related_app for relation in relations)


def app_status_summary(status: jubilant.Status, app: str) -> str:
    """Return a compact summary of application and unit statuses."""
    app_status = status.apps.get(app)
    if not app_status:
        return f"{app}:missing"

    unit_parts = []
    for unit_name, unit_status in sorted(app_status.units.items()):
        workload = unit_status.workload_status.current
        workload_message = unit_status.workload_status.message or ""
        agent = unit_status.juju_status.current
        unit_parts.append(
            f"{unit_name}[workload={workload!s}:{workload_message!s},agent={agent!s}]"
        )

    units_summary = ", ".join(unit_parts) if unit_parts else "no-units"
    return (
        f"{app}[status={app_status.app_status.current}:{app_status.app_status.message or ''};"
        f" units={units_summary}]"
    )


def wait_for_apps_active(
    juju: jubilant.Juju,
    *apps: str,
    timeout: int = DEFAULT_TIMEOUT,
    poll_interval: int = 10,
) -> jubilant.Status:
    """Wait until all *apps* are active and return the last observed status."""
    deadline = time.time() + timeout

    while time.time() < deadline:
        status = juju.status()
        if jubilant.all_active(status, *apps):
            return status
        target_error = any(
            app_status.app_status.current == "error"
            or any(
                unit_status.workload_status.current == "error"
                for unit_status in app_status.units.values()
            )
            for app in apps
            if (app_status := status.apps.get(app)) is not None
        )
        if target_error or jubilant.any_error(status):
            raise AssertionError(f"Juju reported an error status: {status}")
        time.sleep(poll_interval)

    raise TimeoutError(f"Timed out waiting for applications to become active: {apps}")

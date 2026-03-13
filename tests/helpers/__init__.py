# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared helpers for repo-level integration tests."""

import time
from pathlib import Path

import jubilant

DEFAULT_TIMEOUT = 60 * 30


def find_repo_root(start: Path) -> Path:
    """Locate the repository root from *start*."""
    for path in (start, *start.parents):
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
        if jubilant.any_error(status):
            raise AssertionError(f"Juju reported an error status: {status}")
        time.sleep(poll_interval)

    raise TimeoutError(f"Timed out waiting for applications to become active: {apps}")

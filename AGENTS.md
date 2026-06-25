# AGENTS.md

## Layout

- Monorepo. Each charm lives in its own top-level directory (`ceph-mon/`, `ceph-osd/`, `ceph-radosgw/`, `ceph-fs/`, `ceph-dashboard/`, `ceph-nfs/`, `ceph-nvme/`, `ceph-proxy/`, `ceph-rbd-mirror/`) with its own `tox.ini`, `charmcraft.yaml`, `src/`, `unit_tests/`, `tests/`.
- `charms.ceph/` — shared Python library (`charms_ceph`) vendored into each charm at build time. Edit here, not inside an individual charm's `lib/`.
- `constraints/` — shared pip constraint files used by functional/integration envs.
- `terraform/` — top-level Terraform module composing per-charm leaf modules under `<charm>/terraform/`.
- `tests/` at repo root — shared scripts (e.g. `tests/scripts/actionutils.sh`) used by CI, not a test suite.

## Commit conventions

- Commits must be signed off (`Signed-off-by:` trailer) **by the human**. Agents must never add a `Signed-off-by:` trailer on the human's behalf — the DCO sign-off is an attestation only the human can make.
- Agents must include an `Assisted-by:` trailer identifying the agent and model.
- Order trailers as: `Assisted-by:` first, then the human's `Signed-off-by:` last (added by the human).

Format:

```
Assisted-by: AGENT_NAME:MODEL_VERSION
```

- `AGENT_NAME` — the AI tool or framework (e.g. `claude-code`, `opencode`, `codex`, `pi`, …).
- `MODEL_VERSION` — the specific model version used (e.g. `claude-sonnet-4-6`, `gpt-5.5`).

Example:

```
Assisted-by: opencode:gpt-5.5
```

Other commit rules:

- Commit messages must be ASCII only.
- Keep PRs small and focused; don't mix trivial and controversial changes.
- Squash into logical commits (API / docs / CLI / daemon / tests / CI) for non-trivial PRs.
- Maintain a linear git history.

## Pull requests

- Pull request descriptions must always conform to the project's PR template at [.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md). Fill in every section of the template rather than replacing it with free-form text.

## Build

From the repo root, build a single charm:

```
tox -c <charm-dir> -e build
```

This runs `charmcraft clean && charmcraft -v pack && ./rename.sh`. Requires `charmcraft` installed (`sudo snap install charmcraft --classic`).

## Unit tests and lint

Per-charm, from the repo root. All nine charms expose the same envs:

```
tox -c <charm-dir> -e pep8     # lint (flake8)
tox -c <charm-dir> -e py3      # unit tests (stestr)
```

# Plan: Local Development Infrastructure for Multi-Worktree Workflows

## Objective

Create a clean, repeatable development flow where every worktree can independently:

- Install Python and Node dependencies
- Avoid global packages
- Run tests and linting through project-managed tools
- Run pipeline commands reliably
- Start the web app on a Conductor-provided port
- Fail early with actionable environment errors
- Match CI behavior

This plan is independent of the parking-ticket pipeline implementation.

## Current Problems

- `pipeline/pyproject.toml` exists, but there is no lockfile or Python version declaration.
- `scripts/setup.sh` uses whichever `python3` is first on `PATH`; this machine currently reports Python `3.9.6`, below the project's required `3.12+`.
- Setup creates a root `.venv`, but the root `.gitignore` does not ignore `.venv/`.
- Dependency installation upgrades tools dynamically and can attempt source builds.
- `pyarrow` is not currently declared.
- Bare commands such as `pytest` and `ruff` can resolve to unrelated global installations.
- `web/package-lock.json` exists, but no Node version file exists.
- Node and npm are currently unavailable on this machine's `PATH`.
- Vite does not currently read a `PORT` environment variable.
- CI uses direct `pip install` and bare test commands rather than the same local bootstrap flow.
- Tests can run with sample data, but there is no explicit environment or data preflight command.

## Tooling Decision

Standardize Python environments on `uv`.

Reasons:

- `uv` is already available in the current environment.
- It creates isolated environments per worktree.
- `uv.lock` provides transitive dependency locking.
- It can manage or select supported Python versions.
- It supports wheel-only installation and can fail before attempting source builds.
- `uv run` eliminates dependence on activated shells or global executables.

Use npm with the existing `package-lock.json` for the web project.

## Python Environment

### Version and Locking

Add:

- `pipeline/.python-version`, selecting the local baseline Python version
- `pipeline/uv.lock`

Keep `pipeline/pyproject.toml` as the Python project definition.

The supported Python range should remain explicit and match CI. The local baseline should be a specific supported version, preferably one with known wheels for all geospatial dependencies.

Pin:

- `pyarrow`
- Runtime dependencies
- Development dependencies
- Build dependencies as resolved by the lockfile

Dependency updates must be intentional through `uv lock` rather than implicit upgrades during setup.

### Worktree Isolation

Use one environment per worktree:

```text
<worktree>/pipeline/.venv/
```

Do not share virtual environments between worktrees. Allow `uv`'s global package cache to be shared for efficiency.

Add the root `.venv/` ignore entry even if the final flow uses `pipeline/.venv/`, eliminating ambiguity with the existing setup script and older documentation.

### Wheel Safety

Configure installation to prefer or require binary wheels for third-party dependencies, especially:

- `pyarrow`
- `numpy`
- `pandas`
- `geopandas`
- `shapely`
- `pyproj`
- `pyogrio`

If a compatible wheel is unavailable, setup should fail with a clear message naming the package, Python version, and platform. It must not silently fall back to a CMake or compiler-based source build.

## Setup Command

Revise `scripts/setup.sh` into an idempotent repository bootstrap.

It should:

1. Resolve the repository root from the script location.
2. Check for `uv`.
3. Select the configured Python version.
4. Verify the interpreter satisfies the project requirement.
5. Create or synchronize `pipeline/.venv` from `pipeline/uv.lock`.
6. Install the development dependency set with locked versions.
7. Install web dependencies with `npm ci`.
8. Run `pip check` or the equivalent environment consistency check.
9. Import-check key packages: `pytest`, `pyarrow`, `pandas`, `geopandas`, and `shapely`.
10. Verify `parking-run --help` and `parking-tickets --help` once that command exists.
11. Print the canonical test and run commands.

The script must not use global `pip`, `pytest`, or `ruff`.

## Environment Doctor

Add a script such as `scripts/check-dev-environment.sh`.

It should report:

- Repository and worktree path
- Python executable and version
- `uv` version
- Node and npm versions
- Lockfile presence
- Python environment health
- Required package imports
- Console scripts
- Optional TCL/Road Edge data availability
- Current `PORT` and `HOST` values

It should distinguish between:

- Requirements for unit tests
- Requirements for full pipeline execution
- Requirements for web development

It should return nonzero for missing mandatory tooling and provide actionable remediation commands.

## Canonical Commands

Document commands that work without shell activation:

```bash
./scripts/setup.sh
uv run --project pipeline pytest
uv run --project pipeline ruff check src tests scripts
uv run --project pipeline parking-run --help
uv run --project pipeline parking-tickets --help
npm --prefix web test
npm --prefix web run build
```

Activation may remain supported, but should not be required.

## Node Environment

Add a Node version declaration, preferably:

- `web/.nvmrc` containing the supported major version
- `engines.node` in `web/package.json`

Use the existing `web/package-lock.json` as the dependency source of truth.

The setup flow should use:

```bash
npm --prefix web ci
```

If Node is missing or outside the supported version, fail with a clear message rather than attempting to continue.

## Port Configuration

Update Vite configuration to read:

- `PORT`, defaulting to `5173`
- `HOST`, defaulting to a safe local host

Both development and preview servers must honor the values.

Expected workflow:

```bash
PORT=4173 HOST=127.0.0.1 npm --prefix web run dev
```

The configured port should be validated as an integer in the valid range. This development-infrastructure change is independent of ticket UI integration.

Add a smoke check that starts Vite on a non-default port and verifies that the process binds successfully.

## Data Preflight

Do not download large Toronto datasets during ordinary setup.

The environment doctor should report whether these optional runtime files exist:

- TCL streets
- TCL intersections
- TCL street names
- Road Edge GeoPackage
- Ticket and address snapshots when applicable

Unit tests must continue using committed sample fixtures and must not require network access.

Full-data acquisition remains an explicit operator action.

## CI Alignment

Update CI to use the same locked environment strategy:

- Install `uv`
- Use the repository's Python version declarations
- Run `uv sync --locked`
- Run tests and lint through `uv run`
- Verify required wheel availability
- Use the Node version declaration for web CI
- Continue using `npm ci`

CI should include a fresh-environment bootstrap check so dependency failures are caught before application tests.

## Tests

Add or update tests for:

- Unsupported Python version failure
- Missing `uv` failure
- Missing Node/npm failure
- Missing or stale lockfile failure
- Required package import checks
- Console-script availability
- Wheel-only installation behavior
- `PORT` and `HOST` handling
- Invalid port values
- Vite binding to a supplied port
- Setup idempotency
- Sample-data versus full-data preflight behavior

## Documentation

Add a dedicated document such as:

```text
docs/local-development.md
```

Update:

- Root `README.md`
- `pipeline/README.md`
- `web/README.md`

Document:

- Supported Python and Node versions
- One-command worktree setup
- Canonical test commands
- Full pipeline prerequisites
- Conductor port usage
- Troubleshooting for missing wheels or interpreters
- Explicit prohibition on relying on global Python packages

## Acceptance Criteria

A fresh worktree must satisfy all of the following:

1. `./scripts/setup.sh` creates an isolated environment without relying on globally installed Python packages.
2. Unsupported Python versions fail before dependency installation.
3. `pyarrow` installs from a compatible wheel or fails clearly without attempting a source build.
4. `uv run ... pytest` and `uv run ... ruff` work without activation.
5. Python console scripts resolve from the worktree environment.
6. `npm ci` installs the locked web dependencies.
7. Node version mismatches are reported clearly.
8. `PORT=#### npm --prefix web run dev` starts Vite on the requested port.
9. CI and local setup use the same lockfiles and supported versions.
10. Unit tests work without downloaded city datasets or network access.

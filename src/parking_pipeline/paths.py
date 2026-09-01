"""Centralized path resolution for pipeline data and project assets."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("PARKING_DATA_DIR", PROJECT_ROOT / "data"))

# Static shared source files that should be read from root DATA_DIR if not present in branch dir
_SHARED_SOURCE_FILES = frozenset({
    "tcl_streets.geojson",
    "tcl_intersections.geojson",
    "topographic_road_edges.gpkg",
    "topographic_road_edges.manifest.json",
    "street_aliases.csv",
    "highway_aliases.csv",
    "curb_geometry_overrides.csv",
})


def current_git_branch() -> str:
    """Return active git branch name, or 'main' if git is unavailable / detached / in error."""
    override = os.environ.get("PARKING_GIT_BRANCH")
    if override:
        return override
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=PROJECT_ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return branch if branch and branch != "HEAD" else "main"
    except Exception:
        return "main"


def branch_data_dir() -> Path:
    """
    Return the active data directory for pipeline execution.
    - If PARKING_OUTPUT_DIR is set, use it.
    - If PARKING_BRANCH_OUTPUT is '0', 'false', or 'no', use root DATA_DIR.
    - If active branch is 'main' or 'master', use root DATA_DIR.
    - Otherwise, use DATA_DIR / 'branches' / <safe_branch_name>.
    """
    if custom := os.environ.get("PARKING_OUTPUT_DIR"):
        p = Path(custom)
    elif os.environ.get("PARKING_BRANCH_OUTPUT", "").lower() in {"0", "false", "no"}:
        p = DATA_DIR
    else:
        branch = current_git_branch()
        safe_branch = branch.replace("/", "_")
        if safe_branch in {"main", "master"}:
            p = DATA_DIR
        else:
            p = DATA_DIR / "branches" / safe_branch

    p.mkdir(parents=True, exist_ok=True)
    return p


def data_path(filename: str) -> Path:
    """
    Resolve a data file path.
    - If reading a shared source file that exists in root DATA_DIR (and not overridden in branch_dir), return root DATA_DIR.
    - Otherwise, return the path in active branch_data_dir().
    """
    b_dir = branch_data_dir()
    branch_target = b_dir / filename

    if branch_target.exists():
        return branch_target

    root_target = DATA_DIR / filename
    if (filename in _SHARED_SOURCE_FILES or filename.startswith("samples/")) and root_target.exists():
        return root_target

    return branch_target


def cache_dir() -> Path:
    """Return the active geometry cache directory."""
    b_dir = branch_data_dir()
    d = b_dir / ".geo_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d

"""Tests for branch-scoped path resolution in paths.py."""

from __future__ import annotations

from parking_pipeline.paths import (
    DATA_DIR,
    branch_data_dir,
    cache_dir,
    current_git_branch,
    data_path,
)


def test_current_git_branch_override(monkeypatch):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "feature-xyz")
    assert current_git_branch() == "feature-xyz"


def test_branch_data_dir_main(monkeypatch):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "main")
    monkeypatch.delenv("PARKING_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("PARKING_BRANCH_OUTPUT", raising=False)
    assert branch_data_dir() == DATA_DIR


def test_branch_data_dir_feature_branch(monkeypatch):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "data-enhancement")
    monkeypatch.delenv("PARKING_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("PARKING_BRANCH_OUTPUT", raising=False)
    expected = DATA_DIR / "branches" / "data-enhancement"
    assert branch_data_dir() == expected


def test_branch_data_dir_sanitizes_slashes(monkeypatch):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "feature/my-branch")
    monkeypatch.delenv("PARKING_OUTPUT_DIR", raising=False)
    monkeypatch.delenv("PARKING_BRANCH_OUTPUT", raising=False)
    expected = DATA_DIR / "branches" / "feature_my-branch"
    assert branch_data_dir() == expected


def test_branch_data_dir_explicit_output_dir(monkeypatch, tmp_path):
    custom = tmp_path / "custom_out"
    monkeypatch.setenv("PARKING_OUTPUT_DIR", str(custom))
    assert branch_data_dir() == custom


def test_branch_data_dir_disabled(monkeypatch):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "feature-xyz")
    monkeypatch.setenv("PARKING_BRANCH_OUTPUT", "false")
    assert branch_data_dir() == DATA_DIR


def test_data_path_resolves_branch_target(monkeypatch, tmp_path):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "test-branch")
    b_dir = branch_data_dir()
    assert data_path("final_parking_map.geojson") == b_dir / "final_parking_map.geojson"
    assert data_path("parking_map.sqlite") == b_dir / "parking_map.sqlite"
    assert data_path("parking_data_manifest.json") == b_dir / "parking_data_manifest.json"


def test_data_path_shared_source_fallback(monkeypatch):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "test-branch")
    # Shared source file that exists in root DATA_DIR should fall back to root DATA_DIR if absent in branch dir
    sample_file = "samples/tcl_streets.geojson"
    if (DATA_DIR / sample_file).exists():
        assert data_path(sample_file) == DATA_DIR / sample_file


def test_cache_dir(monkeypatch):
    monkeypatch.setenv("PARKING_GIT_BRANCH", "test-branch")
    b_dir = branch_data_dir()
    assert cache_dir() == b_dir / ".geo_cache"

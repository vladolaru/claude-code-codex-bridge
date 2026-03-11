"""Tests for GlobalSkillRegistry projects list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME, GlobalSkillRegistry


def test_registry_round_trips_with_projects(tmp_path: Path):
    """Registry with projects list serializes and deserializes correctly."""
    registry = GlobalSkillRegistry(skills={}, projects=(Path("/a/project-a"), Path("/b/project-b")))
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(registry.to_json())

    loaded = GlobalSkillRegistry.from_path(path)
    assert loaded is not None
    assert loaded.projects == (Path("/a/project-a"), Path("/b/project-b"))


def test_registry_missing_projects_key_treated_as_empty(tmp_path: Path):
    """A version 1 registry file without a projects key loads with empty projects."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(json.dumps({"version": 1, "skills": {}}, indent=2) + "\n")

    loaded = GlobalSkillRegistry.from_path(path)
    assert loaded is not None
    assert loaded.projects == ()


def test_registry_projects_sorted_on_load(tmp_path: Path):
    """Projects list is sorted on load for determinism."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {"version": 1, "skills": {}, "projects": ["/z/last", "/a/first"]}
    path.write_text(json.dumps(data, indent=2) + "\n")

    loaded = GlobalSkillRegistry.from_path(path)
    assert loaded is not None
    assert loaded.projects == (Path("/a/first"), Path("/z/last"))


def test_registry_rejects_relative_project_paths(tmp_path: Path):
    """Relative paths in the projects list are rejected."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {"version": 1, "skills": {}, "projects": ["relative/path"]}
    path.write_text(json.dumps(data, indent=2) + "\n")

    with pytest.raises(ReconcileError):
        GlobalSkillRegistry.from_path(path)

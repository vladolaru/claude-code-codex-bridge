"""Tests for GlobalSkillRegistry projects list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.registry import (
    GLOBAL_REGISTRY_FILENAME,
    GlobalAgentEntry,
    GlobalSkillRegistry,
    hash_agent_file,
)


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


def test_registry_tracks_agent_files(tmp_path: Path):
    """Global registry stores agent file entries alongside skills."""
    registry = GlobalSkillRegistry(
        skills={},
        agents={
            "market-plugin-reviewer.toml": GlobalAgentEntry(
                content_hash="sha256:abc123",
                owners=(Path("/a/project"),),
            ),
        },
    )
    assert "market-plugin-reviewer.toml" in registry.agents
    assert registry.agents["market-plugin-reviewer.toml"].content_hash == "sha256:abc123"


def test_registry_round_trips_with_agents(tmp_path: Path):
    """Registry with agents serializes and deserializes correctly."""
    registry = GlobalSkillRegistry(
        skills={},
        agents={
            "market-plugin-reviewer.toml": GlobalAgentEntry(
                content_hash="sha256:abc123",
                owners=(Path("/a/project"),),
            ),
        },
        projects=(Path("/a/project"),),
    )
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(registry.to_json())

    loaded = GlobalSkillRegistry.from_path(path)
    assert loaded is not None
    assert "market-plugin-reviewer.toml" in loaded.agents
    assert loaded.agents["market-plugin-reviewer.toml"].content_hash == "sha256:abc123"
    assert loaded.agents["market-plugin-reviewer.toml"].owners == (Path("/a/project"),)


def test_registry_backwards_compatible_without_agents(tmp_path: Path):
    """Registries from before agent tracking load with empty agents dict."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(json.dumps({"version": 1, "skills": {}}, indent=2) + "\n")

    loaded = GlobalSkillRegistry.from_path(path)
    assert loaded is not None
    assert loaded.agents == {}


def test_hash_agent_file_is_deterministic():
    """Agent file content hash is stable and deterministic."""
    content = 'name = "reviewer"\ndescription = "Review"\n'
    hash1 = hash_agent_file(content)
    hash2 = hash_agent_file(content)
    assert hash1 == hash2
    assert hash1.startswith("sha256:")
    assert len(hash1) > len("sha256:")

    # Different content produces different hash
    different = hash_agent_file('name = "other"\n')
    assert different != hash1

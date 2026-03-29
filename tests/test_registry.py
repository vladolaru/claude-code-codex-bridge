"""Tests for GlobalResourceRegistry projects list."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.registry import (
    GLOBAL_REGISTRY_FILENAME,
    GlobalAgentEntry,
    GlobalMcpServerEntry,
    GlobalPluginResourceEntry,
    GlobalPromptEntry,
    GlobalSkillEntry,
    GlobalResourceRegistry,
    hash_agent_file,
    hash_prompt_content,
)


def test_registry_round_trips_with_projects(tmp_path: Path):
    """Registry with projects list serializes and deserializes correctly."""
    registry = GlobalResourceRegistry(skills={}, projects=(Path("/a/project-a"), Path("/b/project-b")))
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(registry.to_json())

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert loaded.projects == (Path("/a/project-a"), Path("/b/project-b"))


def test_registry_missing_projects_key_treated_as_empty(tmp_path: Path):
    """A version 1 registry file without a projects key loads with empty projects."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(json.dumps({"version": 1, "skills": {}}, indent=2) + "\n")

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert loaded.projects == ()


def test_registry_projects_sorted_on_load(tmp_path: Path):
    """Projects list is sorted on load for determinism."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {"version": 1, "skills": {}, "projects": ["/z/last", "/a/first"]}
    path.write_text(json.dumps(data, indent=2) + "\n")

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert loaded.projects == (Path("/a/first"), Path("/z/last"))


def test_registry_rejects_relative_project_paths(tmp_path: Path):
    """Relative paths in the projects list are rejected."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {"version": 1, "skills": {}, "projects": ["relative/path"]}
    path.write_text(json.dumps(data, indent=2) + "\n")

    with pytest.raises(ReconcileError):
        GlobalResourceRegistry.from_path(path)


def test_registry_tracks_agent_files(tmp_path: Path):
    """Global registry stores agent file entries alongside skills."""
    registry = GlobalResourceRegistry(
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
    registry = GlobalResourceRegistry(
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

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert "market-plugin-reviewer.toml" in loaded.agents
    assert loaded.agents["market-plugin-reviewer.toml"].content_hash == "sha256:abc123"
    assert loaded.agents["market-plugin-reviewer.toml"].owners == (Path("/a/project"),)


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


def test_registry_round_trips_plugin_resources(tmp_path: Path):
    """Plugin resource entries survive serialization."""
    registry = GlobalResourceRegistry(
        skills={},
        plugin_resources={
            "market-tools": GlobalPluginResourceEntry(
                content_hash="sha256:abc123",
                owners=(Path("/project-a"),),
            ),
        },
    )
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(registry.to_json())
    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert "market-tools" in loaded.plugin_resources
    assert loaded.plugin_resources["market-tools"].content_hash == "sha256:abc123"
    assert loaded.plugin_resources["market-tools"].owners == (Path("/project-a"),)


def test_registry_missing_plugin_resources_key_treated_as_empty(tmp_path: Path):
    """A version 1 registry file without a plugin_resources key loads with empty dict."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(json.dumps({"version": 1, "skills": {}}, indent=2) + "\n")

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert loaded.plugin_resources == {}


def test_registry_plugin_resources_default_is_empty_dict():
    """Plugin resources defaults to empty dict when not provided."""
    registry = GlobalResourceRegistry(skills={})
    assert registry.plugin_resources == {}


def test_registry_rejects_invalid_plugin_resource_dir_name(tmp_path: Path):
    """Plugin resource dir names with path traversal or absolute paths are rejected."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {
        "version": 1,
        "skills": {},
        "plugin_resources": {
            "/absolute/path": {
                "content_hash": "sha256:abc123",
                "owners": ["/project-a"],
            },
        },
    }
    path.write_text(json.dumps(data, indent=2) + "\n")

    with pytest.raises(ReconcileError):
        GlobalResourceRegistry.from_path(path)


def test_registry_rejects_plugin_resource_dir_name_with_toml_suffix(tmp_path: Path):
    """Plugin resource dir names must not end in .toml (they are directories, not files)."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {
        "version": 1,
        "skills": {},
        "plugin_resources": {
            "bad-name.toml": {
                "content_hash": "sha256:abc123",
                "owners": ["/project-a"],
            },
        },
    }
    path.write_text(json.dumps(data, indent=2) + "\n")

    with pytest.raises(ReconcileError):
        GlobalResourceRegistry.from_path(path)


def test_registry_round_trips_prompts(tmp_path):
    """Registry serializes and deserializes prompts section."""
    content_hash = hash_prompt_content(b"---\ndescription: test\n---\n\nDo things.\n")
    owner = Path("/a/project")
    registry = GlobalResourceRegistry(
        skills={},
        prompts={
            "review.md": GlobalPromptEntry(
                content_hash=content_hash,
                owners=(owner,),
            ),
        },
    )
    json_str = registry.to_json()
    path = tmp_path / "registry.json"
    path.write_text(json_str)

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert "review.md" in loaded.prompts
    assert loaded.prompts["review.md"].content_hash == content_hash
    assert loaded.prompts["review.md"].owners == (owner,)


def test_registry_without_prompts_defaults_empty(tmp_path):
    """Old registries without prompts key parse with empty prompts dict."""
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "version": 1,
        "skills": {},
        "agents": {},
        "plugin_resources": {},
        "projects": [],
    }) + "\n")

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert loaded.prompts == {}


def test_hash_file_content_deterministic():
    """hash_file_content returns stable sha256 hash."""
    from cc_codex_bridge.registry import hash_file_content

    content = b"@AGENTS.md\n"
    h1 = hash_file_content(content)
    h2 = hash_file_content(content)
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_hash_file_content_different_for_different_content():
    """Different content produces different hashes."""
    from cc_codex_bridge.registry import hash_file_content

    assert hash_file_content(b"hello") != hash_file_content(b"world")


# ---------------------------------------------------------------------------
# MCP server registry support
# ---------------------------------------------------------------------------


def test_mcp_server_entry_construction():
    """GlobalMcpServerEntry stores content_hash and owners."""
    entry = GlobalMcpServerEntry(
        content_hash="sha256:abc123",
        owners=(Path("/a/project"),),
    )
    assert entry.content_hash == "sha256:abc123"
    assert entry.owners == (Path("/a/project"),)


def test_registry_round_trips_with_mcp_servers(tmp_path: Path):
    """Registry with mcp_servers serializes and deserializes correctly."""
    registry = GlobalResourceRegistry(
        skills={},
        mcp_servers={
            "wpcom": GlobalMcpServerEntry(
                content_hash="sha256:def456",
                owners=(Path("/a/project"),),
            ),
        },
        projects=(Path("/a/project"),),
    )
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(registry.to_json())

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert "wpcom" in loaded.mcp_servers
    assert loaded.mcp_servers["wpcom"].content_hash == "sha256:def456"
    assert loaded.mcp_servers["wpcom"].owners == (Path("/a/project"),)


def test_registry_missing_mcp_servers_key_treated_as_empty(tmp_path: Path):
    """A version 1 registry file without a mcp_servers key loads with empty dict."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(json.dumps({"version": 1, "skills": {}}, indent=2) + "\n")

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert loaded.mcp_servers == {}


def test_registry_mcp_servers_default_is_empty_dict():
    """MCP servers defaults to empty dict when not provided."""
    registry = GlobalResourceRegistry(skills={})
    assert registry.mcp_servers == {}


@pytest.mark.parametrize(
    "valid_name",
    ["wpcom", "context-a8c", "linear_server", "MyServer123", "a"],
)
def test_registry_mcp_server_valid_key_names(tmp_path: Path, valid_name: str):
    """Simple alphanumeric names with hyphens and underscores are valid MCP server keys."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {
        "version": 1,
        "skills": {},
        "mcp_servers": {
            valid_name: {
                "content_hash": "sha256:abc123",
                "owners": ["/a/project"],
            },
        },
    }
    path.write_text(json.dumps(data, indent=2) + "\n")

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert valid_name in loaded.mcp_servers


@pytest.mark.parametrize(
    "invalid_name",
    ["path/separator", "back\\slash", "has space", "has.dot", "../traversal", "", "/absolute"],
)
def test_registry_mcp_server_invalid_key_names_rejected(tmp_path: Path, invalid_name: str):
    """MCP server keys with path separators, dots, spaces, or empty strings are rejected."""
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    data = {
        "version": 1,
        "skills": {},
        "mcp_servers": {
            invalid_name: {
                "content_hash": "sha256:abc123",
                "owners": ["/a/project"],
            },
        },
    }
    path.write_text(json.dumps(data, indent=2) + "\n")

    with pytest.raises(ReconcileError):
        GlobalResourceRegistry.from_path(path)


def test_registry_mcp_server_entry_with_multiple_owners(tmp_path: Path):
    """MCP server entry supports multiple sorted owners."""
    registry = GlobalResourceRegistry(
        skills={},
        mcp_servers={
            "wpcom": GlobalMcpServerEntry(
                content_hash="sha256:multi",
                owners=(Path("/a/project-a"), Path("/b/project-b")),
            ),
        },
    )
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(registry.to_json())

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert loaded.mcp_servers["wpcom"].owners == (
        Path("/a/project-a"),
        Path("/b/project-b"),
    )


def test_registry_mcp_servers_coexist_with_skills_and_agents(tmp_path: Path):
    """Registry with mcp_servers plus skills and agents all coexist."""
    registry = GlobalResourceRegistry(
        skills={"my-skill": GlobalSkillEntry(
            content_hash="sha256:skill1",
            owners=(Path("/a/project"),),
        )},
        agents={
            "reviewer.toml": GlobalAgentEntry(
                content_hash="sha256:agent1",
                owners=(Path("/a/project"),),
            ),
        },
        mcp_servers={
            "wpcom": GlobalMcpServerEntry(
                content_hash="sha256:mcp1",
                owners=(Path("/a/project"),),
            ),
        },
        projects=(Path("/a/project"),),
    )
    path = tmp_path / GLOBAL_REGISTRY_FILENAME
    path.write_text(registry.to_json())

    loaded = GlobalResourceRegistry.from_path(path)
    assert loaded is not None
    assert "my-skill" in loaded.skills
    assert "reviewer.toml" in loaded.agents
    assert "wpcom" in loaded.mcp_servers

"""Tests for bridge state serialization and validation."""

from __future__ import annotations

from pathlib import Path

import json
import pytest

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.registry import GlobalSkillEntry, GlobalSkillRegistry
from cc_codex_bridge.state import BridgeState


def test_bridge_state_round_trips(tmp_path: Path):
    """A valid state file deserializes and preserves deterministic JSON."""
    path = tmp_path / "claude-code-bridge-state.json"
    state = BridgeState(
        project_root=tmp_path / "project",
        codex_home=tmp_path / "codex-home",
        bridge_home=tmp_path / "bridge-home",
        managed_project_files={"CLAUDE.md": "sha256:aaa", ".codex/config.toml": "sha256:bbb"},
    )
    path.write_text(state.to_json())

    loaded = BridgeState.from_path(path)

    assert loaded == state
    assert json.loads(state.to_json())["version"] == 10


def test_bridge_state_handles_missing_invalid_and_unsupported_files(tmp_path: Path):
    """State loading fails clearly for malformed or unsupported files."""
    missing = tmp_path / "missing.json"
    assert BridgeState.from_path(missing) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    with pytest.raises(ReconcileError, match="Invalid bridge state file"):
        BridgeState.from_path(invalid)

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(
        json.dumps(
            {
                "version": 999,
                "project_root": str(tmp_path / "project"),
                "codex_home": str(tmp_path / "codex-home"),
            }
        )
    )
    with pytest.raises(ReconcileError, match="Unsupported bridge state version"):
        BridgeState.from_path(unsupported)


def test_bridge_state_rejects_invalid_schema_shapes(tmp_path: Path):
    """Version-matching state payloads still validate field types strictly."""
    invalid = tmp_path / "invalid-schema.json"
    invalid.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": 1,
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(tmp_path / "bridge-home"),
                "managed_project_files": [123],
            }
        )
    )

    with pytest.raises(ReconcileError, match="Invalid bridge state file"):
        BridgeState.from_path(invalid)


def test_bridge_state_rejects_non_absolute_paths(tmp_path: Path):
    """State path fields must remain absolute paths."""
    invalid_paths = tmp_path / "invalid-paths.json"
    invalid_paths.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": "relative/project",
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(tmp_path / "bridge-home"),
                "managed_project_files": [],
            }
        )
    )

    with pytest.raises(ReconcileError, match="Invalid bridge state file"):
        BridgeState.from_path(invalid_paths)


def test_bridge_state_round_trips_with_project_skill_dirs(tmp_path):
    """BridgeState v5 with managed_project_skill_dirs round-trips correctly."""
    state = BridgeState(
        project_root=tmp_path / "project",
        codex_home=tmp_path / "codex",
        bridge_home=tmp_path / "bridge",
        managed_project_files={".codex/config.toml": "sha256:ccc"},
        managed_project_skill_dirs=("helper", "review"),
    )
    path = tmp_path / "state.json"
    path.write_text(state.to_json())
    loaded = BridgeState.from_path(path)
    assert loaded == state
    assert loaded.managed_project_skill_dirs == ("helper", "review")


def test_bridge_state_managed_files_with_hashes(tmp_path: Path):
    """BridgeState stores managed_project_files as path->hash mapping."""
    state = BridgeState(
        project_root=tmp_path,
        codex_home=tmp_path / "codex",
        bridge_home=tmp_path / "bridge",
        managed_project_files={"CLAUDE.md": "sha256:abc123"},
    )
    assert state.managed_project_files == {"CLAUDE.md": "sha256:abc123"}
    # Serialization round-trip
    json_str = state.to_json()
    state_path = tmp_path / "state.json"
    state_path.write_text(json_str)
    loaded = BridgeState.from_path(state_path)
    assert loaded.managed_project_files == {"CLAUDE.md": "sha256:abc123"}


def test_bridge_state_v8_migration(tmp_path: Path):
    """v8 state files (list format) are migrated to v10 (dict with empty hashes)."""
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "version": 8,
        "project_root": str(tmp_path),
        "codex_home": str(tmp_path / "codex"),
        "bridge_home": str(tmp_path / "bridge"),
        "managed_project_files": ["CLAUDE.md"],
        "managed_project_skill_dirs": [],
    }, indent=2))
    loaded = BridgeState.from_path(state_path)
    # v8 list entries get empty hash (unknown content)
    assert loaded.managed_project_files == {"CLAUDE.md": ""}
    assert loaded.version == 10


def test_global_skill_registry_round_trips(tmp_path: Path):
    """A valid global registry serializes and deserializes deterministically."""
    path = tmp_path / "registry.json"
    registry = GlobalSkillRegistry(
        skills={
            "prompt-engineer-prompt-engineer": GlobalSkillEntry(
                content_hash="sha256:abc123",
                owners=(tmp_path / "project-b", tmp_path / "project-a"),
            )
        }
    )
    path.write_text(registry.to_json())

    loaded = GlobalSkillRegistry.from_path(path)

    assert loaded == GlobalSkillRegistry(
        skills={
            "prompt-engineer-prompt-engineer": GlobalSkillEntry(
                content_hash="sha256:abc123",
                owners=(tmp_path / "project-a", tmp_path / "project-b"),
            )
        }
    )
    assert json.loads(registry.to_json())["version"] == 1


def test_global_skill_registry_rejects_invalid_schema(tmp_path: Path):
    """Registry loading fails clearly for malformed content."""
    missing = tmp_path / "missing.json"
    assert GlobalSkillRegistry.from_path(missing) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    with pytest.raises(ReconcileError, match="Invalid global skill registry file"):
        GlobalSkillRegistry.from_path(invalid)

    unsupported = tmp_path / "unsupported.json"
    unsupported.write_text(json.dumps({"version": 999, "skills": {}}))
    with pytest.raises(ReconcileError, match="Unsupported global skill registry version"):
        GlobalSkillRegistry.from_path(unsupported)

    invalid_schema = tmp_path / "invalid-schema.json"
    invalid_schema.write_text(
        json.dumps(
            {
                "version": 1,
                "skills": {
                    "../escape": {
                        "content_hash": "not-a-hash",
                        "owners": ["relative/project"],
                    }
                },
            }
        )
    )
    with pytest.raises(ReconcileError, match="Invalid global skill registry file"):
        GlobalSkillRegistry.from_path(invalid_schema)

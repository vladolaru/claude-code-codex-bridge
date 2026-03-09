"""Tests for interop state serialization and validation."""

from __future__ import annotations

from pathlib import Path

import json
import pytest

from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.state import InteropState


def test_interop_state_round_trips(tmp_path: Path):
    """A valid state file deserializes and preserves deterministic JSON."""
    path = tmp_path / "claude-code-interop-state.json"
    state = InteropState(
        project_root=tmp_path / "project",
        codex_home=tmp_path / "codex-home",
        selected_plugins=("market/plugin@1.0.0",),
        managed_project_files=("CLAUDE.md", ".codex/config.toml"),
        managed_codex_skill_dirs=("plugin-skill",),
    )
    path.write_text(state.to_json())

    loaded = InteropState.from_path(path)

    assert loaded == state
    assert json.loads(state.to_json())["version"] == 1


def test_interop_state_handles_missing_invalid_and_unsupported_files(tmp_path: Path):
    """State loading fails clearly for malformed or unsupported files."""
    missing = tmp_path / "missing.json"
    assert InteropState.from_path(missing) is None

    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    with pytest.raises(ReconcileError, match="Invalid interop state file"):
        InteropState.from_path(invalid)

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
    with pytest.raises(ReconcileError, match="Unsupported interop state version"):
        InteropState.from_path(unsupported)


def test_interop_state_rejects_invalid_schema_shapes(tmp_path: Path):
    """Version-matching state payloads still validate field types strictly."""
    invalid = tmp_path / "invalid-schema.json"
    invalid.write_text(
        json.dumps(
            {
                "version": 1,
                "project_root": 1,
                "codex_home": str(tmp_path / "codex-home"),
                "selected_plugins": ["market/plugin@1.0.0"],
                "managed_project_files": [123],
                "managed_codex_skill_dirs": [],
            }
        )
    )

    with pytest.raises(ReconcileError, match="Invalid interop state file"):
        InteropState.from_path(invalid)


def test_interop_state_rejects_non_absolute_paths_and_non_name_skill_entries(tmp_path: Path):
    """State path fields must be absolute and managed skill entries must be plain names."""
    invalid_paths = tmp_path / "invalid-paths.json"
    invalid_paths.write_text(
        json.dumps(
            {
                "version": 1,
                "project_root": "relative/project",
                "codex_home": str(tmp_path / "codex-home"),
                "selected_plugins": [],
                "managed_project_files": [],
                "managed_codex_skill_dirs": [],
            }
        )
    )

    with pytest.raises(ReconcileError, match="Invalid interop state file"):
        InteropState.from_path(invalid_paths)

    invalid_skill_dirs = tmp_path / "invalid-skill-dirs.json"
    invalid_skill_dirs.write_text(
        json.dumps(
            {
                "version": 1,
                "project_root": str(tmp_path / "project"),
                "codex_home": str(tmp_path / "codex-home"),
                "selected_plugins": [],
                "managed_project_files": [],
                "managed_codex_skill_dirs": ["../escape"],
            }
        )
    )

    with pytest.raises(ReconcileError, match="Invalid interop state file"):
        InteropState.from_path(invalid_skill_dirs)

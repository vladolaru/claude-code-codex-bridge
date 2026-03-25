"""Tests for config scope resolution."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.config_scope import ConfigScope, resolve_config_scope


def test_force_global_always_returns_global_scope(tmp_path):
    """--global flag always returns global scope regardless of project context."""
    bridge_home = tmp_path / "bridge"
    # Even with a project_dir that has AGENTS.md, force_global wins.
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Instructions\n")

    scope = resolve_config_scope(
        bridge_home=bridge_home,
        project_dir=project,
        force_global=True,
    )

    assert scope.target == "global"
    assert scope.config_path == bridge_home / "config.toml"
    assert scope.project_root is None


def test_force_global_without_project_dir(tmp_path):
    """--global flag works even without a project directory."""
    bridge_home = tmp_path / "bridge"

    scope = resolve_config_scope(
        bridge_home=bridge_home,
        force_global=True,
    )

    assert scope.target == "global"
    assert scope.config_path == bridge_home / "config.toml"
    assert scope.project_root is None


def test_project_with_agents_md_returns_project_scope(tmp_path):
    """Directory containing AGENTS.md resolves to project scope."""
    bridge_home = tmp_path / "bridge"
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Instructions\n")

    scope = resolve_config_scope(
        bridge_home=bridge_home,
        project_dir=project,
    )

    assert scope.target == "project"
    assert scope.config_path == project / ".codex" / "bridge.toml"
    assert scope.project_root == project


def test_no_project_found_falls_back_to_global(tmp_path):
    """Directory without AGENTS.md anywhere falls back to global scope."""
    bridge_home = tmp_path / "bridge"
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    scope = resolve_config_scope(
        bridge_home=bridge_home,
        project_dir=empty_dir,
    )

    assert scope.target == "global"
    assert scope.config_path == bridge_home / "config.toml"
    assert scope.project_root is None


def test_project_root_walks_up_from_start_path(tmp_path):
    """Scope resolver walks up directories to find AGENTS.md."""
    bridge_home = tmp_path / "bridge"
    project = tmp_path / "myproject"
    project.mkdir()
    (project / "AGENTS.md").write_text("# Instructions\n")

    # Start from a nested subdirectory
    nested = project / "src" / "deep" / "nested"
    nested.mkdir(parents=True)

    scope = resolve_config_scope(
        bridge_home=bridge_home,
        project_dir=nested,
    )

    assert scope.target == "project"
    assert scope.config_path == project / ".codex" / "bridge.toml"
    assert scope.project_root == project


def test_no_project_dir_and_no_global_flag_falls_back_to_global(tmp_path):
    """No project_dir and no force_global returns global scope."""
    bridge_home = tmp_path / "bridge"

    scope = resolve_config_scope(bridge_home=bridge_home)

    assert scope.target == "global"
    assert scope.config_path == bridge_home / "config.toml"
    assert scope.project_root is None


def test_config_scope_is_frozen(tmp_path):
    """ConfigScope dataclass is frozen (immutable)."""
    bridge_home = tmp_path / "bridge"
    scope = resolve_config_scope(bridge_home=bridge_home, force_global=True)

    import dataclasses
    assert dataclasses.is_dataclass(scope)

    import pytest
    with pytest.raises(dataclasses.FrozenInstanceError):
        scope.target = "project"  # type: ignore[misc]


def test_project_root_resolution_stops_at_first_agents_md(tmp_path):
    """Walk-up stops at the nearest AGENTS.md, not a more distant one."""
    bridge_home = tmp_path / "bridge"

    # Outer project
    outer = tmp_path / "outer"
    outer.mkdir()
    (outer / "AGENTS.md").write_text("# Outer\n")

    # Inner project (nested inside outer)
    inner = outer / "inner"
    inner.mkdir()
    (inner / "AGENTS.md").write_text("# Inner\n")

    scope = resolve_config_scope(
        bridge_home=bridge_home,
        project_dir=inner,
    )

    assert scope.target == "project"
    assert scope.project_root == inner
    assert scope.config_path == inner / ".codex" / "bridge.toml"

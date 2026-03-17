"""Tests for safe CLAUDE.md shim planning."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge.claude_shim import SHIM_CONTENT, plan_claude_shim
from cc_codex_bridge.model import ProjectContext


def test_plan_claude_shim_creates_when_missing(tmp_path: Path):
    """Missing CLAUDE.md produces a create decision."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "create"
    assert decision.path == project_root / "CLAUDE.md"
    assert decision.content == SHIM_CONTENT


def test_plan_claude_shim_preserves_exact_shim(tmp_path: Path):
    """Existing exact shim is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    claude_md_path = project_root / "CLAUDE.md"
    claude_md_path.write_text("@AGENTS.md\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "preserve"
    assert decision.content == SHIM_CONTENT


def test_plan_claude_shim_rejects_non_exact_shim_variants(tmp_path: Path):
    """Whitespace variants are treated as hand-authored rather than generator-owned."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    claude_md_path = project_root / "CLAUDE.md"
    claude_md_path.write_text("@AGENTS.md\n\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "fail"
    assert "not a generator-owned shim" in decision.reason


def test_plan_claude_shim_preserves_symlink_to_agents_md(tmp_path: Path):
    """Symlinked CLAUDE.md -> AGENTS.md is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    (project_root / "CLAUDE.md").symlink_to(agents_md_path.name)

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "preserve"


def test_plan_claude_shim_fails_for_hand_authored_file(tmp_path: Path):
    """Hand-authored CLAUDE.md is not overwritten."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    (project_root / "CLAUDE.md").write_text("# custom claude instructions\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "fail"
    assert "not a generator-owned shim" in decision.reason


def test_plan_claude_shim_bootstrap_when_claude_md_exists_without_agents_md(tmp_path: Path):
    """Bootstrap action when CLAUDE.md exists but AGENTS.md does not."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# My instructions\n")

    project = ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md")
    decision = plan_claude_shim(project)

    assert decision.action == "bootstrap"
    assert decision.path == project_root / "CLAUDE.md"
    assert decision.content == SHIM_CONTENT
    assert "bootstrap" in decision.reason.lower() or "AGENTS.md" in decision.reason


def test_plan_claude_shim_bootstrap_not_triggered_for_symlink_claude_md(tmp_path: Path):
    """Bootstrap is not triggered when CLAUDE.md is a symlink, even if AGENTS.md is absent."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    other = project_root / "other.md"
    other.write_text("# Other\n")
    (project_root / "CLAUDE.md").symlink_to("other.md")

    project = ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md")
    decision = plan_claude_shim(project)

    # Should not be bootstrap — symlinks are handled separately
    assert decision.action != "bootstrap"


def test_plan_claude_shim_fails_for_non_agents_symlink(tmp_path: Path):
    """Symlinks to anything except AGENTS.md are rejected."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    other_file = project_root / "OTHER.md"
    other_file.write_text("# Other\n")
    (project_root / "CLAUDE.md").symlink_to(other_file.name)

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "fail"
    assert "not to AGENTS.md" in decision.reason

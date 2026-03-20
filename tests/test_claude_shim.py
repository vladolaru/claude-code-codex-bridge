"""Tests for safe CLAUDE.md shim planning."""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_codex_bridge.claude_shim import SHIM_CONTENT, plan_claude_shim
from cc_codex_bridge.model import ProjectContext, ReconcileError


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


def test_plan_claude_shim_preserves_non_exact_shim_with_agents_ref(tmp_path: Path):
    """Whitespace variants that still reference AGENTS.md are preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    claude_md_path = project_root / "CLAUDE.md"
    claude_md_path.write_text("@AGENTS.md\n\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "preserve"
    assert "AGENTS.md" in decision.reason


def test_plan_claude_shim_preserves_symlink_to_agents_md(tmp_path: Path):
    """Symlinked CLAUDE.md -> AGENTS.md is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    (project_root / "CLAUDE.md").symlink_to(agents_md_path.name)

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "preserve"


def test_plan_claude_shim_skips_hand_authored_file(tmp_path: Path):
    """Hand-authored CLAUDE.md without AGENTS.md reference is skipped."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    (project_root / "CLAUDE.md").write_text("# custom claude instructions\n")

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "skip"
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


def test_plan_claude_shim_rejects_bootstrap_when_claude_md_is_shim(tmp_path: Path):
    """Bootstrap is rejected when CLAUDE.md is the @AGENTS.md shim — would create self-reference."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("@AGENTS.md\n")

    project = ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md")
    decision = plan_claude_shim(project)

    assert decision.action == "fail"
    assert "shim" in decision.reason.lower()


def test_plan_claude_shim_rejects_symlinked_agents_md_target(tmp_path: Path):
    """Bootstrap fails when AGENTS.md is a (broken) symlink."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# Real instructions\n")
    (project_root / "AGENTS.md").symlink_to("/nonexistent/target")

    project = ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md")
    decision = plan_claude_shim(project)

    assert decision.action == "fail"
    assert "symlink" in decision.reason.lower()


def test_execute_bootstrap_rejects_symlinked_agents_md(tmp_path: Path):
    """execute_bootstrap refuses to write through a symlinked AGENTS.md."""
    from cc_codex_bridge.claude_shim import execute_bootstrap

    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# Real instructions\n")
    (project_root / "AGENTS.md").symlink_to(tmp_path / "external.md")

    project = ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md")

    with pytest.raises(ReconcileError, match="symlinked AGENTS.md"):
        execute_bootstrap(project)


def test_plan_claude_shim_skips_non_agents_symlink(tmp_path: Path):
    """Symlinks to anything except AGENTS.md are skipped."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md_path = project_root / "AGENTS.md"
    agents_md_path.write_text("# Shared\n")
    other_file = project_root / "OTHER.md"
    other_file.write_text("# Other\n")
    (project_root / "CLAUDE.md").symlink_to(other_file.name)

    decision = plan_claude_shim(ProjectContext(project_root, agents_md_path))

    assert decision.action == "skip"
    assert "not to AGENTS.md" in decision.reason


def test_plan_claude_shim_preserves_agents_md_reference_no_newline(tmp_path: Path):
    """CLAUDE.md with '@AGENTS.md' (no trailing newline) is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# Shared\n")
    (project_root / "CLAUDE.md").write_text("@AGENTS.md")
    decision = plan_claude_shim(ProjectContext(project_root, project_root / "AGENTS.md"))
    assert decision.action == "preserve"
    assert "AGENTS.md" in decision.reason


def test_plan_claude_shim_preserves_human_redirect_phrase(tmp_path: Path):
    """CLAUDE.md with 'Read and follow AGENTS.md' is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# Shared\n")
    (project_root / "CLAUDE.md").write_text("Read and follow AGENTS.md\n")
    decision = plan_claude_shim(ProjectContext(project_root, project_root / "AGENTS.md"))
    assert decision.action == "preserve"


def test_plan_claude_shim_preserves_long_content_referencing_agents_md(tmp_path: Path):
    """CLAUDE.md with substantial content mentioning AGENTS.md is preserved."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# Shared\n")
    (project_root / "CLAUDE.md").write_text(
        "# Instructions\n\nSee AGENTS.md for details.\n\n## More stuff\n"
    )
    decision = plan_claude_shim(ProjectContext(project_root, project_root / "AGENTS.md"))
    assert decision.action == "preserve"


def test_plan_claude_shim_skips_independent_hand_authored(tmp_path: Path):
    """Hand-authored CLAUDE.md without AGENTS.md reference produces skip."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "AGENTS.md").write_text("# Shared\n")
    (project_root / "CLAUDE.md").write_text("# Fully independent instructions\n")
    decision = plan_claude_shim(ProjectContext(project_root, project_root / "AGENTS.md"))
    assert decision.action == "skip"

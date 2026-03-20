"""Tests for reconcile behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import cc_codex_bridge.reconcile as reconcile_module
from cc_codex_bridge.bridge_home import project_state_dir
from cc_codex_bridge.claude_shim import plan_claude_shim
from cc_codex_bridge.discover import discover
from cc_codex_bridge.model import ReconcileError, TranslationError
from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME, GlobalSkillRegistry
from cc_codex_bridge.reconcile import (
    ReconcileReport,
    build_desired_state,
    build_project_desired_state,
    diff_desired_state,
    format_change_report,
    format_diff_report,
    reconcile_desired_state,
)
from cc_codex_bridge.discover import discover_project_skills
from cc_codex_bridge.reconcile import AGENTS_RELATIVE_ROOT
from cc_codex_bridge.render_agent_toml import render_agent_toml
from cc_codex_bridge.translate_agents import (
    translate_installed_agents,
    translate_installed_agents_with_diagnostics,
    translate_standalone_agents,
    validate_merged_agents,
)
from cc_codex_bridge.translate_skills import (
    assign_skill_names,
    translate_installed_skills,
    translate_standalone_skills,
)


def test_reconcile_writes_project_and_codex_outputs(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Reconcile writes local project artifacts and global Codex skills."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.2.3",
        skill_names=("decision-critic",),
        agent_names=("architecture-reviewer",),
    )
    (version_dir / "agents" / "architecture-reviewer.md").write_text(
        "---\n"
        "name: architecture-reviewer\n"
        "description: Architecture review\n"
        "tools:\n"
        "  - Read\n"
        "---\n\n"
        "You are an architecture reviewer.\n"
    )
    (version_dir / "skills" / "decision-critic" / "SKILL.md").write_text(
        "---\n"
        "name: decision-critic\n"
        "description: Criticize decisions\n"
        "---\n\n"
        "Use this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    desired = _build_desired(project_root, cache_root, codex_home)
    report = reconcile_desired_state(desired)

    assert report.applied is True
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"
    # Global agent .toml installed to codex_home
    global_agent_toml = codex_home / "agents" / "architecture-reviewer.toml"
    assert global_agent_toml.exists()
    agent_content = global_agent_toml.read_text()
    assert "architecture-reviewer" in agent_content
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    assert state_path.exists()
    assert (
        codex_home / "skills" / "decision-critic" / "SKILL.md"
    ).read_text().startswith("---\nname: decision-critic\n")
    state_payload = json.loads(state_path.read_text())
    assert "managed_codex_skill_dirs" not in state_payload
    registry_payload = _read_global_registry(bridge_home)
    assert registry_payload["skills"]["decision-critic"]["owners"] == [
        str(project_root)
    ]


def test_reconcile_is_idempotent(make_project, make_plugin_version, tmp_path: Path):
    """Running reconcile twice without input changes becomes a no-op."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    first = reconcile_desired_state(desired)
    second = reconcile_desired_state(desired)

    assert first.changes
    assert second.changes == ()


def test_reconcile_preserves_managed_claude_symlink(make_project, make_plugin_version, tmp_path: Path):
    """A managed CLAUDE.md may transition to an AGENTS.md symlink without being deleted."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    codex_home = tmp_path / "codex-home"

    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))
    claude_md = project_root / "CLAUDE.md"
    claude_md.unlink()
    claude_md.symlink_to("AGENTS.md")

    report = reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert report.changes == ()
    assert claude_md.is_symlink()
    assert claude_md.resolve() == (project_root / "AGENTS.md").resolve()


def test_diff_does_not_write_outputs(make_project, make_plugin_version, tmp_path: Path):
    """Diff computes changes without creating generated artifacts."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    report = diff_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert report.changes
    assert not (project_root / "CLAUDE.md").exists()
    assert not (project_root / ".codex").exists()
    assert not codex_home.exists()


def test_reconcile_removes_stale_managed_skill(make_project, make_plugin_version, tmp_path: Path):
    """A later reconcile removes previously managed skills no longer desired."""
    project_root, _agents_md = make_project()
    cache_root, v1_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        skill_names=("decision-critic",),
    )
    (v1_dir / "skills" / "decision-critic" / "SKILL.md").write_text(
        "---\nname: decision-critic\ndescription: Criticize\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    first_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first_desired)
    installed_skill = codex_home / "skills" / "decision-critic"
    assert installed_skill.exists()

    _, v2_dir = make_plugin_version("market", "pirategoat-tools", "1.0.1")
    # Remove the old skill source so the fake installed latest plugin has no skills.
    stale_source = v2_dir / "skills"
    if stale_source.exists():
        raise AssertionError("Unexpected skills directory in the later fixture version")

    second_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(second_desired)

    assert not installed_skill.exists()


def test_reconcile_shares_identical_skill_ownership_across_projects(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Multiple projects may share the same generated skill when content matches."""
    first_project, _ = make_project("project-a")
    second_project, _ = make_project("project-b")
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    first_report = reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))
    second_report = reconcile_desired_state(_build_desired(second_project, cache_root, codex_home))

    assert any(change.resource_kind == "skill" for change in first_report.changes)
    assert all(change.resource_kind != "skill" for change in second_report.changes)
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    assert _read_global_registry(bridge_home)["skills"]["prompt-engineer"]["owners"] == [
        str(first_project),
        str(second_project),
    ]


def test_reconcile_keeps_shared_skill_when_one_project_drops_claim(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Dropping one shared owner preserves the skill directory for remaining owners."""
    first_project, _ = make_project("project-a")
    second_project, _ = make_project("project-b")
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    installed_skill = codex_home / "skills" / "prompt-engineer"

    reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))
    reconcile_desired_state(_build_desired(second_project, cache_root, codex_home))
    _, later_version_dir = make_plugin_version("market", "prompt-engineer", "1.0.1")
    assert not (later_version_dir / "skills").exists()

    report = reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))

    assert installed_skill.exists()
    assert all(change.path != installed_skill for change in report.changes)
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    assert _read_global_registry(bridge_home)["skills"]["prompt-engineer"]["owners"] == [
        str(second_project)
    ]


def test_reconcile_adopts_existing_matching_skill_directory(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Matching pre-existing skill directories are adopted into the registry safely."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    desired = _build_desired(project_root, cache_root, codex_home)
    _write_skill_directory(
        codex_home / "skills" / "prompt-engineer",
        desired.skills[0],
    )

    report = reconcile_desired_state(desired)

    assert all(change.resource_kind != "skill" for change in report.changes)
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    assert _read_global_registry(bridge_home)["skills"]["prompt-engineer"]["owners"] == [
        str(project_root)
    ]


def test_reconcile_fails_on_registry_conflict_for_same_skill_directory(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """A different generated tree for the same install dir is a hard conflict."""
    first_project, _ = make_project("project-a")
    second_project, _ = make_project("project-b")
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    skill_path = version_dir / "skills" / "prompt-engineer" / "SKILL.md"
    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse version A.\n"
    )
    codex_home = tmp_path / "codex-home"

    reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))
    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse version B.\n"
    )

    with pytest.raises(ReconcileError, match="Generated skill registry conflict"):
        reconcile_desired_state(_build_desired(second_project, cache_root, codex_home))


def test_reconcile_updates_shared_agent_when_plugin_upgrades(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Multi-owner agent files update when the underlying plugin changes."""
    first_project, _ = make_project("project-a")
    second_project, _ = make_project("project-b")
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.0.0",
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nVersion A.\n"
    )
    codex_home = tmp_path / "codex-home"
    installed_agent = codex_home / "agents" / "reviewer.toml"

    reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))
    reconcile_desired_state(_build_desired(second_project, cache_root, codex_home))
    assert "Version A." in installed_agent.read_text()

    # Simulate plugin upgrade
    agent_path.write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nVersion B.\n"
    )

    # First project to reconcile should update the shared agent — no conflict
    report = reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))
    assert "Version B." in installed_agent.read_text()
    assert any(
        change.resource_kind == "agent" and change.kind == "update"
        for change in report.changes
    )

    # Second project should see no changes (already updated)
    report = reconcile_desired_state(_build_desired(second_project, cache_root, codex_home))
    assert not any(
        change.resource_kind == "agent" for change in report.changes
    )



def test_diff_report_includes_unified_diff_for_updated_text_files(
    make_project,
    tmp_path: Path,
):
    """Diff output includes a unified diff when a managed text file changes."""
    project_root, _agents_md = make_project()
    # Use project-local agents so the .toml files are project files with diff support.
    project_agent_dir = project_root / ".claude" / "agents"
    project_agent_dir.mkdir(parents=True)
    (project_agent_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nOld body.\n"
    )
    cache_root = tmp_path / "claude-cache"
    cache_root.mkdir(parents=True)
    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    reconcile_desired_state(desired)

    (project_agent_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nNew body.\n"
    )
    updated = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    report = diff_desired_state(updated)
    rendered = format_diff_report(updated, report)

    assert "UPDATE:" in rendered
    assert "@@" in rendered
    assert "-Old body." in rendered
    assert "+New body." in rendered


def test_reconcile_removes_stale_managed_agent_file(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Previously managed global agent .toml files are removed when no longer desired."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    codex_home = tmp_path / "codex-home"

    first = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first)
    agent_toml_path = codex_home / "agents" / "reviewer.toml"
    assert agent_toml_path.exists()

    updated_agent = version_dir / "agents" / "reviewer.md"
    updated_agent.unlink()
    second = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(second)

    assert not agent_toml_path.exists()


def test_reconcile_rejects_symlinked_project_file(make_project, tmp_path: Path):
    """Managed project targets may not be symlinks."""
    project_root, _agents_md = make_project()

    # Set up a project-local agent that will generate a .codex/agents/*.toml file
    project_agent_dir = project_root / ".claude" / "agents"
    project_agent_dir.mkdir(parents=True)
    (project_agent_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )

    # Pre-create the target as a symlink
    codex_agents_dir = project_root / ".codex" / "agents"
    codex_agents_dir.mkdir(parents=True)
    real_file = project_root / "real-agent.toml"
    real_file.write_text("real\n")
    (codex_agents_dir / "reviewer.toml").symlink_to(real_file)

    cache_root = tmp_path / "claude-cache"
    cache_root.mkdir(parents=True)
    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="symlinked project file"):
        diff_desired_state(desired)


def test_reconcile_rejects_non_directory_skill_target(make_project, make_plugin_version, tmp_path: Path):
    """A file where a skill directory should be is a hard error."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", skill_names=("prompt-engineer",)
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    skills_root = codex_home / "skills"
    skills_root.mkdir(parents=True)
    (skills_root / "prompt-engineer").write_text("not a directory\n")

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="Expected a skill directory but found a file"):
        diff_desired_state(desired)


def test_reconcile_rejects_non_owned_skill_directory(make_project, make_plugin_version, tmp_path: Path):
    """Existing hand-authored skill directories are not overwritten."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", skill_names=("prompt-engineer",)
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    skill_dir = codex_home / "skills" / "prompt-engineer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("hand-authored\n")

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="adopt conflicting existing skill directory"):
        diff_desired_state(desired)


def test_reconcile_keeps_agents_md_and_project_claude_dir_untouched(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Reconcile leaves AGENTS.md and project-local `.claude/` content unchanged."""
    project_root, agents_md = make_project(agents_content="# Canonical shared instructions\n")
    claude_dir = project_root / ".claude"
    claude_dir.mkdir()
    local_settings = claude_dir / "settings.local.json"
    local_settings.write_text('{"permissions":["Skill(example)"]}\n')
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )

    original_agents = agents_md.read_text()
    original_settings = local_settings.read_text()

    reconcile_desired_state(_build_desired(project_root, cache_root, tmp_path / "codex-home"))

    assert agents_md.read_text() == original_agents
    assert local_settings.read_text() == original_settings


def test_reconcile_keeps_user_level_claude_tree_untouched(
    make_project,
    tmp_path: Path,
):
    """Reconcile leaves the user-level `~/.claude` tree untouched."""
    project_root, _agents_md = make_project()
    sandbox_claude_root = tmp_path / "home" / ".claude"
    (sandbox_claude_root / "settings.local.json").parent.mkdir(parents=True)
    (sandbox_claude_root / "settings.local.json").write_text('{"theme":"dark"}\n')
    agents_dir = sandbox_claude_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "local-agent.md").write_text(
        "---\nname: local-agent\ndescription: Local user agent\n---\n\nBody.\n"
    )

    cache_version_dir = (
        sandbox_claude_root
        / "plugins"
        / "cache"
        / "market"
        / "prompt-engineer"
        / "1.0.0"
    )
    skill_dir = cache_version_dir / "skills" / "prompt-engineer"
    agents_dir = cache_version_dir / "agents"
    skill_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )

    before_claude = _snapshot_tree(sandbox_claude_root)

    discovery = discover(project_path=project_root)
    shim_decision = plan_claude_shim(discovery.project)

    agent_result = translate_installed_agents_with_diagnostics(discovery.plugins)
    user_agent_result = translate_standalone_agents(discovery.user_agents, scope="user")
    project_agent_result = translate_standalone_agents(discovery.project_agents, scope="project")
    all_agents = (*agent_result.agents, *user_agent_result.agents, *project_agent_result.agents)
    validate_merged_agents(all_agents)

    global_agents = tuple(a for a in all_agents if a.scope == "global")
    project_agents = tuple(a for a in all_agents if a.scope == "project")

    project_agent_files = []
    for agent in project_agents:
        relpath = AGENTS_RELATIVE_ROOT / agent.install_filename
        content = render_agent_toml(agent.agent_name, agent.description, agent.developer_instructions, sandbox_mode=agent.sandbox_mode)
        project_agent_files.append((relpath, content.encode()))

    plugin_skills = translate_installed_skills(discovery.plugins).skills
    user_skills = translate_standalone_skills(discovery.user_skills, scope="user").skills
    skills = assign_skill_names((*plugin_skills, *user_skills))
    desired = build_desired_state(
        discovery,
        shim_decision,
        skills,
        codex_home=tmp_path / "codex-home",
        global_agents=global_agents,
        project_agent_files=project_agent_files,
    )

    reconcile_desired_state(desired)

    assert _snapshot_tree(sandbox_claude_root) == before_claude


def test_reconcile_rejects_unexpected_managed_project_files_in_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Corrupted state may not authorize touching arbitrary project files."""
    project_root, agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(bridge_home),
                "managed_project_files": ["AGENTS.md", ".claude/settings.local.json"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="unexpected managed project files"):
        diff_desired_state(desired)
    assert agents_md.exists()


def test_reconcile_rejects_unexpected_managed_project_skill_dirs_in_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Corrupted state may not authorize touching arbitrary project skill directories."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(bridge_home),
                "managed_project_files": [],
                "managed_project_skill_dirs": ["../../../bridge-victim-test"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="unexpected managed project skill directories"):
        diff_desired_state(desired)


def test_reconcile_rejects_foreign_project_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """A copied state file from another project may not drive cleanup in this project."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": str(tmp_path / "different-project"),
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(bridge_home),
                "managed_project_files": [],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="different project root"):
        diff_desired_state(desired)


def test_reconcile_rejects_symlinked_state_file(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """The bridge state file itself may not be a symlink."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True)
    real_state = tmp_path / "real-state.json"
    real_state.write_text("{}\n")
    state_path.symlink_to(real_state)

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="symlinked bridge state file"):
        diff_desired_state(desired)


def test_build_desired_state_rejects_agent_paths_outside_project(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Desired-state planning rejects project agent file paths that attempt project traversal."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    discovery = discover(project_path=project_root, cache_dir=cache_root)

    with pytest.raises(ReconcileError, match="parent traversal"):
        build_desired_state(
            discovery,
            plan_claude_shim(discovery.project),
            (),
            codex_home=tmp_path / "codex-home",
            project_agent_files=[
                (Path(".codex/agents/../../../../evil.toml"), b"malicious\n"),
            ],
        )


def test_format_change_report_handles_empty_report():
    """Empty reports render as a single no-op line."""
    assert format_change_report(ReconcileReport(changes=(), applied=False)) == "No changes."


def test_format_diff_report_handles_no_changes(make_project, make_plugin_version, tmp_path: Path):
    """No-op diffs render the short form."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", skill_names=("prompt-engineer",)
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    reconcile_desired_state(desired)
    report = diff_desired_state(desired)

    assert format_change_report(report) == "No changes."
    assert format_diff_report(desired, report) == "No changes."


def test_format_diff_report_includes_global_instructions_diff(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """format_diff_report renders diffs for global instructions changes."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "demo", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\nReview.\n"
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir()
    (claude_home / "CLAUDE.md").write_text("Global instructions.\n")

    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home,
    )
    report = diff_desired_state(desired)

    # Should not crash and should include a diff for global instructions
    rendered = format_diff_report(desired, report)
    assert "CREATE:" in rendered
    global_path = str(codex_home / "AGENTS.md")
    assert global_path in rendered


def test_reconcile_does_not_modify_symlink_resolved_plugin_cache_or_source(
    make_project,
    tmp_path: Path,
):
    """Reconcile treats symlink-resolved plugin installs as read-only inputs."""
    project_root, _agents_md = make_project()
    repo_root = tmp_path / "plugin-repo" / "prompt-engineer"
    skill_dir = repo_root / "skills" / "prompt-engineer"
    agents_dir = repo_root / "agents"
    skill_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )

    cache_root = tmp_path / "claude-cache"
    install_link = cache_root / "market" / "prompt-engineer" / "1.0.0"
    install_link.parent.mkdir(parents=True, exist_ok=True)
    install_link.symlink_to(repo_root, target_is_directory=True)

    before_cache = _snapshot_tree(cache_root)
    before_repo = _snapshot_tree(repo_root)

    reconcile_desired_state(_build_desired(project_root, cache_root, tmp_path / "codex-home"))

    assert _snapshot_tree(cache_root) == before_cache
    assert _snapshot_tree(repo_root) == before_repo


def test_reconcile_updates_skill_directory_when_sole_owner_changes_content(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Sole-owner skill content change replaces the installed skill directory."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    skill_path = version_dir / "skills" / "prompt-engineer" / "SKILL.md"
    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nVersion A.\n"
    )
    codex_home = tmp_path / "codex-home"
    installed_skill = codex_home / "skills" / "prompt-engineer" / "SKILL.md"

    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))
    assert "Version A." in installed_skill.read_text()

    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nVersion B.\n"
    )
    report = reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert any(
        change.resource_kind == "skill" and change.kind == "update"
        for change in report.changes
    )
    assert "Version B." in installed_skill.read_text()


def test_atomic_write_cleans_up_temp_file_on_failure(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """A write failure in _atomic_write_file removes the temp file and re-raises."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    codex_home = tmp_path / "codex-home"
    desired = _build_desired(project_root, cache_root, codex_home)

    original_rename = Path.rename

    def fail_on_first_rename(self, target):
        if self.name.startswith(".bridge-"):
            raise OSError("disk full")
        return original_rename(self, target)

    with patch.object(Path, "rename", fail_on_first_rename):
        with pytest.raises(OSError, match="disk full"):
            reconcile_desired_state(desired)

    bridge_temps = list(project_root.rglob(".bridge-*"))
    assert bridge_temps == []


def test_reconcile_removes_stale_global_agent_toml_file(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Stale global agent .toml files are removed when the agent source disappears."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    codex_home = tmp_path / "codex-home"

    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))
    agent_toml = codex_home / "agents" / "reviewer.toml"
    assert agent_toml.exists()

    (version_dir / "agents" / "reviewer.md").unlink()
    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert not agent_toml.exists()


def test_build_desired_state_skips_hand_authored_claude_md(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """build_desired_state proceeds when the shim decision is 'skip'."""
    project_root, _agents_md = make_project()
    (project_root / "CLAUDE.md").write_text("# My hand-authored config\n")
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")

    discovery = discover(project_path=project_root, cache_dir=cache_root)
    shim_decision = plan_claude_shim(discovery.project)
    assert shim_decision.action == "skip"

    desired = build_desired_state(
        discovery,
        shim_decision,
        (),
        codex_home=tmp_path / "codex-home",
    )
    project_file_paths = [p for p, _ in desired.project_files]
    assert (project_root / "CLAUDE.md") not in project_file_paths
    assert (project_root / "CLAUDE.md") not in desired.preserved_project_files


def test_build_desired_state_proceeds_with_skip_shim(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """build_desired_state succeeds when shim decision is skip."""
    project_root, _ = make_project()
    (project_root / "CLAUDE.md").write_text("# Independent instructions\n")
    cache_root, _ = make_plugin_version("m", "p", "1.0.0", skill_names=("s",))
    codex_home = tmp_path / "codex-home"

    build = build_project_desired_state(
        project_root,
        codex_home=codex_home,
        cache_dir=cache_root,
    )
    assert build.desired_state is not None
    project_file_paths = [p for p, _ in build.desired_state.project_files]
    assert (project_root / "CLAUDE.md") not in project_file_paths
    assert (project_root / "CLAUDE.md") not in build.desired_state.preserved_project_files


def test_diff_report_skips_remove_and_skill_changes_in_diff_output(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Diff output omits unified diffs for remove changes and non-text skill/agent changes."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    (version_dir / "agents" / "reviewer.md").unlink()
    updated = _build_desired(project_root, cache_root, codex_home)
    report = diff_desired_state(updated)
    rendered = format_diff_report(updated, report)

    assert "REMOVE:" in rendered
    agent_toml_path = str(
        codex_home / "agents" / "reviewer.toml"
    )
    assert f"--- {agent_toml_path}" not in rendered


def test_reconcile_detects_skill_directory_with_extra_files(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Pre-existing skill directory with extra files is not adopted."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    desired = _build_desired(project_root, cache_root, codex_home)
    _write_skill_directory(
        codex_home / "skills" / "prompt-engineer",
        desired.skills[0],
    )
    (codex_home / "skills" / "prompt-engineer" / "EXTRA.md").write_text(
        "extra file\n"
    )

    with pytest.raises(ReconcileError, match="adopt conflicting existing skill directory"):
        reconcile_desired_state(desired)


def test_reconcile_detects_skill_directory_with_wrong_file_mode(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Pre-existing skill directory with wrong file mode triggers an update."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    first_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first_desired)
    installed = codex_home / "skills" / "prompt-engineer" / "SKILL.md"
    installed.chmod(0o777)

    report = reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert any(
        change.resource_kind == "skill" and change.kind == "update"
        for change in report.changes
    )
    assert (installed.stat().st_mode & 0o777) != 0o777


def test_reconcile_rejects_symlinked_global_registry(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Symlinked global registry files are rejected."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    bridge_home.mkdir(parents=True)
    real_registry = tmp_path / "real-registry.json"
    real_registry.write_text("{}\n")
    (bridge_home / GLOBAL_REGISTRY_FILENAME).symlink_to(real_registry)

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="symlinked global skill registry"):
        diff_desired_state(desired)


def test_removing_one_global_agent_keeps_sibling(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Removing one global agent .toml keeps the agents/ dir and sibling agents."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer", "helper"),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nReview prompt.\n"
    )
    (version_dir / "agents" / "helper.md").write_text(
        "---\nname: helper\ndescription: Help\n---\n\nHelper prompt.\n"
    )
    codex_home = tmp_path / "codex-home"

    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))
    agents_dir = codex_home / "agents"
    assert len(list(agents_dir.glob("*.toml"))) == 2

    (version_dir / "agents" / "reviewer.md").unlink()
    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert agents_dir.exists()
    remaining = list(agents_dir.glob("*.toml"))
    assert len(remaining) == 1
    assert "helper" in remaining[0].name



def test_reconcile_rejects_traversal_paths_in_corrupted_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Corrupted state with parent traversal paths is rejected."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(bridge_home),
                "managed_project_files": [".."],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="unexpected managed project files"):
        diff_desired_state(desired)


def test_diff_report_skips_skill_create_changes(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Skill create changes are excluded from unified diff output."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    desired = _build_desired(project_root, cache_root, codex_home)
    report = diff_desired_state(desired)

    rendered = format_diff_report(desired, report)

    assert "CREATE:" in rendered
    assert "(skill)" in rendered
    skill_path = str(codex_home / "skills" / "prompt-engineer")
    assert f"--- {skill_path}" not in rendered


def test_reconcile_rejects_absolute_paths_in_corrupted_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Corrupted state with absolute paths is rejected."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(bridge_home),
                "managed_project_files": ["/etc/passwd"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="unexpected managed project files"):
        diff_desired_state(desired)


def test_reconcile_rejects_empty_paths_in_corrupted_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Corrupted state with empty managed path is rejected."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 8,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "bridge_home": str(bridge_home),
                "managed_project_files": ["."],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="unexpected managed project files"):
        diff_desired_state(desired)


def test_reconcile_writes_user_claude_md_to_codex_agents_md(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """User-level CLAUDE.md content is written to ~/.codex/AGENTS.md."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", skill_names=("minimal",)
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir(parents=True)
    (claude_home / "CLAUDE.md").write_text("Always use conventional commits.\n")

    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )
    report = reconcile_desired_state(desired)

    from cc_codex_bridge.reconcile import GLOBAL_INSTRUCTIONS_SENTINEL
    assert report.applied is True
    assert (codex_home / "AGENTS.md").read_text() == "Always use conventional commits.\n" + GLOBAL_INSTRUCTIONS_SENTINEL


def test_reconcile_skips_codex_agents_md_when_no_user_claude_md(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Absent user-level CLAUDE.md means no global instructions file."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", skill_names=("minimal",)
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir(parents=True)

    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )
    reconcile_desired_state(desired)

    assert not (codex_home / "AGENTS.md").exists()


def test_reconcile_updates_codex_agents_md_when_content_changes(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Content change in user-level CLAUDE.md triggers update."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", skill_names=("minimal",)
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir(parents=True)

    (claude_home / "CLAUDE.md").write_text("Version 1\n")
    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )
    from cc_codex_bridge.reconcile import GLOBAL_INSTRUCTIONS_SENTINEL
    reconcile_desired_state(desired)
    assert (codex_home / "AGENTS.md").read_text() == "Version 1\n" + GLOBAL_INSTRUCTIONS_SENTINEL

    (claude_home / "CLAUDE.md").write_text("Version 2\n")
    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )
    report = reconcile_desired_state(desired)

    assert any(
        change.resource_kind == "global_instructions" and change.kind == "update"
        for change in report.changes
    )
    assert (codex_home / "AGENTS.md").read_text() == "Version 2\n" + GLOBAL_INSTRUCTIONS_SENTINEL


def test_reconcile_no_change_when_global_instructions_match(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Identical user-level CLAUDE.md content does not produce a change."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", skill_names=("minimal",)
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir(parents=True)
    (claude_home / "CLAUDE.md").write_text("Stable content.\n")

    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )
    reconcile_desired_state(desired)

    desired2 = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )
    report = reconcile_desired_state(desired2)

    assert not any(
        change.resource_kind == "global_instructions" for change in report.changes
    )


def test_reconcile_rejects_symlinked_global_instructions(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Symlinked global instructions file is rejected."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0", skill_names=("minimal",)
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir(parents=True)
    (claude_home / "CLAUDE.md").write_text("Instructions.\n")

    # Create a symlinked AGENTS.md in codex_home
    codex_home.mkdir(parents=True)
    real_file = tmp_path / "real-agents.md"
    real_file.write_text("old content\n")
    (codex_home / "AGENTS.md").symlink_to(real_file)

    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )

    with pytest.raises(ReconcileError, match="symlinked global instructions"):
        reconcile_desired_state(desired)


def test_build_project_desired_state_raises_on_broken_skill_with_accepted_agent(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Unrecognized agent tools are accepted, so broken skills surface their own errors.

    Previously agent diagnostics would short-circuit before skill translation.
    Now that all tools are accepted, skill errors are raised directly.
    """
    from cc_codex_bridge.reconcile import build_project_desired_state

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "demo", "1.0.0",
        agent_names=("mixed-agent",),
        skill_names=("broken-skill",),
    )
    # Agent with unrecognized tool → now accepted
    (version_dir / "agents" / "mixed-agent.md").write_text(
        "---\nname: mixed-agent\ndescription: Review\ntools:\n  - NotebookEdit\n---\n\nPrompt.\n"
    )
    # Skill referencing a missing sibling → raises TranslationError
    (version_dir / "skills" / "broken-skill" / "SKILL.md").write_text(
        "---\nname: broken-skill\ndescription: Broken\n---\n\nSee ../nonexistent/ for details.\n"
    )

    with pytest.raises(TranslationError, match="missing sibling"):
        build_project_desired_state(
            project_root,
            codex_home=tmp_path / "codex-home",
            cache_dir=cache_root,
        )


def test_build_project_resolves_same_stem_agent_collision_with_alt_suffix(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Same-stem agents across plugin and user scopes get -alt collision resolution.

    A plugin agent file "reviewer.md" and a user agent file "reviewer.md"
    share the same stem.  The user agent wins the bare name and the plugin
    agent gets a -alt suffix.
    """
    from cc_codex_bridge.reconcile import build_project_desired_state

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "plugin", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Plugin reviewer\n---\n\nPlugin prompt.\n"
    )

    claude_home = tmp_path / "claude-home"
    agents_dir = claude_home / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: User reviewer\n---\n\nUser prompt.\n"
    )

    result = build_project_desired_state(
        project_root,
        cache_dir=cache_root,
        claude_home=claude_home,
        codex_home=tmp_path / "codex-home",
    )
    assert result.desired_state is not None
    assert result.agent_count == 2


def test_build_project_returns_bootstrap_without_mutating(tmp_path: Path):
    """build_project_desired_state returns bootstrap decision without writing files."""
    from cc_codex_bridge.reconcile import build_project_desired_state

    project_root = tmp_path / "project"
    project_root.mkdir()
    claude_content = "# My project instructions\n"
    (project_root / "CLAUDE.md").write_text(claude_content)

    build = build_project_desired_state(
        project_root,
        codex_home=tmp_path / "codex-home",
    )

    # Should NOT mutate the filesystem
    assert not (project_root / "AGENTS.md").exists()
    assert (project_root / "CLAUDE.md").read_text() == claude_content

    # Should return bootstrap decision for the caller to handle
    assert build.shim_decision.action == "bootstrap"
    assert build.desired_state is None


def test_execute_bootstrap_copies_claude_md_to_agents_md(tmp_path: Path):
    """execute_bootstrap copies CLAUDE.md to AGENTS.md and writes shim."""
    from cc_codex_bridge.claude_shim import execute_bootstrap
    from cc_codex_bridge.model import ProjectContext

    project_root = tmp_path / "project"
    project_root.mkdir()
    claude_content = "# My project instructions\n"
    (project_root / "CLAUDE.md").write_text(claude_content)

    project = ProjectContext(root=project_root, agents_md_path=project_root / "AGENTS.md")
    execute_bootstrap(project)

    assert (project_root / "AGENTS.md").read_text() == claude_content
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    # After bootstrap, build should proceed normally
    from cc_codex_bridge.reconcile import build_project_desired_state
    build = build_project_desired_state(
        project_root,
        codex_home=tmp_path / "codex-home",
    )
    assert build.shim_decision.action == "preserve"
    assert build.desired_state is not None


def test_reconcile_removes_stale_global_instructions(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """When user-level CLAUDE.md disappears, stale ~/.codex/AGENTS.md is removed."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "demo", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\nReview.\n"
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir()

    # First reconcile WITH user-level CLAUDE.md
    user_claude_md = claude_home / "CLAUDE.md"
    user_claude_md.write_text("User instructions.\n")

    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home,
    )
    reconcile_desired_state(desired)
    assert (codex_home / "AGENTS.md").exists()

    # Second reconcile WITHOUT user-level CLAUDE.md
    user_claude_md.unlink()
    desired2 = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home,
    )
    report = reconcile_desired_state(desired2)

    # The stale global instructions should be removed
    assert not (codex_home / "AGENTS.md").exists()
    remove_changes = [c for c in report.changes if c.kind == "remove" and c.resource_kind == "global_instructions"]
    assert len(remove_changes) == 1


def test_reconcile_preserves_hand_authored_global_instructions(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Hand-authored ~/.codex/AGENTS.md without sentinel is never removed."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "demo", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\nReview.\n"
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir()

    # Pre-existing hand-authored AGENTS.md (no sentinel)
    codex_home.mkdir(parents=True, exist_ok=True)
    (codex_home / "AGENTS.md").write_text("Hand-authored global instructions.\n")

    # Reconcile WITHOUT user-level CLAUDE.md — should NOT remove hand-authored file
    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home,
    )
    report = reconcile_desired_state(desired)

    assert (codex_home / "AGENTS.md").exists()
    assert (codex_home / "AGENTS.md").read_text() == "Hand-authored global instructions.\n"
    assert not any(
        change.resource_kind == "global_instructions" for change in report.changes
    )


def test_reconcile_refuses_to_overwrite_hand_authored_global_instructions(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Reconcile with user CLAUDE.md must not overwrite hand-authored ~/.codex/AGENTS.md."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "demo", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\nReview.\n"
    )
    codex_home = tmp_path / "codex-home"
    claude_home = tmp_path / "claude-home"
    claude_home.mkdir()
    (claude_home / "CLAUDE.md").write_text("Bridge-sourced instructions.\n")

    # Pre-existing hand-authored AGENTS.md (no sentinel)
    codex_home.mkdir(parents=True, exist_ok=True)
    hand_content = "Hand-authored global instructions.\n"
    (codex_home / "AGENTS.md").write_text(hand_content)

    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home,
    )

    with pytest.raises(ReconcileError, match="hand-authored"):
        reconcile_desired_state(desired)

    # Hand-authored file must survive
    assert (codex_home / "AGENTS.md").read_text() == hand_content


def test_reconcile_removes_stale_project_skill_directory(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """When a project skill source disappears, reconcile removes the project skill directory."""
    import shutil as _shutil

    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "tools", "1.0.0")
    codex_home = tmp_path / "codex-home"

    # Create a project-level skill under .claude/skills/helper/
    project_skill_dir = project_root / ".claude" / "skills" / "helper"
    project_skill_dir.mkdir(parents=True)
    (project_skill_dir / "SKILL.md").write_text(
        "---\nname: helper\ndescription: Help\n---\n\nHelp text.\n"
    )

    desired = _build_desired_with_project_skills(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    installed_skill_dir = project_root / ".codex" / "skills" / "helper"
    assert installed_skill_dir.exists()

    # Simulate an untracked file inside the installed skill directory
    (installed_skill_dir / "notes.txt").write_text("user notes")

    # Remove the skill source and reconcile again
    _shutil.rmtree(project_skill_dir)
    desired2 = _build_desired_with_project_skills(project_root, cache_root, codex_home)
    reconcile_desired_state(desired2)

    # The project-local skill directory should be fully removed (including untracked files)
    assert not installed_skill_dir.exists()


def test_diff_reports_state_file_repair(
    make_project, make_plugin_version, tmp_path,
):
    """diff must report when the state file needs to be created or updated."""
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "tools", "1.0.0", skill_names=("review",))
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Delete the state file — all managed files are still correct
    desired.state_path.unlink()

    desired2 = _build_desired(project_root, cache_root, codex_home)
    report = diff_desired_state(desired2)

    # Should report state file needs creation, not "no changes"
    assert len(report.changes) > 0
    state_changes = [c for c in report.changes if c.path == desired2.state_path]
    assert len(state_changes) == 1
    assert state_changes[0].kind == "create"


def test_reconcile_reports_state_file_in_changes(
    make_project, make_plugin_version, tmp_path,
):
    """reconcile must include the state file write in its returned changes."""
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "tools", "1.0.0", skill_names=("review",))
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    desired = _build_desired(project_root, cache_root, codex_home)
    report = reconcile_desired_state(desired)

    state_changes = [c for c in report.changes if c.path == desired.state_path]
    assert len(state_changes) == 1
    assert state_changes[0].kind == "create"


def test_reconcile_reports_state_file_update_in_changes(
    make_project, make_plugin_version, tmp_path,
):
    """reconcile must report state file as 'update' when it already existed."""
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "tools", "1.0.0", skill_names=("review",))
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Delete the state file — managed files are still present, so a re-reconcile
    # only needs to write the state file (as an update, since it existed before).
    desired.state_path.unlink()

    desired2 = _build_desired(project_root, cache_root, codex_home)
    report = reconcile_desired_state(desired2)

    state_changes = [c for c in report.changes if c.path == desired2.state_path]
    assert len(state_changes) == 1
    assert state_changes[0].kind == "create"


def test_diff_detects_extra_file_in_project_skill_directory(
    make_project, make_plugin_version, tmp_path,
):
    """An extra file added to a managed project skill directory must trigger an update."""
    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("market", "tools", "1.0.0")
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    # Create a project-level skill
    skill_dir = project_root / ".claude" / "skills" / "helper"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: helper\ndescription: test\n---\nBody\n")

    desired = _build_desired_with_project_skills(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Add an extra file that the bridge didn't generate
    installed_skill = project_root / ".codex" / "skills" / "helper"
    (installed_skill / "junk.txt").write_text("unexpected")

    desired2 = _build_desired_with_project_skills(project_root, cache_root, codex_home)
    report = diff_desired_state(desired2)

    skill_changes = [c for c in report.changes if c.resource_kind == "project_skill"]
    assert len(skill_changes) == 1
    assert skill_changes[0].kind == "update"


# ---------------------------------------------------------------------------
# clean_project tests
# ---------------------------------------------------------------------------


def test_clean_removes_all_managed_project_files(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project removes all managed project files and the state file."""
    project_root, _agents_md = make_project()
    cache_root, _v1_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        skill_names=("review",), agent_names=("checker",),
    )
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Verify artifacts exist before clean
    assert (project_root / "CLAUDE.md").exists()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    assert state_path.exists()

    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True
    assert len(report.changes) > 0

    # All managed project files gone
    assert not (project_root / "CLAUDE.md").exists()
    assert not state_path.exists()
    # AGENTS.md untouched
    assert (project_root / "AGENTS.md").exists()


def test_clean_releases_last_owner_skill(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project deletes the skill directory when this project is the last owner."""
    project_root, _agents_md = make_project()
    cache_root, _v1_dir = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    skill_dir = codex_home / "skills" / "review"
    assert skill_dir.exists()

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True
    assert not skill_dir.exists()

    # Registry should be empty or not have this skill
    from cc_codex_bridge.registry import GlobalSkillRegistry, GLOBAL_REGISTRY_FILENAME
    registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    if registry is not None:
        assert "review" not in registry.skills


def test_clean_releases_shared_skill_preserves_for_other_owner(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project preserves a shared skill when another project still owns it."""
    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    desired_a = _reconcile_once(project_a, cache_root, codex_home)
    reconcile_desired_state(desired_a)
    desired_b = _reconcile_once(project_b, cache_root, codex_home)
    reconcile_desired_state(desired_b)

    skill_dir = codex_home / "skills" / "review"
    assert skill_dir.exists()

    # Clean project A only
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    report = clean_project(project_a, bridge_home=bridge_home)
    assert report.applied is True

    # Skill directory still exists — project B still owns it
    assert skill_dir.exists()

    from cc_codex_bridge.registry import GlobalSkillRegistry, GLOBAL_REGISTRY_FILENAME
    registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry is not None
    entry = registry.skills.get("review")
    assert entry is not None
    assert project_a.resolve() not in entry.owners
    assert project_b.resolve() in entry.owners


def test_clean_no_state_is_noop(make_project, tmp_path: Path):
    """clean_project on a project with no bridge state is a no-op."""
    project_root, _agents_md = make_project()
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True
    assert len(report.changes) == 0


def test_clean_dry_run_no_side_effects(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project with dry_run=True reports changes but deletes nothing."""
    project_root, _agents_md = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0",
        skill_names=("review",), agent_names=("checker",),
    )
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    report = clean_project(project_root, bridge_home=bridge_home, dry_run=True)
    assert report.applied is False
    assert len(report.changes) > 0

    # Everything still exists
    assert (project_root / "CLAUDE.md").exists()
    assert (codex_home / "skills" / "review").exists()


def test_clean_preserves_bridge_toml(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project does not remove hand-authored bridge.toml."""
    project_root, _agents_md = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    # Write a hand-authored bridge.toml
    bridge_toml = project_root / ".codex" / "bridge.toml"
    bridge_toml.parent.mkdir(parents=True, exist_ok=True)
    bridge_toml.write_text('[exclude]\nplugins = []\n')

    from cc_codex_bridge.reconcile import clean_project

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    clean_project(project_root, bridge_home=bridge_home)

    # bridge.toml survives
    assert bridge_toml.exists()
    assert bridge_toml.read_text() == '[exclude]\nplugins = []\n'


def test_clean_removes_claude_md_shim(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project removes the CLAUDE.md shim when it is generator-owned."""
    project_root, _agents_md = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    clean_project(project_root, bridge_home=bridge_home)

    assert not (project_root / "CLAUDE.md").exists()


def test_clean_preserves_preexisting_claude_md_shim(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project does not remove a CLAUDE.md that existed before the bridge ran."""
    project_root, _agents_md = make_project()
    # Pre-create CLAUDE.md before the bridge runs — simulates a project that
    # already has the @AGENTS.md shim checked into version control.
    (project_root / "CLAUDE.md").write_text("@AGENTS.md\n")

    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Bridge preserved the existing CLAUDE.md (action=preserve, not create)
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    clean_project(project_root, bridge_home=bridge_home)

    # CLAUDE.md must survive clean — it was not created by the bridge
    assert (project_root / "CLAUDE.md").exists()
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"


def test_clean_does_not_touch_global_agents_md(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project does not remove ~/.codex/AGENTS.md (that's uninstall's job)."""
    project_root, _agents_md = make_project()
    codex_home = tmp_path / "codex-home"

    # Create minimal state so clean has something to work with
    cache_root = tmp_path / "cache"
    from cc_codex_bridge.reconcile import clean_project
    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Create a global AGENTS.md after reconcile (simulating a prior reconcile with user CLAUDE.md)
    (codex_home / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (codex_home / "AGENTS.md").write_text("# Global instructions\n")

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    clean_project(project_root, bridge_home=bridge_home)

    # Global AGENTS.md untouched
    assert (codex_home / "AGENTS.md").exists()
    assert (codex_home / "AGENTS.md").read_text() == "# Global instructions\n"


def test_reconcile_all_empty_registry(tmp_path: Path):
    """reconcile_all with no registered projects succeeds with empty results."""
    from cc_codex_bridge.reconcile import reconcile_all

    codex_home = tmp_path / "codex-home"
    report = reconcile_all(codex_home=codex_home)
    assert report.results == ()
    assert report.errors == ()


def test_reconcile_all_skips_inaccessible_project(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """reconcile_all reports error for deleted project, continues with the rest."""
    import shutil

    from cc_codex_bridge.reconcile import reconcile_all

    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    cache_root, _ = make_plugin_version("m", "p", "1.0.0", skill_names=("s",))
    codex_home = tmp_path / "codex-home"

    # Reconcile both to register them
    desired_a = _build_desired(project_a, cache_root, codex_home)
    reconcile_desired_state(desired_a)
    desired_b = _build_desired(project_b, cache_root, codex_home)
    reconcile_desired_state(desired_b)

    # Delete project A
    shutil.rmtree(project_a)

    report = reconcile_all(codex_home=codex_home)
    assert len(report.errors) == 1
    assert report.errors[0].project_root == project_a
    # Project B should have been reconciled
    assert any(r.project_root == project_b for r in report.results)


def test_reconcile_all_dry_run_no_side_effects(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """reconcile_all --dry-run does not modify anything."""
    from cc_codex_bridge.reconcile import reconcile_all

    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("m", "p", "1.0.0", skill_names=("s",))
    codex_home = tmp_path / "codex-home"

    desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Modify a managed file (CLAUDE.md shim) to create a pending change
    claude_md = project_root / "CLAUDE.md"
    claude_md.write_text("# tampered\n")

    report = reconcile_all(codex_home=codex_home, dry_run=True)
    # File was not restored
    assert claude_md.read_text() == "# tampered\n"


def test_reconcile_all_rejects_symlinked_registry(tmp_path: Path):
    """reconcile_all must fail on a symlinked global registry."""
    from cc_codex_bridge.reconcile import reconcile_all

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    bridge_home.mkdir(parents=True)
    real_registry = tmp_path / "real-registry.json"
    real_registry.write_text("{}")
    (bridge_home / GLOBAL_REGISTRY_FILENAME).symlink_to(real_registry)

    with pytest.raises(ReconcileError, match="symlinked global skill registry"):
        reconcile_all(codex_home=tmp_path / "codex-home")


def test_reconcile_all_merges_scanned_projects(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """reconcile_all discovers projects via scan config and reconciles them."""
    from cc_codex_bridge.reconcile import reconcile_all
    from cc_codex_bridge.scan import SCAN_CONFIG_FILENAME

    # Create a project that is NOT in the registry but IS scannable
    scan_dir = tmp_path / "scan-root"
    project_root = scan_dir / "scanned-project"
    project_root.mkdir(parents=True)
    (project_root / ".git").mkdir()
    (project_root / "AGENTS.md").write_text("# Scanned project\n")

    # Write scan config to bridge_home
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    bridge_home.mkdir(parents=True)
    config_path = bridge_home / SCAN_CONFIG_FILENAME
    config_path.write_text(f'scan_paths = ["{scan_dir}/*"]\n')

    codex_home = tmp_path / "codex-home"
    report = reconcile_all(codex_home=codex_home)

    # The scanned project should appear in results
    result_roots = [r.project_root for r in report.results]
    assert project_root.resolve() in [r.resolve() for r in result_roots]


def test_reconcile_all_deduplicates_registry_and_scan(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """A project in both registry and scan appears exactly once in results."""
    from cc_codex_bridge.reconcile import reconcile_all
    from cc_codex_bridge.scan import SCAN_CONFIG_FILENAME

    # Create a project, give it a .git dir, and reconcile to register it
    scan_dir = tmp_path / "scan-root"
    project_root = scan_dir / "my-project"
    project_root.mkdir(parents=True)
    (project_root / ".git").mkdir()
    (project_root / "AGENTS.md").write_text("# My project\n")

    cache_root, _ = make_plugin_version("m", "p", "1.0.0", skill_names=("s",))
    codex_home = tmp_path / "codex-home"

    # Register the project via reconcile
    desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    # Also configure scan to find the same project
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    bridge_home.mkdir(parents=True, exist_ok=True)
    config_path = bridge_home / SCAN_CONFIG_FILENAME
    config_path.write_text(f'scan_paths = ["{scan_dir}/*"]\n')

    report = reconcile_all(codex_home=codex_home)

    # The project should appear exactly once
    resolved_roots = [r.project_root.resolve() for r in report.results]
    assert resolved_roots.count(project_root.resolve()) == 1


def test_reconcile_all_no_config_uses_registry_only(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Without config.toml, reconcile_all uses registry projects only (backwards compatible)."""
    from cc_codex_bridge.reconcile import reconcile_all

    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("m", "p", "1.0.0", skill_names=("s",))
    codex_home = tmp_path / "codex-home"

    # Register the project via reconcile (no config.toml created)
    desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    report = reconcile_all(codex_home=codex_home)
    assert len(report.results) == 1
    assert report.results[0].project_root == project_root


def test_reconcile_all_scan_result_in_report(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """report.scan_result reflects whether scan config exists."""
    from cc_codex_bridge.reconcile import reconcile_all
    from cc_codex_bridge.scan import SCAN_CONFIG_FILENAME, ScanResult

    codex_home = tmp_path / "codex-home"

    # Without config.toml — scan_result should be empty ScanResult
    report_no_config = reconcile_all(codex_home=codex_home)
    assert report_no_config.scan_result is not None
    assert report_no_config.scan_result.bridgeable == ()

    # With config.toml — scan_result should be a ScanResult
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    bridge_home.mkdir(parents=True, exist_ok=True)
    scan_dir = tmp_path / "scan-root"
    scan_dir.mkdir(parents=True)
    config_path = bridge_home / SCAN_CONFIG_FILENAME
    config_path.write_text(f'scan_paths = ["{scan_dir}/*"]\n')

    report_with_config = reconcile_all(codex_home=codex_home)
    assert report_with_config.scan_result is not None
    assert isinstance(report_with_config.scan_result, ScanResult)


def test_uninstall_rejects_symlinked_registry(tmp_path: Path):
    """uninstall_all must fail on a symlinked global registry."""
    from cc_codex_bridge.reconcile import uninstall_all

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    bridge_home.mkdir(parents=True)
    real_registry = tmp_path / "real-registry.json"
    real_registry.write_text("{}")
    (bridge_home / GLOBAL_REGISTRY_FILENAME).symlink_to(real_registry)

    with pytest.raises(ReconcileError, match="symlinked global skill registry"):
        uninstall_all(codex_home=tmp_path / "codex-home")


def test_clean_removes_project_from_registry_projects_list(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """After clean, the project root is removed from the global registry projects list.

    A second registered project must survive the clean of the first.
    """
    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0",
        skill_names=("test-skill",),
    )
    codex_home = tmp_path / "codex-home"

    # Register both projects
    desired_a = _build_desired(project_a, cache_root, codex_home)
    reconcile_desired_state(desired_a)
    desired_b = _build_desired(project_b, cache_root, codex_home)
    reconcile_desired_state(desired_b)

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    registry_data = _read_global_registry(bridge_home)
    assert str(project_a) in registry_data["projects"]
    assert str(project_b) in registry_data["projects"]

    # Clean project A
    from cc_codex_bridge.reconcile import clean_project
    clean_project(project_a, bridge_home=bridge_home)

    # Project A removed, project B still present
    registry_data = _read_global_registry(bridge_home)
    assert str(project_a) not in registry_data["projects"]
    assert str(project_b) in registry_data["projects"]


def test_clean_uses_state_recorded_codex_home(make_project, tmp_path: Path):
    """clean_project uses the codex_home from bridge state, not the caller-supplied one."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.registry import GlobalSkillEntry, GlobalSkillRegistry, GLOBAL_REGISTRY_FILENAME
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    actual_codex = tmp_path / "actual-codex"
    actual_codex.mkdir()
    wrong_codex = tmp_path / "wrong-codex"
    wrong_codex.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Build state that records actual_codex as the codex_home
    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=actual_codex.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
    )
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json())

    # Write a registry in bridge_home with this project as owner
    registry = GlobalSkillRegistry(
        skills={
            "test-skill": GlobalSkillEntry(
                content_hash="sha256:abc",
                owners=(project_root.resolve(),),
            ),
        },
        projects=(project_root.resolve(),),
    )
    bridge_home.mkdir(parents=True, exist_ok=True)
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())
    skill_dir = actual_codex / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("content\n")

    # Clean with the wrong codex_home — should still clean the actual one
    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True

    # The bridge_home registry should have the project removed
    updated = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert updated is not None
    assert "test-skill" not in updated.skills
    assert project_root.resolve() not in updated.projects

    # The skill directory should be removed
    assert not skill_dir.exists()


def test_clean_dry_run_reports_managed_file_removal(make_project, tmp_path: Path):
    """clean --dry-run must report managed project files in the removal set."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex = tmp_path / "codex"
    codex.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Create an empty registry in bridge_home so clean_project doesn't fail on missing registry
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(skills={}, projects=(project_root.resolve(),))
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    # Create a managed CLAUDE.md so there's something to report
    (project_root / "CLAUDE.md").write_text("@AGENTS.md\n")

    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=("CLAUDE.md",),
    )
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json())

    report = clean_project(project_root, bridge_home=bridge_home, dry_run=True)

    removed_paths = {change.path for change in report.changes}
    assert (project_root / "CLAUDE.md") in removed_paths, "dry-run must report managed file removal"

    # Everything should still exist (dry-run)
    assert state_path.exists()
    assert (project_root / "CLAUDE.md").exists()


def test_clean_removes_full_project_skill_directory(make_project, tmp_path: Path):
    """clean_project removes the entire project-local skill directory, not just tracked files."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex = tmp_path / "codex"
    codex.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Create an empty registry in bridge_home so clean_project doesn't fail on missing registry
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(skills={}, projects=(project_root.resolve(),))
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    skill_dir = project_root / ".codex" / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("generated\n")
    (skill_dir / "extra.txt").write_text("also in the dir\n")

    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
        managed_project_skill_dirs=("demo",),
    )
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json())

    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True

    # The entire skill directory should be gone, including extra.txt
    assert not skill_dir.exists()


def test_reconcile_registers_project_in_global_registry(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """After reconcile, the project root appears in the global registry projects list."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0",
        skill_names=("test-skill",),
        agent_names=("test-agent",),
    )
    (version_dir / "agents" / "test-agent.md").write_text(
        "---\nname: test-agent\ndescription: Test\ntools:\n  - Read\n---\n\nBody.\n"
    )
    codex_home = tmp_path / "codex-home"

    desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    registry_data = _read_global_registry(bridge_home)
    assert str(project_root) in registry_data.get("projects", [])


def test_reconcile_rejects_symlinked_codex_ancestor(
    make_project, make_plugin_version, tmp_path,
):
    """Reconcile must refuse to write through a symlinked .codex directory."""
    project_root, _ = make_project()
    # Use a project-local agent so that a .codex/agents/*.toml file is generated
    project_agent_dir = project_root / ".claude" / "agents"
    project_agent_dir.mkdir(parents=True)
    (project_agent_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    cache_root = tmp_path / "claude-cache"
    cache_root.mkdir(parents=True)
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    # Create a .codex symlink pointing outside the project
    outside = tmp_path / "outside"
    outside.mkdir()
    codex_dir = project_root / ".codex"
    codex_dir.symlink_to(outside)

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="resolves outside"):
        reconcile_desired_state(desired)


def test_diff_rejects_symlinked_codex_ancestor(
    make_project, make_plugin_version, tmp_path,
):
    """Dry-run planning must fail when reconcile write targets resolve outside the project."""
    project_root, _ = make_project()
    # Use a project-local agent so that a .codex/agents/*.toml file is generated
    project_agent_dir = project_root / ".claude" / "agents"
    project_agent_dir.mkdir(parents=True)
    (project_agent_dir / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    cache_root = tmp_path / "claude-cache"
    cache_root.mkdir(parents=True)
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()

    outside = tmp_path / "outside"
    outside.mkdir()
    (project_root / ".codex").symlink_to(outside)

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="resolves outside"):
        diff_desired_state(desired)


def test_reconcile_rejects_symlinked_global_skill_directory(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Symlinked global skill directories are rejected."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0",
        skill_names=("prompt-engineer",),
    )
    (version_dir / "skills" / "prompt-engineer" / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nUse this skill.\n"
    )
    codex_home = tmp_path / "codex-home"
    skill_dir = codex_home / "skills" / "prompt-engineer"
    skill_dir.parent.mkdir(parents=True)
    real_dir = tmp_path / "real-skill"
    real_dir.mkdir()
    (real_dir / "SKILL.md").write_text("hand-authored\n")
    skill_dir.symlink_to(real_dir, target_is_directory=True)

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="symlinked.*skill directory"):
        diff_desired_state(desired)


def test_clean_rejects_symlinked_projects_dir_under_bridge_home(make_project, tmp_path):
    """clean must refuse to operate when the projects dir under bridge home is a symlink."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    bridge_home = tmp_path / "bridge-home"
    bridge_home.mkdir(parents=True)

    # Write state to the real location first
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    real_projects_dir = state_dir.parent  # bridge_home / "projects"

    # Write the state file in the real location
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex_home.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=("CLAUDE.md",),
    )
    state_path.write_text(state.to_json())

    # Create a registry in bridge_home so clean doesn't fail on missing registry
    registry = GlobalSkillRegistry(skills={}, projects=(project_root.resolve(),))
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    # Move the real projects dir outside and replace with a symlink
    import shutil
    outside = tmp_path / "outside-projects"
    shutil.move(str(real_projects_dir), str(outside))
    real_projects_dir.symlink_to(outside)

    # The state path now resolves through a symlink to outside bridge_home
    with pytest.raises(ReconcileError, match="resolves outside"):
        clean_project(project_root, bridge_home=bridge_home)


def test_clean_rejects_unexpected_managed_project_skill_dirs_in_state(make_project, tmp_path: Path):
    """clean must reject corrupted managed project skill directory names from state."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex_home.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
        managed_project_skill_dirs=("../../../bridge-victim-test",),
    )
    state_path.write_text(state.to_json())

    with pytest.raises(ReconcileError, match="unexpected managed project skill directories"):
        clean_project(project_root, bridge_home=bridge_home)


def test_clean_rejects_unexpected_managed_project_files_in_state(make_project, tmp_path: Path):
    """clean must reject corrupted managed project file paths from state.

    This is the counterpart to test_clean_rejects_unexpected_managed_project_skill_dirs_in_state.
    A corrupted state file listing AGENTS.md must NOT cause clean to delete
    the hand-authored file.
    """
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.state import BridgeState

    project_root, agents_md = make_project()
    codex_home = tmp_path / "codex_home"
    codex_home.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex_home.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(
            "AGENTS.md",  # NOT a valid managed path
        ),
    )
    state_path.write_text(state.to_json())

    with pytest.raises(ReconcileError, match="unexpected managed project files"):
        clean_project(project_root, bridge_home=bridge_home)

    # AGENTS.md must survive
    assert agents_md.exists()


def test_clean_fails_when_global_registry_missing(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """clean raises when the registry is missing but state exists."""
    from cc_codex_bridge.reconcile import clean_project

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0",
        skill_names=("test-skill",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nReview.\n"
    )
    (version_dir / "skills" / "test-skill" / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test\n---\n\nUse this.\n"
    )
    codex_home = tmp_path / "codex-home"

    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    assert state_path.exists()

    # Delete the registry — simulates manual removal
    registry_path = bridge_home / GLOBAL_REGISTRY_FILENAME
    registry_path.unlink()

    with pytest.raises(ReconcileError, match="global.*registry"):
        clean_project(project_root, bridge_home=bridge_home)
    # State file must be preserved for retry
    assert state_path.exists()


def test_clean_fails_when_global_registry_corrupt(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """clean raises when the registry exists but is corrupt (unparseable)."""
    from cc_codex_bridge.reconcile import clean_project

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0",
        skill_names=("test-skill",),
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nReview.\n"
    )
    (version_dir / "skills" / "test-skill" / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test\n---\n\nUse this.\n"
    )
    codex_home = tmp_path / "codex-home"

    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    assert state_path.exists()

    # Corrupt the registry — simulates partial write or disk error
    registry_path = bridge_home / GLOBAL_REGISTRY_FILENAME
    registry_path.write_text("not valid json{{{")

    with pytest.raises(ReconcileError, match="global.*registry"):
        clean_project(project_root, bridge_home=bridge_home)
    # State file must be preserved for retry
    assert state_path.exists()


def test_clean_handles_file_at_skill_path(make_project, make_plugin_version, tmp_path: Path):
    """clean must not crash when a regular file exists at a skill directory path."""
    from cc_codex_bridge.reconcile import clean_project

    project_root, _ = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    skill_dir = codex_home / "skills" / "review"
    assert skill_dir.is_dir()

    # Replace the skill directory with a regular file
    import shutil
    shutil.rmtree(skill_dir)
    skill_dir.write_text("not a directory")

    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True
    # The file should have been removed
    assert not skill_dir.exists()


def test_uninstall_handles_file_at_global_skill_path(
    make_project, make_plugin_version, tmp_path: Path
):
    """uninstall must not crash when a regular file exists at a global skill path."""
    from cc_codex_bridge.reconcile import uninstall_all

    project_root, _ = make_project()
    cache_root, _ = make_plugin_version(
        "market", "tools", "1.0.0", skill_names=("review",),
    )
    codex_home = tmp_path / "codex-home"

    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    skill_dir = codex_home / "skills" / "review"
    assert skill_dir.is_dir()

    # Replace the skill directory with a regular file
    import shutil
    shutil.rmtree(skill_dir)
    skill_dir.write_text("not a directory")

    report = uninstall_all(codex_home=codex_home)
    assert report.applied is True
    assert not skill_dir.exists()


def test_uninstall_has_errors_on_cleanup_failure(make_project, tmp_path: Path):
    """UninstallReport.has_errors is True when a project cleanup fails."""
    from cc_codex_bridge.reconcile import uninstall_all
    from cc_codex_bridge.state import BridgeState
    from cc_codex_bridge.registry import GlobalSkillRegistry

    project_root, _ = make_project()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Set up a state file with corrupted managed paths
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex_home.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=("AGENTS.md",),  # invalid
    )
    state_path.write_text(state.to_json())

    # Register the project in the global registry (at bridge_home)
    from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(skills={}, projects=(project_root.resolve(),))
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    report = uninstall_all(codex_home=codex_home, bridge_home=bridge_home)
    assert report.has_errors is True
    # The project should be skipped, not cleaned
    assert report.projects[0].status == "skipped"


def test_uninstall_no_errors_on_vanished_project(make_project, tmp_path: Path):
    """Vanished projects (directory not found) are NOT treated as errors."""
    import shutil as shutil_mod
    from cc_codex_bridge.reconcile import uninstall_all
    from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME, GlobalSkillRegistry

    project_root, _ = make_project()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    bridge_home.mkdir(parents=True, exist_ok=True)

    # Register the project then delete it
    registry = GlobalSkillRegistry(skills={}, projects=(project_root.resolve(),))
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())
    shutil_mod.rmtree(project_root)

    report = uninstall_all(codex_home=codex_home)
    assert report.has_errors is False
    assert report.projects[0].status == "skipped"
    assert report.projects[0].skip_reason == "directory not found"


def test_uninstall_treats_no_state_as_error(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Accessible projects with no state file are errors that block global cleanup."""
    from cc_codex_bridge.reconcile import uninstall_all

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "test-plugin", "1.0.0",
        skill_names=("test-skill",),
    )
    (version_dir / "skills" / "test-skill" / "SKILL.md").write_text(
        "---\nname: test-skill\ndescription: Test\n---\n\nUse this.\n"
    )
    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    # Delete the state file — simulates manual removal
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.unlink()

    report = uninstall_all(codex_home=codex_home, bridge_home=bridge_home, dry_run=True)

    assert report.has_errors is True
    assert any(r.status == "no_state" for r in report.projects)


def test_clean_removes_plugin_resources(make_project, tmp_path: Path):
    """clean_project removes vendored plugin directories via registry ownership."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.registry import GlobalPluginResourceEntry
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex = tmp_path / "codex"
    codex.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Create vendored plugin resources on disk
    plugin_dir = bridge_home / "plugins" / "market-pirategoat-tools"
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "bootstrap.py").write_text("print('bootstrap')")

    # Create registry with plugin resource ownership
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(
        skills={},
        projects=(project_root.resolve(),),
        plugin_resources={
            "market-pirategoat-tools": GlobalPluginResourceEntry(
                content_hash="sha256:abc123",
                owners=(project_root.resolve(),),
            ),
        },
    )
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
    )
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json())

    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True
    assert not plugin_dir.exists()
    assert any(c.resource_kind == "plugin_resource" for c in report.changes)


def test_clean_dry_run_reports_plugin_resource_removal(make_project, tmp_path: Path):
    """clean --dry-run reports plugin resource directories but does not remove them."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.registry import GlobalPluginResourceEntry
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex = tmp_path / "codex"
    codex.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Create vendored plugin resources on disk
    plugin_dir = bridge_home / "plugins" / "market-pirategoat-tools"
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "bootstrap.py").write_text("print('bootstrap')")

    # Create registry with plugin resource ownership
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(
        skills={},
        projects=(project_root.resolve(),),
        plugin_resources={
            "market-pirategoat-tools": GlobalPluginResourceEntry(
                content_hash="sha256:abc123",
                owners=(project_root.resolve(),),
            ),
        },
    )
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
    )
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json())

    report = clean_project(project_root, bridge_home=bridge_home, dry_run=True)
    assert report.applied is False
    assert any(c.resource_kind == "plugin_resource" for c in report.changes)
    # Plugin dir should still exist after dry-run
    assert plugin_dir.exists()


def test_clean_skips_nonexistent_plugin_dirs(make_project, tmp_path: Path):
    """clean_project silently skips plugin dirs that no longer exist on disk."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.registry import GlobalPluginResourceEntry
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex = tmp_path / "codex"
    codex.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Registry claims ownership of a plugin dir that does not exist on disk
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(
        skills={},
        projects=(project_root.resolve(),),
        plugin_resources={
            "market-gone-plugin": GlobalPluginResourceEntry(
                content_hash="sha256:abc123",
                owners=(project_root.resolve(),),
            ),
        },
    )
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
    )
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json())

    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True
    # No plugin_resource changes since the dir was already missing
    assert not any(c.resource_kind == "plugin_resource" for c in report.changes)
    # Registry should have the entry removed
    updated_registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert "market-gone-plugin" not in updated_registry.plugin_resources


def test_clean_preserves_shared_vendored_plugin_dirs(
    make_plugin_version, make_project, tmp_path,
):
    """Cleaning one project preserves vendored dirs still owned by another."""
    from cc_codex_bridge.reconcile import clean_project
    from cc_codex_bridge.registry import GlobalPluginResourceEntry
    from cc_codex_bridge.state import BridgeState

    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    codex = tmp_path / "codex"
    codex.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Create vendored plugin resources on disk
    plugin_dir = bridge_home / "plugins" / "market-shared-tools"
    scripts_dir = plugin_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run.py").write_text("print('shared')")

    # Both projects own this plugin resource in the registry
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(
        skills={},
        projects=(project_a.resolve(), project_b.resolve()),
        plugin_resources={
            "market-shared-tools": GlobalPluginResourceEntry(
                content_hash="sha256:shared123",
                owners=(project_a.resolve(), project_b.resolve()),
            ),
        },
    )
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    # Create state for project A
    state_a = BridgeState(
        project_root=project_a.resolve(),
        codex_home=codex.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
    )
    state_dir_a = project_state_dir(project_a, bridge_home=bridge_home)
    state_path_a = state_dir_a / "state.json"
    state_path_a.parent.mkdir(parents=True, exist_ok=True)
    state_path_a.write_text(state_a.to_json())

    # Clean project A
    report = clean_project(project_a, bridge_home=bridge_home)
    assert report.applied is True

    # Plugin dir should still exist (project B still owns it)
    assert plugin_dir.exists()
    assert not any(c.resource_kind == "plugin_resource" for c in report.changes)

    # Registry should still have the entry with only project B as owner
    updated_registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert "market-shared-tools" in updated_registry.plugin_resources
    entry = updated_registry.plugin_resources["market-shared-tools"]
    assert entry.owners == (project_b.resolve(),)


def test_uninstall_removes_plugins_dir(make_project, tmp_path: Path):
    """uninstall_all removes the entire plugins/ directory."""
    from cc_codex_bridge.reconcile import uninstall_all
    from cc_codex_bridge.registry import GlobalPluginResourceEntry
    from cc_codex_bridge.state import BridgeState

    project_root, _ = make_project()
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Create vendored plugin resources
    plugins_dir = bridge_home / "plugins" / "market-tools"
    plugins_dir.mkdir(parents=True)
    (plugins_dir / "script.py").write_text("x=1")

    # Create state and registry with plugin resource ownership
    bridge_home.mkdir(parents=True, exist_ok=True)
    registry = GlobalSkillRegistry(
        skills={},
        projects=(project_root.resolve(),),
        plugin_resources={
            "market-tools": GlobalPluginResourceEntry(
                content_hash="sha256:abc123",
                owners=(project_root.resolve(),),
            ),
        },
    )
    (bridge_home / GLOBAL_REGISTRY_FILENAME).write_text(registry.to_json())

    state = BridgeState(
        project_root=project_root.resolve(),
        codex_home=codex_home.resolve(),
        bridge_home=bridge_home.resolve(),
        managed_project_files=(),
    )
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(state.to_json())

    report = uninstall_all(bridge_home=bridge_home, codex_home=codex_home)
    assert report.applied is True
    assert not (bridge_home / "plugins").exists()


def test_reconcile_records_plugin_resource_ownership_in_registry(
    make_project, make_plugin_version, tmp_path: Path,
):
    """Reconcile tracks plugin resource ownership in the global registry, not state."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "pirategoat-tools",
        "1.2.3",
        skill_names=("decision-critic",),
    )
    (version_dir / "skills" / "decision-critic" / "SKILL.md").write_text(
        "---\nname: decision-critic\ndescription: Criticize\n---\n\nUse this skill.\n"
    )

    # Create a scripts directory in the plugin that will be vendored
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "helper.py").write_text("print('helper')")

    # Create SKILL.md that references the scripts
    (version_dir / "skills" / "decision-critic" / "SKILL.md").write_text(
        "---\nname: decision-critic\ndescription: Criticize\n---\n\n"
        "Run the script at `{{scripts}}/helper.py`.\n"
    )

    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # Use the full pipeline to get plugin resources detected
    from cc_codex_bridge.reconcile import build_project_desired_state, reconcile_desired_state
    build = build_project_desired_state(
        project_root,
        codex_home=codex_home,
        bridge_home=bridge_home,
    )
    if build.desired_state is not None:
        reconcile_desired_state(build.desired_state)

    # State should NOT contain managed_plugin_dirs (removed in v8)
    state_dir = project_state_dir(project_root, bridge_home=bridge_home)
    state_path = state_dir / "state.json"
    if state_path.exists():
        payload = json.loads(state_path.read_text())
        assert "managed_plugin_dirs" not in payload

    # Registry should contain plugin_resources ownership
    registry_path = bridge_home / GLOBAL_REGISTRY_FILENAME
    if registry_path.exists():
        registry_payload = json.loads(registry_path.read_text())
        assert "plugin_resources" in registry_payload


# -- Prompt reconciliation tests --


def test_reconcile_creates_prompt_files(make_project, make_plugin_version, tmp_path: Path):
    """Prompts in desired state are written to codex_home/prompts/."""
    from cc_codex_bridge.reconcile import build_project_desired_state

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version("market", "tools", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "review.md").write_text(
        "---\ndescription: Review code\n---\n\nReview the code.\n"
    )

    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    build = build_project_desired_state(
        project_root,
        codex_home=codex_home,
        bridge_home=bridge_home,
        cache_dir=cache_root,
    )
    assert build.desired_state is not None
    assert len(build.desired_state.global_prompts) > 0

    report = reconcile_desired_state(build.desired_state)
    assert report.applied is True

    # Verify prompt file was written
    prompt_files = list((codex_home / "prompts").glob("*.md"))
    assert len(prompt_files) > 0
    # Check content is non-empty
    for pf in prompt_files:
        assert pf.read_bytes()

    # Verify registry tracks prompt ownership
    registry_payload = _read_global_registry(bridge_home)
    assert "prompts" in registry_payload
    assert len(registry_payload["prompts"]) > 0
    for prompt_name, entry in registry_payload["prompts"].items():
        assert str(project_root) in entry["owners"]
        assert entry["content_hash"].startswith("sha256:")


def test_reconcile_prompt_is_idempotent(make_project, make_plugin_version, tmp_path: Path):
    """Running reconcile twice with same prompts produces no changes on second run."""
    from cc_codex_bridge.reconcile import build_project_desired_state

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version("market", "tools", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "lint.md").write_text(
        "---\ndescription: Lint code\n---\n\nLint the code.\n"
    )

    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    build = build_project_desired_state(
        project_root, codex_home=codex_home, bridge_home=bridge_home,
        cache_dir=cache_root,
    )
    assert build.desired_state is not None
    first = reconcile_desired_state(build.desired_state)
    assert first.applied is True

    # Rebuild and reconcile again — should be no-op
    build2 = build_project_desired_state(
        project_root, codex_home=codex_home, bridge_home=bridge_home,
        cache_dir=cache_root,
    )
    assert build2.desired_state is not None
    second = reconcile_desired_state(build2.desired_state)
    assert second.changes == ()


def test_reconcile_removes_stale_prompts(make_project, make_plugin_version, tmp_path: Path):
    """Prompts removed from desired state are cleaned up from disk and registry."""
    from cc_codex_bridge.reconcile import build_project_desired_state

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version("market", "tools", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "deploy.md").write_text(
        "---\ndescription: Deploy\n---\n\nDeploy the app.\n"
    )

    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    # First reconcile creates the prompt
    build1 = build_project_desired_state(
        project_root, codex_home=codex_home, bridge_home=bridge_home,
        cache_dir=cache_root,
    )
    assert build1.desired_state is not None
    reconcile_desired_state(build1.desired_state)

    prompt_files_before = list((codex_home / "prompts").glob("*.md"))
    assert len(prompt_files_before) > 0

    # Remove the command source and reconcile again
    (commands_dir / "deploy.md").unlink()
    build2 = build_project_desired_state(
        project_root, codex_home=codex_home, bridge_home=bridge_home,
        cache_dir=cache_root,
    )
    assert build2.desired_state is not None
    report = reconcile_desired_state(build2.desired_state)

    # Prompt file should be removed
    prompt_files_after = list((codex_home / "prompts").glob("*.md"))
    assert len(prompt_files_after) == 0

    # Registry should no longer list the prompt
    registry_payload = _read_global_registry(bridge_home)
    assert len(registry_payload.get("prompts", {})) == 0


def test_clean_project_removes_prompt_files(make_project, make_plugin_version, tmp_path: Path):
    """clean_project releases prompt ownership and removes files."""
    from cc_codex_bridge.reconcile import build_project_desired_state, clean_project

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version("market", "tools", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "test-runner.md").write_text(
        "---\ndescription: Run tests\n---\n\nRun tests.\n"
    )

    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    build = build_project_desired_state(
        project_root, codex_home=codex_home, bridge_home=bridge_home,
        cache_dir=cache_root,
    )
    assert build.desired_state is not None
    reconcile_desired_state(build.desired_state)

    prompt_files_before = list((codex_home / "prompts").glob("*.md"))
    assert len(prompt_files_before) > 0

    report = clean_project(project_root, bridge_home=bridge_home)
    assert report.applied is True

    # Prompt files should be removed
    prompt_files_after = list((codex_home / "prompts").glob("*.md"))
    assert len(prompt_files_after) == 0

    # Registry should no longer list prompts for this project
    registry_payload = _read_global_registry(bridge_home)
    assert len(registry_payload.get("prompts", {})) == 0


def test_uninstall_all_removes_prompt_files(make_project, make_plugin_version, tmp_path: Path):
    """uninstall_all removes prompt files from codex_home/prompts/."""
    from cc_codex_bridge.reconcile import build_project_desired_state, uninstall_all

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version("market", "tools", "1.0.0")
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "check.md").write_text(
        "---\ndescription: Check\n---\n\nCheck things.\n"
    )

    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"

    build = build_project_desired_state(
        project_root, codex_home=codex_home, bridge_home=bridge_home,
        cache_dir=cache_root,
    )
    assert build.desired_state is not None
    reconcile_desired_state(build.desired_state)

    prompt_files_before = list((codex_home / "prompts").glob("*.md"))
    assert len(prompt_files_before) > 0

    report = uninstall_all(bridge_home=bridge_home, codex_home=codex_home)
    assert report.applied is True

    # Prompt files should be removed
    prompts_dir = codex_home / "prompts"
    if prompts_dir.exists():
        remaining = list(prompts_dir.glob("*.md"))
        assert len(remaining) == 0


def test_reconcile_prompt_conflict_across_projects_raises(make_project, tmp_path: Path):
    """Two projects with the same prompt filename but different content raises ReconcileError."""
    from cc_codex_bridge.model import GeneratedPrompt, ReconcileError
    from cc_codex_bridge.reconcile import build_desired_state, reconcile_desired_state

    project_a, _ = make_project("project-a")
    project_b = tmp_path / "project-b"
    project_b.mkdir()
    (project_b / "AGENTS.md").write_text("# B\n")

    codex_home = tmp_path / "codex-home"
    bridge_home = tmp_path / "bridge-home"

    from cc_codex_bridge.model import DiscoveryResult, ProjectContext, ClaudeShimDecision

    # Reconcile project A with a prompt
    discovery_a = DiscoveryResult(
        project=ProjectContext(root=project_a, agents_md_path=project_a / "AGENTS.md"),
        plugins=(),
    )
    shim_a = ClaudeShimDecision(action="skip", path=project_a / "CLAUDE.md")
    prompt_a = GeneratedPrompt(
        filename="build--shop.md",
        content=b"---\ndescription: Build A\n---\n\nBuild project A.\n",
        source_path=project_a / ".claude" / "commands" / "build.md",
        marketplace="_project",
        plugin_name="local",
    )
    state_a = build_desired_state(
        discovery_a, shim_a, (),
        codex_home=codex_home, bridge_home=bridge_home,
        global_prompts=(prompt_a,),
    )
    reconcile_desired_state(state_a)

    # Reconcile project B with a different prompt at the same filename
    discovery_b = DiscoveryResult(
        project=ProjectContext(root=project_b, agents_md_path=project_b / "AGENTS.md"),
        plugins=(),
    )
    shim_b = ClaudeShimDecision(action="skip", path=project_b / "CLAUDE.md")
    prompt_b = GeneratedPrompt(
        filename="build--shop.md",
        content=b"---\ndescription: Build B\n---\n\nBuild project B.\n",
        source_path=project_b / ".claude" / "commands" / "build.md",
        marketplace="_project",
        plugin_name="local",
    )
    state_b = build_desired_state(
        discovery_b, shim_b, (),
        codex_home=codex_home, bridge_home=bridge_home,
        global_prompts=(prompt_b,),
    )
    with pytest.raises(ReconcileError, match="Generated prompt registry conflict"):
        reconcile_desired_state(state_b)


def _reconcile_once(project_root, cache_root, codex_home):
    """Run a full discover+translate+reconcile and return the desired state."""
    from cc_codex_bridge.discover import discover
    from cc_codex_bridge.claude_shim import plan_claude_shim
    from cc_codex_bridge.translate_agents import translate_installed_agents_with_diagnostics, translate_standalone_agents, validate_merged_agents
    from cc_codex_bridge.translate_skills import translate_installed_skills, translate_standalone_skills, assign_skill_names
    from cc_codex_bridge.render_agent_toml import render_agent_toml
    from cc_codex_bridge.reconcile import build_desired_state, AGENTS_RELATIVE_ROOT

    result = discover(project_path=project_root, cache_dir=cache_root)
    shim_decision = plan_claude_shim(result.project)

    agent_result = translate_installed_agents_with_diagnostics(result.plugins)
    user_agent_result = translate_standalone_agents(result.user_agents, scope="user")
    project_agent_result = translate_standalone_agents(result.project_agents, scope="project")
    all_agents = (*agent_result.agents, *user_agent_result.agents, *project_agent_result.agents)
    validate_merged_agents(all_agents)

    global_agents = tuple(a for a in all_agents if a.scope == "global")
    project_agents = tuple(a for a in all_agents if a.scope == "project")

    project_agent_files = []
    for agent in project_agents:
        relpath = AGENTS_RELATIVE_ROOT / agent.install_filename
        content = render_agent_toml(agent.agent_name, agent.description, agent.developer_instructions, sandbox_mode=agent.sandbox_mode)
        project_agent_files.append((relpath, content.encode()))

    plugin_skills = translate_installed_skills(result.plugins).skills
    user_skills = translate_standalone_skills(result.user_skills, scope="user").skills
    skills = assign_skill_names((*plugin_skills, *user_skills))
    return build_desired_state(
        result, shim_decision, skills,
        codex_home=codex_home,
        global_agents=global_agents,
        project_agent_files=project_agent_files,
    )


def _build_desired(
    project_root: Path,
    cache_root: Path,
    codex_home: Path,
    *,
    claude_home: Path | None = None,
):
    """Build desired state from fixture project and cache roots."""
    discovery = discover(
        project_path=project_root,
        cache_dir=cache_root,
        claude_home=claude_home,
    )
    shim_decision = plan_claude_shim(discovery.project)

    agent_result = translate_installed_agents_with_diagnostics(discovery.plugins)
    user_agent_result = translate_standalone_agents(discovery.user_agents, scope="user")
    project_agent_result = translate_standalone_agents(discovery.project_agents, scope="project")
    all_agents = (*agent_result.agents, *user_agent_result.agents, *project_agent_result.agents)
    validate_merged_agents(all_agents)

    global_agents = tuple(a for a in all_agents if a.scope == "global")
    project_agents = tuple(a for a in all_agents if a.scope == "project")

    project_agent_files = []
    for agent in project_agents:
        relpath = AGENTS_RELATIVE_ROOT / agent.install_filename
        content = render_agent_toml(agent.agent_name, agent.description, agent.developer_instructions, sandbox_mode=agent.sandbox_mode)
        project_agent_files.append((relpath, content.encode()))

    plugin_skills = translate_installed_skills(discovery.plugins).skills
    user_skills = translate_standalone_skills(discovery.user_skills, scope="user").skills
    skills = assign_skill_names((*plugin_skills, *user_skills))
    return build_desired_state(
        discovery,
        shim_decision,
        skills,
        codex_home=codex_home,
        global_agents=global_agents,
        project_agent_files=project_agent_files,
    )


def _build_desired_with_project_skills(
    project_root: Path,
    cache_root: Path,
    codex_home: Path,
    *,
    claude_home: Path | None = None,
):
    """Build desired state including project-level skills."""
    discovery = discover(
        project_path=project_root,
        cache_dir=cache_root,
        claude_home=claude_home,
    )
    shim_decision = plan_claude_shim(discovery.project)

    agent_result = translate_installed_agents_with_diagnostics(discovery.plugins)
    user_agent_result = translate_standalone_agents(discovery.user_agents, scope="user")
    project_agent_result = translate_standalone_agents(discovery.project_agents, scope="project")
    all_agents = (*agent_result.agents, *user_agent_result.agents, *project_agent_result.agents)
    validate_merged_agents(all_agents)

    global_agents = tuple(a for a in all_agents if a.scope == "global")
    project_agents = tuple(a for a in all_agents if a.scope == "project")

    project_agent_files = []
    for agent in project_agents:
        relpath = AGENTS_RELATIVE_ROOT / agent.install_filename
        content = render_agent_toml(agent.agent_name, agent.description, agent.developer_instructions, sandbox_mode=agent.sandbox_mode)
        project_agent_files.append((relpath, content.encode()))

    plugin_skills = translate_installed_skills(discovery.plugins).skills
    user_skills = translate_standalone_skills(discovery.user_skills, scope="user").skills
    skills = assign_skill_names((*plugin_skills, *user_skills))

    project_skills = translate_standalone_skills(discovery.project_skills, scope="project").skills

    return build_desired_state(
        discovery,
        shim_decision,
        skills,
        codex_home=codex_home,
        project_skills=project_skills,
        global_agents=global_agents,
        project_agent_files=project_agent_files,
    )


def test_reconcile_collects_command_plugin_resources(
    make_plugin_version, make_project, tmp_path,
):
    """Command-derived skills contribute plugin resources to DesiredState."""
    from cc_codex_bridge.reconcile import build_project_desired_state

    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
    )
    commands_dir = version_dir / "commands"
    commands_dir.mkdir()
    (commands_dir / "optimize.md").write_text(
        "---\ndescription: Optimize\n---\n\n"
        'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/optimize.py"\n'
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "optimize.py").write_text("print('optimize')\n")

    bridge_home = tmp_path / "bridge"
    build = build_project_desired_state(
        project_root,
        cache_dir=cache_root,
        bridge_home=bridge_home,
    )
    assert build.desired_state is not None
    resource_dirs = {
        (r.marketplace, r.plugin_name, r.target_dir_name)
        for r in build.desired_state.plugin_resources
    }
    assert ("market", "tools", "scripts") in resource_dirs


def _read_global_registry(bridge_home: Path) -> dict[str, object]:
    """Read the global registry JSON payload for assertions."""
    return json.loads((bridge_home / GLOBAL_REGISTRY_FILENAME).read_text())


def _write_skill_directory(destination: Path, skill) -> None:
    """Materialize one generated skill tree for adoption tests."""
    destination.mkdir(parents=True, exist_ok=True)
    for generated_file in skill.files:
        file_path = destination / generated_file.relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(generated_file.content)
        file_path.chmod(generated_file.mode)


def _snapshot_tree(root: Path) -> dict[str, tuple[str, str | bytes]]:
    """Capture a deterministic snapshot of a directory tree without following symlinks."""
    snapshot: dict[str, tuple[str, str | bytes]] = {}
    if not root.exists():
        return snapshot

    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            snapshot[relative] = ("symlink", str(path.readlink()))
        elif path.is_dir():
            snapshot[relative] = ("dir", "")
        else:
            snapshot[relative] = ("file", path.read_bytes())

    return snapshot


def test_hash_fast_path_skips_on_disk_comparison(
    make_plugin_version, make_project, tmp_path,
):
    """Hash-based fast path skips on-disk comparison when registry hash matches.

    When vendored files are tampered with but the plugin source hasn't changed,
    the registry content hash still matches the desired hash.  The fast path
    skips the expensive on-disk comparison and reports no changes.  This is the
    expected tradeoff: the bridge trusts the registry for unchanged plugins.

    When the plugin actually changes (different version), the hash differs and
    the on-disk comparison falls through to the slow path.
    """
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        agent_names=("reviewer",),
    )
    # Agent references $PLUGIN_ROOT/scripts/ so scripts get vendored
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('v1')\n")

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"
    build = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build.desired_state is not None

    # First reconcile — creates everything
    report1 = reconcile_desired_state(build.desired_state)
    assert report1.applied
    vendored_script = bridge_home / "plugins" / "market-tools" / "scripts" / "run.py"
    assert vendored_script.read_text() == "print('v1')\n"

    # Second reconcile — no changes (idempotent)
    build2 = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    report2 = diff_desired_state(build2.desired_state)
    resource_changes = [c for c in report2.changes if c.resource_kind == "plugin_resource"]
    assert resource_changes == [], "Idempotent reconcile should have no resource changes"

    # Tamper with vendored file — registry hash still matches (same plugin
    # version), so the fast path skips the on-disk comparison.
    vendored_script.write_text("print('STALE')\n")
    build3 = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    report3 = diff_desired_state(build3.desired_state)
    resource_changes = [c for c in report3.changes if c.resource_kind == "plugin_resource"]
    assert resource_changes == [], (
        "Hash-based fast path should skip on-disk comparison when registry hash matches"
    )


def test_plugin_upgrade_triggers_on_disk_comparison(
    make_plugin_version, make_project, tmp_path,
):
    """When plugin content changes, hash differs and on-disk comparison runs."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('v1')\n")

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"
    build = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    reconcile_desired_state(build.desired_state)

    # Simulate plugin upgrade: change the source script content
    (scripts_dir / "run.py").write_text("print('v2')\n")

    build2 = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    report = diff_desired_state(build2.desired_state)
    resource_changes = [c for c in report.changes if c.resource_kind == "plugin_resource"]
    assert len(resource_changes) == 1
    assert resource_changes[0].kind == "update"

    # Reconcile should apply the update
    report2 = reconcile_desired_state(build2.desired_state)
    assert report2.applied
    vendored_script = bridge_home / "plugins" / "market-tools" / "scripts" / "run.py"
    assert vendored_script.read_text() == "print('v2')\n"


def test_status_detects_missing_vendored_resources(
    make_plugin_version, make_project, tmp_path,
):
    """status/dry-run detects when vendored plugin resources are missing from disk."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('v1')\n")

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"
    build = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    reconcile_desired_state(build.desired_state)

    # Delete the vendored dir to simulate P2 scenario (another project cleaned it)
    import shutil
    shutil.rmtree(bridge_home / "plugins" / "market-tools" / "scripts")

    # Status should detect the missing resource
    build2 = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    report = diff_desired_state(build2.desired_state)
    resource_changes = [c for c in report.changes if c.resource_kind == "plugin_resource"]
    assert len(resource_changes) == 1
    assert resource_changes[0].kind == "create"

    # Reconcile should recreate it
    report2 = reconcile_desired_state(build2.desired_state)
    assert report2.applied
    assert (bridge_home / "plugins" / "market-tools" / "scripts" / "run.py").exists()


def test_reconcile_tracks_vendored_plugin_resource_ownership_in_registry(
    make_plugin_version, make_project, tmp_path,
):
    """Reconcile records vendored plugin resource ownership in the global registry."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('hello')\n")

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"
    build = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build.desired_state is not None
    reconcile_desired_state(build.desired_state)

    # Verify the registry has the plugin resource entry
    registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry is not None
    assert "market-tools" in registry.plugin_resources
    entry = registry.plugin_resources["market-tools"]
    assert entry.content_hash.startswith("sha256:")
    assert project_root.resolve() in entry.owners


def test_reconcile_shares_vendored_plugin_resource_ownership_across_projects(
    make_plugin_version, make_project, tmp_path,
):
    """Two projects using the same plugin both appear as owners in the registry."""
    first_project, _ = make_project("project-a")
    second_project, _ = make_project("project-b")
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('shared')\n")

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"

    # Reconcile first project
    build1 = build_project_desired_state(
        first_project, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build1.desired_state is not None
    reconcile_desired_state(build1.desired_state)

    # Reconcile second project
    build2 = build_project_desired_state(
        second_project, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build2.desired_state is not None
    reconcile_desired_state(build2.desired_state)

    # Verify both projects are owners in the registry
    registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry is not None
    assert "market-tools" in registry.plugin_resources
    entry = registry.plugin_resources["market-tools"]
    assert entry.content_hash.startswith("sha256:")
    owners = [str(o) for o in entry.owners]
    assert str(first_project.resolve()) in owners
    assert str(second_project.resolve()) in owners


def test_reconcile_combines_hash_for_multiple_resource_dirs(
    make_plugin_version, make_project, tmp_path,
):
    """Plugin with multiple vendored subdirs gets a combined hash per parent dir."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        skill_names=("analyzer",),
        agent_names=("reviewer",),
    )
    # Agent references scripts/ dir
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    # Skill references data/ dir
    (version_dir / "skills" / "analyzer" / "SKILL.md").write_text(
        "---\nname: analyzer\ndescription: Analyze\n---\n\n"
        "Use data at `{{data}}/config.json`.\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('script')\n")
    data_dir = version_dir / "data"
    data_dir.mkdir()
    (data_dir / "config.json").write_text('{"key": "value"}\n')

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"
    build = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build.desired_state is not None
    reconcile_desired_state(build.desired_state)

    # The registry should have one entry for "market-tools" with a combined hash
    registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry is not None
    assert "market-tools" in registry.plugin_resources
    entry = registry.plugin_resources["market-tools"]
    assert entry.content_hash.startswith("sha256:")
    assert project_root.resolve() in entry.owners


def test_reconcile_releases_stale_plugin_resources(
    make_plugin_version, make_project, tmp_path,
):
    """Plugin resources are released when a plugin is no longer used."""
    project_root, _ = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('hello')\n")

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"

    # First reconcile: plugin is active, resources are vendored
    build1 = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build1.desired_state is not None
    reconcile_desired_state(build1.desired_state)

    # Verify the vendored dir exists and registry has the entry
    vendored_dir = bridge_home / "plugins" / "market-tools"
    assert vendored_dir.exists()
    registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry is not None
    assert "market-tools" in registry.plugin_resources

    # Second reconcile: exclude the plugin so resources are no longer desired
    build2 = build_project_desired_state(
        project_root, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
        exclude_plugins=("market/tools",),
    )
    assert build2.desired_state is not None
    report = reconcile_desired_state(build2.desired_state)

    # Verify the vendored dir is removed and registry entry is gone
    assert not vendored_dir.exists()
    registry2 = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry2 is not None
    assert "market-tools" not in registry2.plugin_resources

    # A remove change should have been recorded
    remove_changes = [c for c in report.changes if c.kind == "remove" and c.resource_kind == "plugin_resource"]
    assert len(remove_changes) == 1


def test_reconcile_releases_stale_plugin_resources_preserves_shared_dirs(
    make_plugin_version, make_project, tmp_path,
):
    """Stale plugin resources are released but shared dirs survive."""
    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    cache_root, version_dir = make_plugin_version(
        "market", "tools", "1.0.0",
        agent_names=("reviewer",),
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\ntools:\n  - Read\n---\n\n"
        "python3 $PLUGIN_ROOT/scripts/run.py\n"
    )
    scripts_dir = version_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.py").write_text("print('shared')\n")

    bridge_home = tmp_path / "bridge"
    codex_home = tmp_path / "codex"

    # Reconcile both projects with the plugin
    build_a1 = build_project_desired_state(
        project_a, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build_a1.desired_state is not None
    reconcile_desired_state(build_a1.desired_state)

    build_b1 = build_project_desired_state(
        project_b, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
    )
    assert build_b1.desired_state is not None
    reconcile_desired_state(build_b1.desired_state)

    # Both projects own the resource
    vendored_dir = bridge_home / "plugins" / "market-tools"
    assert vendored_dir.exists()
    registry = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry is not None
    assert len(registry.plugin_resources["market-tools"].owners) == 2

    # Reconcile project A WITHOUT the plugin (exclude it)
    build_a2 = build_project_desired_state(
        project_a, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
        exclude_plugins=("market/tools",),
    )
    assert build_a2.desired_state is not None
    report_a2 = reconcile_desired_state(build_a2.desired_state)

    # Dir still exists because project B still owns it
    assert vendored_dir.exists()
    registry2 = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry2 is not None
    assert "market-tools" in registry2.plugin_resources
    entry2 = registry2.plugin_resources["market-tools"]
    assert len(entry2.owners) == 1
    assert project_b.resolve() in entry2.owners
    # No remove change since the dir is still needed
    remove_changes_a = [c for c in report_a2.changes if c.kind == "remove" and c.resource_kind == "plugin_resource"]
    assert len(remove_changes_a) == 0

    # Reconcile project B WITHOUT the plugin
    build_b2 = build_project_desired_state(
        project_b, cache_dir=cache_root,
        bridge_home=bridge_home, codex_home=codex_home,
        exclude_plugins=("market/tools",),
    )
    assert build_b2.desired_state is not None
    report_b2 = reconcile_desired_state(build_b2.desired_state)

    # Now the dir should be removed
    assert not vendored_dir.exists()
    registry3 = GlobalSkillRegistry.from_path(bridge_home / GLOBAL_REGISTRY_FILENAME)
    assert registry3 is not None
    assert "market-tools" not in registry3.plugin_resources
    remove_changes_b = [c for c in report_b2.changes if c.kind == "remove" and c.resource_kind == "plugin_resource"]
    assert len(remove_changes_b) == 1


def test_reconcile_seeds_config_stub(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """First reconcile seeds config.toml stub in bridge home."""
    from cc_codex_bridge.scan import SCAN_CONFIG_FILENAME

    project_root, _ = make_project()
    cache_root, _ = make_plugin_version("m", "p", "1.0.0", skill_names=("s",))
    codex_home = tmp_path / "codex-home"

    desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    bridge_home = desired.bridge_home
    config_path = bridge_home / SCAN_CONFIG_FILENAME
    assert config_path.exists()
    # Stub should be all comments — loading it produces empty config
    from cc_codex_bridge.scan import load_scan_config
    config = load_scan_config(bridge_home)
    assert config.scan_paths == ()

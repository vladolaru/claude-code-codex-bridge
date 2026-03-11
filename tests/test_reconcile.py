"""Tests for reconcile behavior."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import cc_codex_bridge.reconcile as reconcile_module
from cc_codex_bridge.claude_shim import plan_claude_shim
from cc_codex_bridge.discover import discover
from cc_codex_bridge.model import ReconcileError
from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME
from cc_codex_bridge.reconcile import (
    STATE_RELATIVE_PATH,
    ReconcileReport,
    build_desired_state,
    diff_desired_state,
    format_change_report,
    format_diff_report,
    reconcile_desired_state,
)
from cc_codex_bridge.render_codex_config import render_inline_codex_config, render_prompt_files
from cc_codex_bridge.translate_agents import translate_installed_agents
from cc_codex_bridge.translate_skills import translate_installed_skills


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
    assert (project_root / ".codex" / "config.toml").exists()
    assert (
        project_root / ".codex" / "prompts" / "agents" / "market-pirategoat-tools-architecture-reviewer.md"
    ).read_text() == "You are an architecture reviewer.\n"
    assert (project_root / STATE_RELATIVE_PATH).exists()
    assert (
        codex_home / "skills" / "market-pirategoat-tools-decision-critic" / "SKILL.md"
    ).read_text().startswith("---\nname: market-pirategoat-tools-decision-critic\n")
    state_payload = json.loads((project_root / STATE_RELATIVE_PATH).read_text())
    assert "managed_codex_skill_dirs" not in state_payload
    registry_payload = _read_global_registry(codex_home)
    assert registry_payload["skills"]["market-pirategoat-tools-decision-critic"]["owners"] == [
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


def test_reconcile_fails_for_non_owned_project_config(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Existing hand-authored `.codex/config.toml` is not overwritten."""
    project_root, _agents_md = make_project()
    (project_root / ".codex").mkdir()
    (project_root / ".codex" / "config.toml").write_text("# hand-authored\n")
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

    with pytest.raises(ReconcileError, match="Refusing to overwrite non-generated project file"):
        reconcile_desired_state(desired)


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
    installed_skill = codex_home / "skills" / "market-pirategoat-tools-decision-critic"
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
    assert _read_global_registry(codex_home)["skills"]["market-prompt-engineer-prompt-engineer"]["owners"] == [
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
    installed_skill = codex_home / "skills" / "market-prompt-engineer-prompt-engineer"

    reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))
    reconcile_desired_state(_build_desired(second_project, cache_root, codex_home))
    _, later_version_dir = make_plugin_version("market", "prompt-engineer", "1.0.1")
    assert not (later_version_dir / "skills").exists()

    report = reconcile_desired_state(_build_desired(first_project, cache_root, codex_home))

    assert installed_skill.exists()
    assert all(change.path != installed_skill for change in report.changes)
    assert _read_global_registry(codex_home)["skills"]["market-prompt-engineer-prompt-engineer"]["owners"] == [
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
        codex_home / "skills" / "market-prompt-engineer-prompt-engineer",
        desired.skills[0],
    )

    report = reconcile_desired_state(desired)

    assert all(change.resource_kind != "skill" for change in report.changes)
    assert _read_global_registry(codex_home)["skills"]["market-prompt-engineer-prompt-engineer"]["owners"] == [
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


def test_reconcile_moves_managed_skills_when_codex_home_changes(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Changing Codex home migrates managed skill directories instead of orphaning the old ones."""
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
    first_home = tmp_path / "codex-home-one"
    second_home = tmp_path / "codex-home-two"

    reconcile_desired_state(_build_desired(project_root, cache_root, first_home))
    original_skill = first_home / "skills" / "market-prompt-engineer-prompt-engineer"
    assert original_skill.exists()

    reconcile_desired_state(_build_desired(project_root, cache_root, second_home))

    assert not original_skill.exists()
    assert (second_home / "skills" / "market-prompt-engineer-prompt-engineer").exists()
    assert '"codex_home": "' + str(second_home.resolve()) + '"' in (
        project_root / STATE_RELATIVE_PATH
    ).read_text()


def test_diff_report_includes_unified_diff_for_updated_text_files(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Diff output includes a unified diff when a managed text file changes."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nOld body.\n")
    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    reconcile_desired_state(desired)

    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nNew body.\n")
    updated = _build_desired(project_root, cache_root, tmp_path / "codex-home")
    report = diff_desired_state(updated)
    rendered = format_diff_report(updated, report)

    assert "UPDATE:" in rendered
    assert "@@" in rendered
    assert "-Old body." in rendered
    assert "+New body." in rendered


def test_reconcile_removes_stale_managed_prompt_file(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Previously managed prompt files are removed when no longer desired."""
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
    prompt_path = project_root / ".codex" / "prompts" / "agents" / "market-prompt-engineer-reviewer.md"
    assert prompt_path.exists()

    updated_agent = version_dir / "agents" / "reviewer.md"
    updated_agent.unlink()
    second = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(second)

    assert not prompt_path.exists()


def test_reconcile_rejects_symlinked_project_file(make_project, make_plugin_version, tmp_path: Path):
    """Managed project targets may not be symlinks."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market", "prompt-engineer", "1.0.0", agent_names=("reviewer",)
    )
    (version_dir / "agents" / "reviewer.md").write_text(
        "---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n"
    )
    config_dir = project_root / ".codex"
    config_dir.mkdir()
    real_config = project_root / "real-config.toml"
    real_config.write_text("real\n")
    (config_dir / "config.toml").symlink_to(real_config)

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
    (skills_root / "market-prompt-engineer-prompt-engineer").write_text("not a directory\n")

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
    skill_dir = codex_home / "skills" / "market-prompt-engineer-prompt-engineer"
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
    roles = translate_installed_agents(discovery.plugins)
    skills = translate_installed_skills(discovery.plugins)
    prompt_files = render_prompt_files(roles)
    rendered_config = render_inline_codex_config(roles)
    desired = build_desired_state(
        discovery,
        shim_decision,
        prompt_files,
        rendered_config,
        skills,
        codex_home=tmp_path / "codex-home",
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
    state_path = project_root / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 3,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
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
    state_path = project_root / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 3,
                "project_root": str(tmp_path / "different-project"),
                "codex_home": str(tmp_path / "codex-home"),
                "managed_project_files": [STATE_RELATIVE_PATH.as_posix()],
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
    state_path = project_root / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True)
    real_state = project_root / "real-state.json"
    real_state.write_text("{}\n")
    state_path.symlink_to(real_state)

    desired = _build_desired(project_root, cache_root, tmp_path / "codex-home")

    with pytest.raises(ReconcileError, match="symlinked bridge state file"):
        diff_desired_state(desired)


def test_build_desired_state_rejects_prompt_paths_outside_project(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Desired-state planning rejects prompt paths that attempt project traversal."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    discovery = discover(project_path=project_root, cache_dir=cache_root)

    with pytest.raises(ReconcileError, match="parent traversal"):
        build_desired_state(
            discovery,
            plan_claude_shim(discovery.project),
            {
                Path(".codex/prompts/agents/../../../../evil.md"): "malicious\n",
            },
            render_inline_codex_config(()),
            (),
            codex_home=tmp_path / "codex-home",
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
    installed_skill = codex_home / "skills" / "market-prompt-engineer-prompt-engineer" / "SKILL.md"

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


def test_reconcile_cleans_empty_prompt_parents_but_stops_at_non_empty(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Stale prompt removal cleans empty parent dirs but stops at .codex."""
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
    prompts_dir = project_root / ".codex" / "prompts"
    agents_dir = prompts_dir / "agents"
    assert agents_dir.exists()

    (version_dir / "agents" / "reviewer.md").unlink()
    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert not agents_dir.exists()
    assert not prompts_dir.exists()
    assert (project_root / ".codex").exists()


def test_build_desired_state_fails_for_hand_authored_claude_md(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """build_desired_state raises when the shim decision is 'fail'."""
    project_root, _agents_md = make_project()
    (project_root / "CLAUDE.md").write_text("# My hand-authored config\n")
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")

    discovery = discover(project_path=project_root, cache_dir=cache_root)
    shim_decision = plan_claude_shim(discovery.project)
    assert shim_decision.action == "fail"

    with pytest.raises(ReconcileError, match="CLAUDE.md"):
        build_desired_state(
            discovery,
            shim_decision,
            {},
            "",
            (),
            codex_home=tmp_path / "codex-home",
        )


def test_diff_report_skips_remove_and_skill_changes_in_diff_output(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Diff output omits unified diffs for remove changes and non-text skill changes."""
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
    prompt_path = str(
        project_root / ".codex" / "prompts" / "agents" / "market-prompt-engineer-reviewer.md"
    )
    assert f"--- {prompt_path}" not in rendered


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
        codex_home / "skills" / "market-prompt-engineer-prompt-engineer",
        desired.skills[0],
    )
    (codex_home / "skills" / "market-prompt-engineer-prompt-engineer" / "EXTRA.md").write_text(
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
    installed = codex_home / "skills" / "market-prompt-engineer-prompt-engineer" / "SKILL.md"
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
    codex_home.mkdir(parents=True)
    real_registry = tmp_path / "real-registry.json"
    real_registry.write_text("{}\n")
    (codex_home / "claude-code-bridge-global-state.json").symlink_to(real_registry)

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="symlinked global skill registry"):
        diff_desired_state(desired)


def test_cleanup_empty_parents_stops_when_sibling_prompt_exists(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Removing one prompt keeps the agents/ dir when another prompt still exists."""
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
    agents_dir = project_root / ".codex" / "prompts" / "agents"
    assert len(list(agents_dir.glob("*.md"))) == 2

    (version_dir / "agents" / "reviewer.md").unlink()
    reconcile_desired_state(_build_desired(project_root, cache_root, codex_home))

    assert agents_dir.exists()
    remaining = list(agents_dir.glob("*.md"))
    assert len(remaining) == 1
    assert "helper" in remaining[0].name


def test_reconcile_codex_home_migration_preserves_other_owners_in_previous_registry(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Moving codex home preserves other projects' ownership in the previous registry."""
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
    first_home = tmp_path / "codex-home-one"
    second_home = tmp_path / "codex-home-two"

    reconcile_desired_state(_build_desired(first_project, cache_root, first_home))
    reconcile_desired_state(_build_desired(second_project, cache_root, first_home))

    original_registry = _read_global_registry(first_home)
    assert sorted(
        original_registry["skills"]["market-prompt-engineer-prompt-engineer"]["owners"]
    ) == sorted([str(first_project), str(second_project)])

    reconcile_desired_state(_build_desired(first_project, cache_root, second_home))

    previous_registry = _read_global_registry(first_home)
    assert previous_registry["skills"]["market-prompt-engineer-prompt-engineer"]["owners"] == [
        str(second_project)
    ]
    new_registry = _read_global_registry(second_home)
    assert new_registry["skills"]["market-prompt-engineer-prompt-engineer"]["owners"] == [
        str(first_project)
    ]
    assert (first_home / "skills" / "market-prompt-engineer-prompt-engineer").exists()
    assert (second_home / "skills" / "market-prompt-engineer-prompt-engineer").exists()


def test_reconcile_rejects_traversal_paths_in_corrupted_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Corrupted state with parent traversal paths is rejected."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    state_path = project_root / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 3,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "managed_project_files": ["..", STATE_RELATIVE_PATH.as_posix()],
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
    skill_path = str(codex_home / "skills" / "market-prompt-engineer-prompt-engineer")
    assert f"--- {skill_path}" not in rendered


def test_reconcile_rejects_absolute_paths_in_corrupted_state(
    make_project,
    make_plugin_version,
    tmp_path: Path,
):
    """Corrupted state with absolute paths is rejected."""
    project_root, _agents_md = make_project()
    cache_root, _version_dir = make_plugin_version("market", "prompt-engineer", "1.0.0")
    state_path = project_root / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 3,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "managed_project_files": ["/etc/passwd", STATE_RELATIVE_PATH.as_posix()],
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
    state_path = project_root / STATE_RELATIVE_PATH
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        json.dumps(
            {
                "version": 3,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "managed_project_files": [".", STATE_RELATIVE_PATH.as_posix()],
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

    assert report.applied is True
    assert (codex_home / "AGENTS.md").read_text() == "Always use conventional commits.\n"


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
    reconcile_desired_state(desired)
    assert (codex_home / "AGENTS.md").read_text() == "Version 1\n"

    (claude_home / "CLAUDE.md").write_text("Version 2\n")
    desired = _build_desired(
        project_root, cache_root, codex_home, claude_home=claude_home
    )
    report = reconcile_desired_state(desired)

    assert any(
        change.resource_kind == "global_instructions" and change.kind == "update"
        for change in report.changes
    )
    assert (codex_home / "AGENTS.md").read_text() == "Version 2\n"


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
    assert (project_root / ".codex" / "config.toml").exists()
    assert (project_root / "CLAUDE.md").exists()
    state_path = project_root / ".codex" / "claude-code-bridge-state.json"
    assert state_path.exists()

    report = clean_project(project_root, codex_home=codex_home)
    assert report.applied is True
    assert len(report.changes) > 0

    # All managed project files gone
    assert not (project_root / ".codex" / "config.toml").exists()
    assert not (project_root / "CLAUDE.md").exists()
    assert not state_path.exists()
    # Prompt files gone
    prompts_dir = project_root / ".codex" / "prompts" / "agents"
    assert not prompts_dir.exists() or len(list(prompts_dir.glob("*.md"))) == 0
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

    skill_dir = codex_home / "skills" / "market-tools-review"
    assert skill_dir.exists()

    report = clean_project(project_root, codex_home=codex_home)
    assert report.applied is True
    assert not skill_dir.exists()

    # Registry should be empty or not have this skill
    from cc_codex_bridge.registry import GlobalSkillRegistry, GLOBAL_REGISTRY_FILENAME
    registry = GlobalSkillRegistry.from_path(codex_home / GLOBAL_REGISTRY_FILENAME)
    if registry is not None:
        assert "market-tools-review" not in registry.skills


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

    skill_dir = codex_home / "skills" / "market-tools-review"
    assert skill_dir.exists()

    # Clean project A only
    report = clean_project(project_a, codex_home=codex_home)
    assert report.applied is True

    # Skill directory still exists — project B still owns it
    assert skill_dir.exists()

    from cc_codex_bridge.registry import GlobalSkillRegistry, GLOBAL_REGISTRY_FILENAME
    registry = GlobalSkillRegistry.from_path(codex_home / GLOBAL_REGISTRY_FILENAME)
    assert registry is not None
    entry = registry.skills.get("market-tools-review")
    assert entry is not None
    assert project_a.resolve() not in entry.owners
    assert project_b.resolve() in entry.owners


def test_clean_no_state_is_noop(make_project, tmp_path: Path):
    """clean_project on a project with no bridge state is a no-op."""
    project_root, _agents_md = make_project()
    codex_home = tmp_path / "codex-home"

    from cc_codex_bridge.reconcile import clean_project

    report = clean_project(project_root, codex_home=codex_home)
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

    report = clean_project(project_root, codex_home=codex_home, dry_run=True)
    assert report.applied is False
    assert len(report.changes) > 0

    # Everything still exists
    assert (project_root / ".codex" / "config.toml").exists()
    assert (project_root / "CLAUDE.md").exists()
    assert (codex_home / "skills" / "market-tools-review").exists()


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

    clean_project(project_root, codex_home=codex_home)

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

    clean_project(project_root, codex_home=codex_home)

    assert not (project_root / "CLAUDE.md").exists()


def test_clean_does_not_touch_global_agents_md(
    make_project, make_plugin_version, tmp_path: Path
):
    """clean_project does not remove ~/.codex/AGENTS.md (that's uninstall's job)."""
    project_root, _agents_md = make_project()
    codex_home = tmp_path / "codex-home"

    # Create a global AGENTS.md manually (simulating a prior reconcile with user CLAUDE.md)
    (codex_home / "AGENTS.md").parent.mkdir(parents=True, exist_ok=True)
    (codex_home / "AGENTS.md").write_text("# Global instructions\n")

    # Create minimal state so clean has something to work with
    cache_root = tmp_path / "cache"
    from cc_codex_bridge.reconcile import clean_project
    desired = _reconcile_once(project_root, cache_root, codex_home)
    reconcile_desired_state(desired)

    clean_project(project_root, codex_home=codex_home)

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

    # Modify a managed file to create a pending change
    config = project_root / ".codex" / "config.toml"
    config.write_text("# tampered\n")

    report = reconcile_all(codex_home=codex_home, dry_run=True)
    # File was not restored
    assert config.read_text() == "# tampered\n"


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

    registry_data = _read_global_registry(codex_home)
    assert str(project_a) in registry_data["projects"]
    assert str(project_b) in registry_data["projects"]

    # Clean project A
    from cc_codex_bridge.reconcile import clean_project
    clean_project(project_a, codex_home=codex_home)

    # Project A removed, project B still present
    registry_data = _read_global_registry(codex_home)
    assert str(project_a) not in registry_data["projects"]
    assert str(project_b) in registry_data["projects"]


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

    registry_data = _read_global_registry(codex_home)
    assert str(project_root) in registry_data.get("projects", [])


def _reconcile_once(project_root, cache_root, codex_home):
    """Run a full discover+translate+reconcile and return the desired state."""
    from cc_codex_bridge.discover import discover
    from cc_codex_bridge.claude_shim import plan_claude_shim
    from cc_codex_bridge.translate_agents import translate_installed_agents_with_diagnostics
    from cc_codex_bridge.translate_skills import translate_installed_skills
    from cc_codex_bridge.render_codex_config import render_inline_codex_config, render_prompt_files
    from cc_codex_bridge.reconcile import build_desired_state

    result = discover(project_path=project_root, cache_dir=cache_root)
    shim_decision = plan_claude_shim(result.project)
    agent_result = translate_installed_agents_with_diagnostics(result.plugins)
    skills = translate_installed_skills(result.plugins)
    prompt_files = render_prompt_files(agent_result.roles)
    rendered_config = render_inline_codex_config(agent_result.roles)
    return build_desired_state(
        result, shim_decision, prompt_files, rendered_config, skills,
        codex_home=codex_home,
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
    roles = translate_installed_agents(discovery.plugins)
    skills = translate_installed_skills(discovery.plugins)
    prompt_files = render_prompt_files(roles)
    rendered_config = render_inline_codex_config(roles)
    return build_desired_state(
        discovery,
        shim_decision,
        prompt_files,
        rendered_config,
        skills,
        codex_home=codex_home,
    )


def _read_global_registry(codex_home: Path) -> dict[str, object]:
    """Read the global registry JSON payload for assertions."""
    return json.loads((codex_home / GLOBAL_REGISTRY_FILENAME).read_text())


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

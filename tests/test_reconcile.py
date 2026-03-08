"""Tests for reconcile behavior."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cc_codex_bridge.reconcile as reconcile_module
from cc_codex_bridge.claude_shim import plan_claude_shim
from cc_codex_bridge.discover import discover
from cc_codex_bridge.model import ReconcileError
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
        project_root / ".codex" / "prompts" / "agents" / "pirategoat-tools-architecture-reviewer.md"
    ).read_text() == "You are an architecture reviewer.\n"
    assert (project_root / STATE_RELATIVE_PATH).exists()
    assert (
        codex_home / "skills" / "pirategoat-tools-decision-critic" / "SKILL.md"
    ).read_text().startswith("---\nname: pirategoat-tools-decision-critic\n")


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
    installed_skill = codex_home / "skills" / "pirategoat-tools-decision-critic"
    assert installed_skill.exists()

    _, v2_dir = make_plugin_version("market", "pirategoat-tools", "1.0.1")
    # Remove the old skill source so the fake installed latest plugin has no skills.
    stale_source = v2_dir / "skills"
    if stale_source.exists():
        raise AssertionError("Unexpected skills directory in the later fixture version")

    second_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(second_desired)

    assert not installed_skill.exists()


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
    original_skill = first_home / "skills" / "prompt-engineer-prompt-engineer"
    assert original_skill.exists()

    reconcile_desired_state(_build_desired(project_root, cache_root, second_home))

    assert not original_skill.exists()
    assert (second_home / "skills" / "prompt-engineer-prompt-engineer").exists()
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
    prompt_path = project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
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
    (skills_root / "prompt-engineer-prompt-engineer").write_text("not a directory\n")

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
    skill_dir = codex_home / "skills" / "prompt-engineer-prompt-engineer"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("hand-authored\n")

    desired = _build_desired(project_root, cache_root, codex_home)

    with pytest.raises(ReconcileError, match="non-generated skill directory"):
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
                "version": 1,
                "project_root": str(project_root),
                "codex_home": str(tmp_path / "codex-home"),
                "selected_plugins": [],
                "managed_project_files": ["AGENTS.md", ".claude/settings.local.json"],
                "managed_codex_skill_dirs": [],
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


def test_reconcile_rolls_back_project_outputs_when_skill_swap_fails(
    make_project,
    make_plugin_version,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Later skill swap failures restore previously generated project outputs and state."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        skill_names=("prompt-engineer",),
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    skill_path = version_dir / "skills" / "prompt-engineer" / "SKILL.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nOld prompt.\n")
    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nOld skill.\n"
    )
    codex_home = tmp_path / "codex-home"

    first_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first_desired)

    original_config = (project_root / ".codex" / "config.toml").read_text()
    original_prompt = (
        project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    ).read_text()
    original_skill = (
        codex_home / "skills" / "prompt-engineer-prompt-engineer" / "SKILL.md"
    ).read_text()
    original_state = (project_root / STATE_RELATIVE_PATH).read_text()

    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nNew prompt.\n")
    skill_path.write_text(
        "---\nname: prompt-engineer\ndescription: Prompt help\n---\n\nNew skill.\n"
    )
    updated = _build_desired(project_root, cache_root, codex_home)

    original_swap_path = reconcile_module._swap_path

    def fail_on_skill_swap(destination: Path, staged_path: Path, backup_root: Path, *, is_dir: bool):
        if is_dir and destination.name == "prompt-engineer-prompt-engineer":
            raise RuntimeError("boom during skill swap")
        return original_swap_path(destination, staged_path, backup_root, is_dir=is_dir)

    monkeypatch.setattr(reconcile_module, "_swap_path", fail_on_skill_swap)

    with pytest.raises(RuntimeError, match="boom during skill swap"):
        reconcile_desired_state(updated)

    assert (project_root / ".codex" / "config.toml").read_text() == original_config
    assert (
        project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    ).read_text() == original_prompt
    assert (
        codex_home / "skills" / "prompt-engineer-prompt-engineer" / "SKILL.md"
    ).read_text() == original_skill
    assert (project_root / STATE_RELATIVE_PATH).read_text() == original_state


def test_reconcile_rolls_back_when_stale_prompt_removal_fails(
    make_project,
    make_plugin_version,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    """Stale-output removal failures restore the prior prompt tree and state."""
    project_root, _agents_md = make_project()
    cache_root, version_dir = make_plugin_version(
        "market",
        "prompt-engineer",
        "1.0.0",
        agent_names=("reviewer",),
    )
    agent_path = version_dir / "agents" / "reviewer.md"
    agent_path.write_text("---\nname: reviewer\ndescription: Review\n---\n\nPrompt body.\n")
    codex_home = tmp_path / "codex-home"

    first_desired = _build_desired(project_root, cache_root, codex_home)
    reconcile_desired_state(first_desired)

    prompt_path = project_root / ".codex" / "prompts" / "agents" / "prompt-engineer-reviewer.md"
    original_config = (project_root / ".codex" / "config.toml").read_text()
    original_prompt = prompt_path.read_text()
    original_state = (project_root / STATE_RELATIVE_PATH).read_text()

    agent_path.unlink()
    updated = _build_desired(project_root, cache_root, codex_home)

    original_remove_path = reconcile_module._remove_path

    def fail_on_prompt_removal(destination: Path, backup_root: Path, *, is_dir: bool):
        if destination == prompt_path:
            raise RuntimeError("boom during stale prompt removal")
        return original_remove_path(destination, backup_root, is_dir=is_dir)

    monkeypatch.setattr(reconcile_module, "_remove_path", fail_on_prompt_removal)

    with pytest.raises(RuntimeError, match="boom during stale prompt removal"):
        reconcile_desired_state(updated)

    assert (project_root / ".codex" / "config.toml").read_text() == original_config
    assert prompt_path.read_text() == original_prompt
    assert (project_root / STATE_RELATIVE_PATH).read_text() == original_state


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


def _build_desired(project_root: Path, cache_root: Path, codex_home: Path):
    """Build desired state from fixture project and cache roots."""
    discovery = discover(project_path=project_root, cache_dir=cache_root)
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

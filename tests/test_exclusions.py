"""Tests for exclusion config loading and discovery filtering."""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_codex_bridge.exclusions import (
    SyncExclusions,
    apply_sync_exclusions,
    load_project_exclusions,
    resolve_effective_exclusions,
)
from cc_codex_bridge.model import (
    DiscoveryResult,
    InstalledPlugin,
    ProjectContext,
    ReconcileError,
    SemVer,
)


def test_load_project_exclusions_reads_default_config_and_normalizes_agent_id(make_project):
    """Project exclusions load from `.codex/bridge.toml` when present."""
    project_root, agents_md = make_project()
    config_path = project_root / ".codex" / "bridge.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[exclude]\n"
        'plugins = ["market/prompt-engineer"]\n'
        'skills = ["market/prompt-engineer/internal"]\n'
        'agents = ["market/prompt-engineer/reviewer"]\n'
    )

    exclusions = load_project_exclusions(project_root)

    assert exclusions.plugins == ("market/prompt-engineer",)
    assert exclusions.skills == ("market/prompt-engineer/internal",)
    assert exclusions.agents == ("market/prompt-engineer/reviewer.md",)
    assert agents_md.exists()


def test_load_project_exclusions_rejects_invalid_id_format(make_project):
    """Malformed exclusion ids fail fast with a clear error."""
    project_root, _agents_md = make_project()
    config_path = project_root / ".codex" / "bridge.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text("[exclude]\nplugins = [\"broken\"]\n")

    with pytest.raises(ReconcileError, match="Invalid exclusion id"):
        load_project_exclusions(project_root)


def test_resolve_effective_exclusions_prefers_cli_values_per_kind():
    """CLI exclusions replace config exclusions for the same entity kind only."""
    config = SyncExclusions(
        plugins=("market/alpha",),
        skills=("market/alpha/one",),
        agents=("market/alpha/reviewer.md",),
    )

    resolved = resolve_effective_exclusions(
        config,
        cli_exclude_skills=["market/alpha/two"],
    )

    assert resolved.plugins == ("market/alpha",)
    assert resolved.skills == ("market/alpha/two",)
    assert resolved.agents == ("market/alpha/reviewer.md",)


def test_apply_sync_exclusions_filters_plugins_skills_and_agents(tmp_path: Path):
    """Filtering excludes whole plugins and individual nested entities."""
    plugin_alpha = InstalledPlugin(
        marketplace="market",
        plugin_name="alpha",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=tmp_path / "installed-alpha",
        source_path=tmp_path / "source-alpha",
        skills=(
            tmp_path / "source-alpha" / "skills" / "portable",
            tmp_path / "source-alpha" / "skills" / "cc-only",
        ),
        agents=(
            tmp_path / "source-alpha" / "agents" / "reviewer.md",
            tmp_path / "source-alpha" / "agents" / "helper.md",
        ),
        commands=(),
    )
    plugin_beta = InstalledPlugin(
        marketplace="market",
        plugin_name="beta",
        version_text="2.0.0",
        version=SemVer.parse("2.0.0"),
        installed_path=tmp_path / "installed-beta",
        source_path=tmp_path / "source-beta",
        skills=(tmp_path / "source-beta" / "skills" / "tooling",),
        agents=(tmp_path / "source-beta" / "agents" / "auditor.md",),
        commands=(),
    )
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(plugin_alpha, plugin_beta),
    )
    exclusions = SyncExclusions(
        plugins=("market/beta",),
        skills=("market/alpha/cc-only",),
        agents=("market/alpha/reviewer.md",),
    )

    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.plugins) == 1
    assert filtered.plugins[0].plugin_name == "alpha"
    assert tuple(path.name for path in filtered.plugins[0].skills) == ("portable",)
    assert tuple(path.name for path in filtered.plugins[0].agents) == ("helper.md",)
    assert report.plugins == ("market/beta",)
    assert report.skills == ("market/alpha/cc-only",)
    assert report.agents == ("market/alpha/reviewer.md",)


def test_exclude_standalone_skill_by_bare_name(tmp_path: Path):
    """A 1-part skill exclusion matches standalone skills in all scopes."""
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(),
        user_skills=(tmp_path / "user-skills" / "my-tool", tmp_path / "user-skills" / "other-tool"),
        project_skills=(tmp_path / "project-skills" / "my-tool",),
    )
    exclusions = SyncExclusions(skills=("my-tool",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.user_skills) == 1
    assert filtered.user_skills[0].name == "other-tool"
    assert len(filtered.project_skills) == 0
    assert "user/my-tool" in report.skills
    assert "project/my-tool" in report.skills


def test_exclude_scoped_standalone_skill(tmp_path: Path):
    """A 2-part skill exclusion matches only the specified scope."""
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(),
        user_skills=(tmp_path / "user-skills" / "run-tests",),
        project_skills=(tmp_path / "project-skills" / "run-tests",),
    )
    exclusions = SyncExclusions(skills=("user/run-tests",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.user_skills) == 0
    assert len(filtered.project_skills) == 1
    assert "user/run-tests" in report.skills


def test_exclude_standalone_agent_by_bare_name(tmp_path: Path):
    """A 1-part agent exclusion matches standalone agents in all scopes."""
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(),
        user_agents=(tmp_path / "user-agents" / "reviewer.md", tmp_path / "user-agents" / "helper.md"),
        project_agents=(tmp_path / "project-agents" / "reviewer.md",),
    )
    exclusions = SyncExclusions(agents=("reviewer.md",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.user_agents) == 1
    assert filtered.user_agents[0].name == "helper.md"
    assert len(filtered.project_agents) == 0


def test_exclude_scoped_standalone_agent(tmp_path: Path):
    """A 2-part agent exclusion matches only the specified scope."""
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(),
        user_agents=(tmp_path / "user-agents" / "reviewer.md",),
        project_agents=(tmp_path / "project-agents" / "reviewer.md",),
    )
    exclusions = SyncExclusions(agents=("project/reviewer.md",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.user_agents) == 1
    assert len(filtered.project_agents) == 0


def test_normalize_accepts_1_and_2_part_skill_exclusions():
    """The normalize path accepts 1-part and 2-part skill exclusion IDs."""
    exclusions = SyncExclusions(skills=("my-tool", "user/my-tool", "market/plugin/my-tool"))
    # Should not raise — all formats valid
    assert "my-tool" in exclusions.skills
    assert "user/my-tool" in exclusions.skills
    assert "market/plugin/my-tool" in exclusions.skills


def test_load_project_exclusions_accepts_standalone_ids(make_project):
    """bridge.toml accepts 1-part and 2-part skill/agent exclusion IDs."""
    project_root, _agents_md = make_project()
    config_path = project_root / ".codex" / "bridge.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[exclude]\n"
        'skills = ["my-tool", "user/my-tool", "market/plugin/my-tool"]\n'
        'agents = ["reviewer", "project/reviewer.md"]\n'
    )

    exclusions = load_project_exclusions(project_root)
    assert "my-tool" in exclusions.skills
    assert "user/my-tool" in exclusions.skills
    assert "market/plugin/my-tool" in exclusions.skills
    assert "reviewer.md" in exclusions.agents
    assert "project/reviewer.md" in exclusions.agents


def test_bare_name_exclusion_also_matches_plugin_skills(tmp_path: Path):
    """A 1-part skill exclusion matches plugin skills by bare name too."""
    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="alpha",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=tmp_path / "installed-alpha",
        source_path=tmp_path / "source-alpha",
        skills=(
            tmp_path / "source-alpha" / "skills" / "my-tool",
            tmp_path / "source-alpha" / "skills" / "other-tool",
        ),
        agents=(),
        commands=(),
    )
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(plugin,),
    )
    exclusions = SyncExclusions(skills=("my-tool",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.plugins) == 1
    assert tuple(p.name for p in filtered.plugins[0].skills) == ("other-tool",)
    assert "market/alpha/my-tool" in report.skills


def test_bare_name_exclusion_also_matches_plugin_agents(tmp_path: Path):
    """A 1-part agent exclusion matches plugin agents by bare name too."""
    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="alpha",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=tmp_path / "installed-alpha",
        source_path=tmp_path / "source-alpha",
        skills=(),
        agents=(
            tmp_path / "source-alpha" / "agents" / "reviewer.md",
            tmp_path / "source-alpha" / "agents" / "helper.md",
        ),
        commands=(),
    )
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(plugin,),
    )
    exclusions = SyncExclusions(agents=("reviewer.md",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.plugins) == 1
    assert tuple(p.name for p in filtered.plugins[0].agents) == ("helper.md",)
    assert "market/alpha/reviewer.md" in report.agents


# --- Command exclusion tests ---


def test_exclude_plugin_commands_by_3_part_id(tmp_path: Path):
    """Plugin commands can be excluded by 3-part id."""
    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="tools",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=tmp_path / "installed-tools",
        source_path=tmp_path / "source-tools",
        skills=(),
        agents=(),
        commands=(
            tmp_path / "source-tools" / "commands" / "review.md",
            tmp_path / "source-tools" / "commands" / "update.md",
        ),
    )
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(plugin,),
    )
    exclusions = SyncExclusions(commands=("market/tools/review.md",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.plugins[0].commands) == 1
    assert filtered.plugins[0].commands[0].name == "update.md"
    assert "market/tools/review.md" in report.commands


def test_exclude_commands_by_bare_name(tmp_path: Path):
    """Commands can be excluded by bare filename (matches all scopes)."""
    plugin = InstalledPlugin(
        marketplace="market",
        plugin_name="tools",
        version_text="1.0.0",
        version=SemVer.parse("1.0.0"),
        installed_path=tmp_path / "installed-tools",
        source_path=tmp_path / "source-tools",
        skills=(),
        agents=(),
        commands=(
            tmp_path / "source-tools" / "commands" / "review.md",
            tmp_path / "source-tools" / "commands" / "update.md",
        ),
    )
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(plugin,),
        user_commands=(tmp_path / "user-commands" / "review.md",),
        project_commands=(tmp_path / "project-commands" / "review.md",),
    )
    exclusions = SyncExclusions(commands=("review.md",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    # Plugin command excluded
    assert len(filtered.plugins[0].commands) == 1
    assert filtered.plugins[0].commands[0].name == "update.md"
    # Standalone user and project commands excluded
    assert len(filtered.user_commands) == 0
    assert len(filtered.project_commands) == 0
    assert "market/tools/review.md" in report.commands
    assert "user/review.md" in report.commands
    assert "project/review.md" in report.commands


def test_exclude_standalone_command_by_scope(tmp_path: Path):
    """A 2-part command exclusion matches only the specified scope."""
    discovery = DiscoveryResult(
        project=ProjectContext(
            root=tmp_path / "project",
            agents_md_path=tmp_path / "project" / "AGENTS.md",
        ),
        plugins=(),
        user_commands=(tmp_path / "user-commands" / "deploy.md",),
        project_commands=(tmp_path / "project-commands" / "deploy.md",),
    )
    exclusions = SyncExclusions(commands=("user/deploy.md",))
    filtered, report = apply_sync_exclusions(discovery, exclusions)

    assert len(filtered.user_commands) == 0
    assert len(filtered.project_commands) == 1
    assert "user/deploy.md" in report.commands


def test_command_exclusion_normalizes_md_extension():
    """Command exclusion IDs auto-append .md like agent IDs."""
    exclusions = SyncExclusions(commands=("review",))
    # Normalization happens at SyncExclusions construction via the caller;
    # test through load_project_exclusions to verify the full path.
    from cc_codex_bridge.exclusions import _normalize_id_list
    normalized = _normalize_id_list(["review", "market/tools/deploy"], kind="command")
    assert "review.md" in normalized
    assert "market/tools/deploy.md" in normalized


def test_load_project_exclusions_reads_commands(make_project):
    """Project exclusions load commands from bridge.toml."""
    project_root, _agents_md = make_project()
    config_path = project_root / ".codex" / "bridge.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "[exclude]\n"
        'commands = ["market/tools/review", "deploy"]\n'
    )

    exclusions = load_project_exclusions(project_root)
    assert "market/tools/review.md" in exclusions.commands
    assert "deploy.md" in exclusions.commands


def test_resolve_effective_exclusions_includes_commands():
    """CLI command exclusions override config command exclusions."""
    config = SyncExclusions(
        plugins=("market/alpha",),
        commands=("market/alpha/review.md",),
    )

    resolved = resolve_effective_exclusions(
        config,
        cli_exclude_commands=["market/alpha/deploy"],
    )

    assert resolved.plugins == ("market/alpha",)
    assert resolved.commands == ("market/alpha/deploy.md",)
    # Config commands replaced, not merged
    assert "market/alpha/review.md" not in resolved.commands


def test_resolve_effective_exclusions_preserves_commands_when_cli_is_none():
    """When no CLI command exclusions given, config values are preserved."""
    config = SyncExclusions(
        commands=("market/alpha/review.md",),
    )

    resolved = resolve_effective_exclusions(config)

    assert resolved.commands == ("market/alpha/review.md",)

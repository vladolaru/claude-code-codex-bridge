"""Tests for Codex bridge discovery and SemVer behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from cc_codex_bridge.discover import discover, discover_latest_plugins, resolve_project_root
from cc_codex_bridge.model import DiscoveryError, SemVer

def test_resolve_project_root_from_cwd(make_project, monkeypatch: pytest.MonkeyPatch):
    """Project root resolves from current working directory when AGENTS.md exists above."""
    project_root, agents_md = make_project()
    nested_dir = project_root / "src" / "feature"
    nested_dir.mkdir(parents=True)

    monkeypatch.chdir(nested_dir)
    result = resolve_project_root()

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_from_explicit_project_path(make_project):
    """Explicit project path can point inside a repo and still resolves upward."""
    project_root, agents_md = make_project()
    nested_dir = project_root / "docs" / "nested"
    nested_dir.mkdir(parents=True)

    result = resolve_project_root(nested_dir)

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_from_file_path(make_project):
    """Explicit file paths resolve from the parent directory."""
    project_root, agents_md = make_project()
    nested_file = project_root / "docs" / "guide.md"
    nested_file.parent.mkdir(parents=True)
    nested_file.write_text("guide\n")

    result = resolve_project_root(nested_file)

    assert result.root == project_root.resolve()
    assert result.agents_md_path == agents_md.resolve()


def test_resolve_project_root_prefers_nearest_nested_agents_md(make_project):
    """When multiple AGENTS.md files exist, the nearest ancestor wins."""
    project_root, _root_agents = make_project()
    nested_root = project_root / "packages" / "dashboard"
    nested_root.mkdir(parents=True)
    nested_agents = nested_root / "AGENTS.md"
    nested_agents.write_text("# Nested instructions\n")

    deeper_dir = nested_root / "src" / "components"
    deeper_dir.mkdir(parents=True)

    result = resolve_project_root(deeper_dir)

    assert result.root == nested_root.resolve()
    assert result.agents_md_path == nested_agents.resolve()


def test_resolve_project_root_uses_nearest_agents_md_in_vendored_style_subtree(make_project):
    """Vendored-style dependency paths still resolve to the nearest nested AGENTS.md."""
    project_root, _root_agents = make_project()
    vendor_root = (
        project_root
        / "node_modules"
        / ".pnpm"
        / "package@1.0.0"
        / "node_modules"
        / "@scope"
        / "package"
    )
    vendor_root.mkdir(parents=True)
    vendor_agents = vendor_root / "AGENTS.md"
    vendor_agents.write_text("# Vendored package instructions\n")

    nested_dir = vendor_root / "src" / "internal"
    nested_dir.mkdir(parents=True)

    result = resolve_project_root(nested_dir)

    assert result.root == vendor_root.resolve()
    assert result.agents_md_path == vendor_agents.resolve()


def test_resolve_project_root_requires_agents_md(tmp_path: Path):
    """Missing AGENTS.md is a hard discovery failure."""
    project_root = tmp_path / "project"
    project_root.mkdir()

    with pytest.raises(DiscoveryError, match="AGENTS.md"):
        resolve_project_root(project_root)


def test_resolve_project_root_falls_back_to_claude_md(tmp_path: Path):
    """Project root resolved via CLAUDE.md when AGENTS.md is absent."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    (project_root / "CLAUDE.md").write_text("# Project instructions\n")

    ctx = resolve_project_root(project_root)

    assert ctx.root == project_root.resolve()
    assert ctx.agents_md_path == project_root.resolve() / "AGENTS.md"
    assert not ctx.agents_md_path.exists()  # AGENTS.md not created yet


def test_resolve_project_root_claude_md_fallback_walks_upward(tmp_path: Path):
    """CLAUDE.md fallback searches parent directories like AGENTS.md does."""
    project_root = tmp_path / "project"
    nested = project_root / "src" / "feature"
    nested.mkdir(parents=True)
    (project_root / "CLAUDE.md").write_text("# Project instructions\n")

    ctx = resolve_project_root(nested)

    assert ctx.root == project_root.resolve()
    assert ctx.agents_md_path == project_root.resolve() / "AGENTS.md"


def test_resolve_project_root_prefers_agents_md_over_claude_md(tmp_path: Path):
    """AGENTS.md takes priority when both exist."""
    project_root = tmp_path / "project"
    project_root.mkdir()
    agents_md = project_root / "AGENTS.md"
    agents_md.write_text("# Agents\n")
    (project_root / "CLAUDE.md").write_text("@AGENTS.md\n")

    ctx = resolve_project_root(project_root)

    assert ctx.root == project_root.resolve()
    assert ctx.agents_md_path == agents_md.resolve()


def test_discover_latest_plugins_uses_semver_order(make_plugin_version):
    """Latest installed plugin version is selected by semantic version precedence."""
    cache_root, _ = make_plugin_version(
        "market", "prompt-engineer", "2.0.0", skill_names=("prompt-engineer",)
    )
    make_plugin_version(
        "market", "prompt-engineer", "2.1.0", skill_names=("prompt-engineer",)
    )
    make_plugin_version(
        "market", "prompt-engineer", "2.0.5", skill_names=("prompt-engineer",)
    )

    plugins = discover_latest_plugins(cache_root)

    assert len(plugins) == 1
    assert plugins[0].plugin_name == "prompt-engineer"
    assert plugins[0].version_text == "2.1.0"


def test_discover_latest_plugins_ignores_invalid_version_dirs(make_plugin_version):
    """Malformed version directories are ignored."""
    cache_root, _ = make_plugin_version(
        "market", "dex", "1.5.3", skill_names=("knowledge-capture",)
    )
    (cache_root / "market" / "dex" / "latest").mkdir(parents=True)

    plugins = discover_latest_plugins(cache_root)

    assert len(plugins) == 1
    assert plugins[0].version_text == "1.5.3"


def test_discover_latest_plugins_skips_if_no_valid_versions(tmp_path: Path):
    """A plugin with no valid semantic versions is skipped, not fatal."""
    cache_root = tmp_path / "claude-cache"
    (cache_root / "market" / "broken-plugin" / "latest").mkdir(parents=True)

    plugins = discover_latest_plugins(cache_root)
    assert plugins == ()


def test_discover_latest_plugins_skips_malformed_plugin_dir(tmp_path: Path):
    """A plugin directory with no valid versions is skipped, not fatal."""
    cache_root = tmp_path / "cache"

    # Valid plugin
    good_dir = cache_root / "market" / "good" / "1.0.0"
    good_dir.mkdir(parents=True)

    # Malformed plugin — no valid semver subdirectory
    bad_dir = cache_root / "market" / "broken"
    bad_dir.mkdir(parents=True)
    (bad_dir / "not-a-version").mkdir()

    plugins = discover_latest_plugins(cache_dir=cache_root)
    assert len(plugins) == 1
    assert plugins[0].plugin_name == "good"


def test_discover_latest_plugins_returns_empty_for_missing_cache(tmp_path: Path):
    """A missing cache root returns an empty result instead of raising."""
    plugins = discover_latest_plugins(tmp_path / "missing-cache")
    assert plugins == ()


def test_discover_latest_plugins_returns_empty_for_empty_cache(tmp_path: Path):
    """An empty cache root returns an empty result instead of raising."""
    cache_root = tmp_path / "claude-cache"
    cache_root.mkdir()

    plugins = discover_latest_plugins(cache_root)
    assert plugins == ()


def test_discover_latest_plugins_follows_symlinked_repo_source(tmp_path: Path):
    """Installed plugin versions that are symlinks resolve to the repo target."""
    cache_root = tmp_path / "claude-cache"
    repo_root = tmp_path / "repo" / "prompt-engineer"
    skills_dir = repo_root / "skills" / "prompt-engineer"
    agents_dir = repo_root / "agents"
    skills_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: prompt-engineer\ndescription: test skill\n---\n"
    )
    (agents_dir / "optimizer.md").write_text(
        "---\nname: optimizer\ndescription: test agent\n---\n"
    )

    install_link = cache_root / "market" / "prompt-engineer" / "2.1.0"
    install_link.parent.mkdir(parents=True, exist_ok=True)
    install_link.symlink_to(repo_root, target_is_directory=True)

    plugins = discover_latest_plugins(cache_root)

    assert len(plugins) == 1
    assert plugins[0].installed_path == install_link
    assert plugins[0].source_path == repo_root.resolve()
    assert plugins[0].skills == (skills_dir.resolve(),)
    assert plugins[0].agents == ((agents_dir / "optimizer.md").resolve(),)


def test_discover_combines_project_and_plugins(make_project, make_plugin_version):
    """Top-level discover returns both project and installed plugin information."""
    project_root, _agents_md = make_project()
    cache_root, _ = make_plugin_version(
        "market",
        "prompt-engineer",
        "2.1.0",
        skill_names=("prompt-engineer",),
        agent_names=("optimizer",),
    )

    result = discover(project_root, cache_root)

    assert result.project.root == project_root.resolve()
    assert len(result.plugins) == 1
    assert result.plugins[0].plugin_name == "prompt-engineer"
    assert result.plugins[0].version_text == "2.1.0"


def test_semver_prerelease_ordering_and_type_guards():
    """Prerelease precedence follows semver ordering rules."""
    alpha1 = SemVer.parse("1.2.3-alpha.1")
    alpha2 = SemVer.parse("1.2.3-alpha.2")
    beta = SemVer.parse("1.2.3-beta")
    stable = SemVer.parse("1.2.3")

    assert alpha1 < alpha2 < beta < stable
    assert SemVer.__lt__(stable, "1.2.4") is NotImplemented


# ---------------------------------------------------------------------------
# User-level and project-level skill/agent discovery
# ---------------------------------------------------------------------------


def _make_minimal_plugin(
    cache_root: Path,
    marketplace: str = "market",
    plugin_name: str = "test-plugin",
    version: str = "1.0.0",
) -> None:
    """Create a minimal valid plugin in the cache so discover() succeeds."""
    version_dir = cache_root / marketplace / plugin_name / version
    version_dir.mkdir(parents=True, exist_ok=True)
    skills_dir = version_dir / "skills" / "minimal"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\nname: minimal\ndescription: test\n---\n"
    )


def test_discover_finds_user_level_skills_and_agents(make_project, tmp_path: Path):
    """Discovery includes user-level skills and agents from Claude home."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root)

    # Create user-level skill
    user_skill = claude_home / "skills" / "my-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: test\n---\n"
    )

    # Create user-level agent
    user_agents = claude_home / "agents"
    user_agents.mkdir(parents=True)
    (user_agents / "my-agent.md").write_text(
        "---\nname: my-agent\ndescription: test\ntools:\n  - Read\n---\nPrompt.\n"
    )

    result = discover(project_path=project_root, claude_home=claude_home)

    assert len(result.user_skills) == 1
    assert result.user_skills[0].name == "my-skill"
    assert len(result.user_agents) == 1
    assert result.user_agents[0].name == "my-agent.md"


def test_discover_finds_project_level_skills_and_agents(make_project, tmp_path: Path):
    """Discovery includes project-level skills and agents."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root)

    # Create project-level skill
    project_skill = project_root / ".claude" / "skills" / "run-tests"
    project_skill.mkdir(parents=True)
    (project_skill / "SKILL.md").write_text(
        "---\nname: run-tests\ndescription: Run tests\n---\n"
    )

    # Create project-level agent
    project_agents = project_root / ".claude" / "agents"
    project_agents.mkdir(parents=True)
    (project_agents / "code-reviewer.md").write_text(
        "---\nname: code-reviewer\ndescription: Review code\n---\nReview.\n"
    )

    result = discover(project_path=project_root, claude_home=claude_home)

    assert len(result.project_skills) == 1
    assert result.project_skills[0].name == "run-tests"
    assert len(result.project_agents) == 1
    assert result.project_agents[0].name == "code-reviewer.md"


def test_discover_returns_empty_when_no_user_or_project_sources(
    make_project, tmp_path: Path
):
    """Missing non-plugin source directories yield empty tuples."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root)

    result = discover(project_path=project_root, claude_home=claude_home)

    assert result.user_skills == ()
    assert result.user_agents == ()
    assert result.project_skills == ()
    assert result.project_agents == ()


def test_discover_skips_skill_dirs_without_skill_md(make_project, tmp_path: Path):
    """Incomplete skill directories (missing SKILL.md) are ignored."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    cache_root = claude_home / "plugins" / "cache"
    _make_minimal_plugin(cache_root)

    # Create a skill dir without SKILL.md
    incomplete = claude_home / "skills" / "incomplete"
    incomplete.mkdir(parents=True)
    # Create a valid skill dir
    valid = claude_home / "skills" / "valid"
    valid.mkdir(parents=True)
    (valid / "SKILL.md").write_text("---\nname: valid\ndescription: test\n---\n")

    result = discover(project_path=project_root, claude_home=claude_home)

    assert len(result.user_skills) == 1
    assert result.user_skills[0].name == "valid"


def test_discover_works_with_only_user_skills_and_no_plugins(make_project, tmp_path: Path):
    """Discovery succeeds with user-level skills even when plugin cache is empty."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    (claude_home / "plugins" / "cache").mkdir(parents=True)  # empty cache

    user_skill = claude_home / "skills" / "my-skill"
    user_skill.mkdir(parents=True)
    (user_skill / "SKILL.md").write_text("---\nname: my-skill\ndescription: test\n---\n")

    result = discover(project_path=project_root, claude_home=claude_home)

    assert len(result.plugins) == 0
    assert len(result.user_skills) == 1

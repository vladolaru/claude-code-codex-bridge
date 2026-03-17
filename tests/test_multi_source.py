"""End-to-end multi-source integration test."""

from __future__ import annotations

from pathlib import Path

from cc_codex_bridge import cli


def _make_minimal_plugin(
    cache_root: Path,
    marketplace: str,
    plugin_name: str,
    version: str,
) -> Path:
    """Create a minimal plugin with one skill and one agent."""
    plugin_dir = cache_root / marketplace / plugin_name / version
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(f'{{"name": "{plugin_name}", "version": "{version}"}}')

    skill_dir = plugin_dir / "skills" / "test-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: test-skill\ndescription: A test skill\n---\n\nPlugin skill content.\n"
    )

    agent_dir = plugin_dir / "agents"
    agent_dir.mkdir(parents=True)
    (agent_dir / "test-agent.md").write_text(
        "---\nname: test-agent\ndescription: A test agent\nmodel: claude-sonnet-4-20250514\ntools:\n  - Read\n---\n\nPlugin agent prompt.\n"
    )

    return plugin_dir


def _make_user_skill(claude_home: Path, name: str) -> Path:
    """Create a user-level skill directory."""
    skill_dir = claude_home / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: User skill {name}\n---\n\nUser skill content for {name}.\n"
    )
    return skill_dir


def _make_user_agent(claude_home: Path, name: str) -> Path:
    """Create a user-level agent file."""
    agents_dir = claude_home / "agents"
    agents_dir.mkdir(parents=True)
    agent_path = agents_dir / f"{name}.md"
    agent_path.write_text(
        f"---\nname: {name}\ndescription: User agent {name}\nmodel: claude-sonnet-4-20250514\ntools:\n  - Read\n---\n\nUser agent prompt for {name}.\n"
    )
    return agent_path


def _make_project_skill(project_root: Path, name: str) -> Path:
    """Create a project-level skill directory."""
    skill_dir = project_root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Project skill {name}\n---\n\nProject skill content for {name}.\n"
    )
    return skill_dir


def _make_project_agent(project_root: Path, name: str) -> Path:
    """Create a project-level agent file."""
    agents_dir = project_root / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    agent_path = agents_dir / f"{name}.md"
    agent_path.write_text(
        f"---\nname: {name}\ndescription: Project agent {name}\nmodel: claude-sonnet-4-20250514\ntools:\n  - Read\n---\n\nProject agent prompt for {name}.\n"
    )
    return agent_path


def test_reconcile_combines_all_source_types(make_project, tmp_path: Path, capsys):
    """Reconcile generates Codex artifacts from plugins, user sources, and project sources."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    # 1. Plugin with one skill and one agent
    _make_minimal_plugin(cache_root, "market", "test-plugin", "1.0.0")

    # 2. User-level skill
    _make_user_skill(claude_home, "url-shorthand")

    # 3. User-level agent
    _make_user_agent(claude_home, "thinking-partner")

    # 4. Project-level skill
    _make_project_skill(project_root, "run-tests")

    # 5. Project-level agent
    _make_project_agent(project_root, "code-reviewer")

    # 6. User-level CLAUDE.md
    (claude_home / "CLAUDE.md").write_text("Always use conventional commits.\n")

    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ])

    assert exit_code == 0

    # Plugin skill → global registry (bare name)
    plugin_skill_md = codex_home / "skills" / "test-skill" / "SKILL.md"
    assert plugin_skill_md.exists()

    # User skill → global registry (bare name)
    user_skill_md = codex_home / "skills" / "url-shorthand" / "SKILL.md"
    assert user_skill_md.exists()

    # Project skill → project-local .codex/skills/ (raw name, no prefix)
    project_skill_md = project_root / ".codex" / "skills" / "run-tests" / "SKILL.md"
    assert project_skill_md.exists()
    # NOT in global registry
    assert not (codex_home / "skills" / "run-tests" / "SKILL.md").exists()
    assert not (codex_home / "skills" / "project-run-tests" / "SKILL.md").exists()

    # Global agents (plugin + user) → ~/.codex/agents/*.toml
    global_agents_dir = codex_home / "agents"
    assert global_agents_dir.exists()
    global_agent_files = sorted(f.name for f in global_agents_dir.glob("*.toml"))
    assert "market-test-plugin-test-agent.toml" in global_agent_files  # plugin agent
    assert "user-thinking-partner.toml" in global_agent_files  # user agent

    # Plugin agent .toml content
    plugin_agent_toml = (global_agents_dir / "market-test-plugin-test-agent.toml").read_text()
    assert "market_test-plugin_test_agent" in plugin_agent_toml

    # User agent .toml content
    user_agent_toml = (global_agents_dir / "user-thinking-partner.toml").read_text()
    assert "user_thinking_partner" in user_agent_toml

    # Project agent → .codex/agents/*.toml
    project_agents_dir = project_root / ".codex" / "agents"
    assert project_agents_dir.exists()
    project_agent_files = sorted(f.name for f in project_agents_dir.glob("*.toml"))
    assert "project-code-reviewer.toml" in project_agent_files

    project_agent_toml = (project_agents_dir / "project-code-reviewer.toml").read_text()
    assert "project_code_reviewer" in project_agent_toml

    # config.toml no longer generated
    assert not (project_root / ".codex" / "config.toml").exists()

    # prompts/agents/ no longer generated
    assert not (project_root / ".codex" / "prompts" / "agents").exists()

    # User CLAUDE.md bridged to Codex global instructions
    codex_agents_md = codex_home / "AGENTS.md"
    assert codex_agents_md.exists()
    assert "conventional commits" in codex_agents_md.read_text()

    # Project-level CLAUDE.md shim unchanged
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"


def test_reconcile_works_with_no_plugins(make_project, tmp_path: Path, capsys):
    """Reconcile succeeds with only non-plugin sources."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    (claude_home / "plugins" / "cache").mkdir(parents=True)  # empty cache

    # User skill only
    _make_user_skill(claude_home, "my-tool")

    # Project agent only
    _make_project_agent(project_root, "reviewer")

    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ])

    assert exit_code == 0
    assert (codex_home / "skills" / "my-tool" / "SKILL.md").exists()

    # Project agent → .codex/agents/*.toml
    project_agent_toml = project_root / ".codex" / "agents" / "project-reviewer.toml"
    assert project_agent_toml.exists()
    assert "project_reviewer" in project_agent_toml.read_text()

    # config.toml no longer generated
    assert not (project_root / ".codex" / "config.toml").exists()


def test_reconcile_resolves_user_plugin_skill_name_collision(make_project, tmp_path: Path, capsys):
    """When user and plugin skills share a directory name, user wins the bare name."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    # Plugin skill named "review"
    plugin_dir = cache_root / "market" / "test-plugin" / "1.0.0"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text('{"name": "test-plugin", "version": "1.0.0"}')
    plugin_skill = plugin_dir / "skills" / "review"
    plugin_skill.mkdir(parents=True)
    (plugin_skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Plugin review\n---\n\nPlugin review content.\n"
    )

    # User skill also named "review"
    _make_user_skill(claude_home, "review")

    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ])

    assert exit_code == 0

    # User skill wins bare name
    user_review_md = codex_home / "skills" / "review" / "SKILL.md"
    assert user_review_md.exists()
    assert "User skill content" in user_review_md.read_text()

    # Plugin skill gets -alt suffix
    plugin_review_md = codex_home / "skills" / "review-alt" / "SKILL.md"
    assert plugin_review_md.exists()
    assert "Plugin review content" in plugin_review_md.read_text()


def test_reconcile_is_idempotent_with_all_sources(make_project, tmp_path: Path, capsys):
    """Running reconcile twice with the same inputs produces the same output."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    _make_minimal_plugin(cache_root, "market", "test-plugin", "1.0.0")
    _make_user_skill(claude_home, "my-tool")
    _make_project_skill(project_root, "local-tool")
    _make_project_agent(project_root, "reviewer")
    (claude_home / "CLAUDE.md").write_text("Global instructions.\n")

    args = [
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ]

    # First run
    assert cli.main(args) == 0

    # Capture state after first run
    global_agents_md_v1 = (codex_home / "AGENTS.md").read_text()
    plugin_agent_v1 = (codex_home / "agents" / "market-test-plugin-test-agent.toml").read_text()
    project_agent_v1 = (project_root / ".codex" / "agents" / "project-reviewer.toml").read_text()

    # Second run
    assert cli.main(args) == 0

    # State unchanged
    assert (codex_home / "AGENTS.md").read_text() == global_agents_md_v1
    assert (codex_home / "agents" / "market-test-plugin-test-agent.toml").read_text() == plugin_agent_v1
    assert (project_root / ".codex" / "agents" / "project-reviewer.toml").read_text() == project_agent_v1

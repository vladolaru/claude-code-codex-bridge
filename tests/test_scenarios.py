"""Scenario-driven e2e tests with realistic mock trees.

Each test models a real-world usage story: a user with a specific setup
runs the bridge and gets a working Codex environment. Builders create
mock trees that match production-observed structures.
"""

from __future__ import annotations

import os
from pathlib import Path

from cc_codex_bridge import cli
from cc_codex_bridge.bridge_home import project_state_dir


def _bridge_state_path(project_root: Path, tmp_path: Path) -> Path:
    """Compute the bridge-home state path for a project in test context."""
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    return project_state_dir(project_root, bridge_home=bridge_home) / "state.json"


# ---------------------------------------------------------------------------
# Builder: realistic marketplace plugin
# ---------------------------------------------------------------------------

def build_plugin(
    cache_root: Path,
    marketplace: str,
    plugin_name: str,
    version: str,
) -> Path:
    """Build a realistic plugin with multiple skills and agents.

    Modeled after pirategoat-tools: skills with scripts/, references/,
    sibling references, companion files alongside SKILL.md, agents with
    full tool sets, and .DS_Store noise.
    """
    plugin_dir = cache_root / marketplace / plugin_name / version
    plugin_dir.mkdir(parents=True)

    # -- Skill: code-review (simple skill with a script) --
    code_review = plugin_dir / "skills" / "code-review"
    code_review.mkdir(parents=True)
    (code_review / "SKILL.md").write_text(
        "---\n"
        "name: code-review\n"
        "description: Run a multi-agent code review on the current branch\n"
        "---\n"
        "\n"
        "# Code Review\n"
        "\n"
        "Use this skill when asked to review code changes.\n"
        "\n"
        "## Process\n"
        "\n"
        "1. Gather context from the current branch\n"
        "2. Dispatch review agents in parallel\n"
        "3. Consolidate findings\n"
        "4. Present actionable summary\n"
    )
    scripts_dir = code_review / "scripts"
    scripts_dir.mkdir()
    review_script = scripts_dir / "run-review.py"
    review_script.write_text(
        "#!/usr/bin/env python3\n"
        '"""Dispatch parallel review agents."""\n'
        "\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        "def main():\n"
        "    print('Running code review...')\n"
        "    return 0\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    sys.exit(main())\n"
    )
    review_script.chmod(0o755)

    # -- Skill: software-architecture (skill with references/ and companion files) --
    arch = plugin_dir / "skills" / "software-architecture"
    arch.mkdir(parents=True)
    (arch / "SKILL.md").write_text(
        "---\n"
        "name: software-architecture\n"
        "description: Software architecture patterns and SOLID principles\n"
        "---\n"
        "\n"
        "# Software Architecture\n"
        "\n"
        "Use when designing systems, choosing patterns, or refactoring.\n"
        "\n"
        "See references/patterns/ for the full pattern catalog.\n"
        "See solid-principles.md for SOLID breakdown.\n"
    )
    (arch / "solid-principles.md").write_text(
        "# SOLID Principles\n\n"
        "## Single Responsibility\nA class should have one reason to change.\n\n"
        "## Open/Closed\nOpen for extension, closed for modification.\n\n"
        "## Liskov Substitution\nSubtypes must be substitutable for base types.\n\n"
        "## Interface Segregation\nMany specific interfaces beat one general.\n\n"
        "## Dependency Inversion\nDepend on abstractions, not concretions.\n"
    )
    refs = arch / "references"
    refs.mkdir()
    patterns = refs / "patterns"
    patterns.mkdir()
    (patterns / "strategy.md").write_text(
        "# Strategy Pattern\n\nDefine a family of algorithms, encapsulate each one.\n"
    )
    (patterns / "observer.md").write_text(
        "# Observer Pattern\n\nDefine a one-to-many dependency between objects.\n"
    )

    # -- Skill: accessible-frontend (skill that references a sibling) --
    a11y = plugin_dir / "skills" / "accessible-frontend"
    a11y.mkdir(parents=True)
    (a11y / "SKILL.md").write_text(
        "---\n"
        "name: accessible-frontend\n"
        "description: Accessible frontend component development\n"
        "---\n"
        "\n"
        "# Accessible Frontend Dev\n"
        "\n"
        "When building components, always check ../shared-references/ for\n"
        "the ARIA patterns guide at ../shared-references/aria-patterns.md.\n"
    )

    # -- Sibling directory referenced by accessible-frontend --
    shared_refs = plugin_dir / "skills" / "shared-references"
    shared_refs.mkdir(parents=True)
    (shared_refs / "SKILL.md").write_text(
        "---\nname: shared-references\ndescription: Shared reference material\n---\n"
    )
    (shared_refs / "aria-patterns.md").write_text(
        "# ARIA Patterns\n\nCombobox, dialog, tabs, tree patterns.\n"
    )

    # -- .DS_Store noise in skills dir --
    (plugin_dir / "skills" / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")

    # -- Agent: security-reviewer (full tool set) --
    agents = plugin_dir / "agents"
    agents.mkdir(parents=True)
    (agents / "security-reviewer.md").write_text(
        "---\n"
        "name: security-reviewer\n"
        "description: WordPress security-focused code review\n"
        "model: claude-sonnet-4-20250514\n"
        "tools:\n"
        "  - Read\n"
        "  - Glob\n"
        "  - Grep\n"
        "  - Bash\n"
        "  - Write\n"
        "  - WebSearch\n"
        "---\n"
        "\n"
        "You are a security-focused code reviewer.\n"
        "\n"
        "## Review Checklist\n"
        "\n"
        "1. Check for SQL injection vulnerabilities\n"
        "2. Verify all user input is sanitized\n"
        "3. Ensure nonces are validated on form submissions\n"
        "4. Check capability checks on admin actions\n"
        "5. Verify output escaping with esc_html, esc_attr, etc.\n"
    )

    # -- Agent: a11y-reviewer (subset of tools) --
    (agents / "a11y-reviewer.md").write_text(
        "---\n"
        "name: a11y-reviewer\n"
        "description: Frontend accessibility code review\n"
        "model: claude-sonnet-4-20250514\n"
        "tools:\n"
        "  - Read\n"
        "  - Glob\n"
        "  - Grep\n"
        "---\n"
        "\n"
        "You are an accessibility-focused reviewer.\n"
        "\n"
        "Check ARIA attributes, keyboard navigation,\n"
        "focus management, and screen reader announcements.\n"
    )

    # -- Agent: performance-reviewer (another full tool set) --
    (agents / "performance-reviewer.md").write_text(
        "---\n"
        "name: performance-reviewer\n"
        "description: WordPress performance-focused code review\n"
        "model: claude-sonnet-4-20250514\n"
        "tools:\n"
        "  - Read\n"
        "  - Grep\n"
        "  - Bash\n"
        "---\n"
        "\n"
        "You review code for performance issues.\n"
        "\n"
        "Focus on database queries, caching, and asset loading.\n"
    )

    return plugin_dir


# ---------------------------------------------------------------------------
# Builder: user-level CLAUDE.md
# ---------------------------------------------------------------------------

def build_user_claude_md(claude_home: Path) -> Path:
    """Build a realistic user-level CLAUDE.md with multi-section instructions."""
    path = claude_home / "CLAUDE.md"
    claude_home.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Personal Instructions\n"
        "\n"
        "## Terminology\n"
        "\n"
        "| Abbreviation | Meaning |\n"
        "|--------------|----------------------------------------------|\n"
        "| a8c          | Automattic (8 letters between 'a' and 'c')  |\n"
        "| a11n         | Automattician                                |\n"
        "\n"
        "## Git Commits\n"
        "\n"
        "Uses Conventional Commits specification.\n"
        "\n"
        "### RULE 0\n"
        "One logical change per commit. If you write 'and', STOP and split.\n"
        "\n"
        "### RULE 1\n"
        "The human is the sole author of all commits.\n"
        "Omit any Co-Authored-By footer.\n"
        "\n"
        "## Code Search\n"
        "\n"
        "Use Grep for text, ast-grep for structure.\n"
        "\n"
        "## GitHub PRs\n"
        "\n"
        "Always read the current PR description before updating it.\n"
    )
    return path


# ---------------------------------------------------------------------------
# Builder: user-level skills
# ---------------------------------------------------------------------------

def build_user_skill_simple(claude_home: Path, name: str) -> Path:
    """Build a simple user-level skill (SKILL.md only)."""
    skill_dir = claude_home / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: User skill for {name}\n"
        f"---\n"
        f"\n"
        f"# {name}\n"
        f"\n"
        f"Use this skill when you need to {name.replace('-', ' ')}.\n"
    )
    return skill_dir


def build_user_skill_with_references(claude_home: Path, name: str) -> Path:
    """Build a user-level skill with a references/ subdirectory."""
    skill_dir = claude_home / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: User skill for {name} with reference material\n"
        f"---\n"
        f"\n"
        f"# {name}\n"
        f"\n"
        f"See references/ for supporting material.\n"
    )
    refs = skill_dir / "references"
    refs.mkdir()
    (refs / "examples.md").write_text(
        f"# Examples for {name}\n\n"
        f"## Example 1\nHere is how to use this correctly.\n\n"
        f"## Example 2\nHere is an advanced usage pattern.\n"
    )
    (refs / "guidelines.md").write_text(
        f"# Guidelines for {name}\n\n"
        f"Follow these rules when using this skill.\n"
    )
    return skill_dir


# ---------------------------------------------------------------------------
# Builder: user-level agents
# ---------------------------------------------------------------------------

def build_user_agent(claude_home: Path, name: str, *, tools: tuple[str, ...] = ("Read",)) -> Path:
    """Build a user-level agent with realistic content."""
    agents_dir = claude_home / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    tools_yaml = "\n".join(f"  - {t}" for t in tools)
    agent_path = agents_dir / f"{name}.md"
    agent_path.write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: Thinking partner for technical decisions\n"
        f"model: claude-sonnet-4-20250514\n"
        f"tools:\n"
        f"{tools_yaml}\n"
        f"---\n"
        f"\n"
        f"You are a thinking partner, not an implementer.\n"
        f"\n"
        f"## Approach\n"
        f"\n"
        f"1. Ask clarifying questions before suggesting solutions\n"
        f"2. Explore trade-offs explicitly\n"
        f"3. Challenge assumptions constructively\n"
        f"4. Summarize the decision and rationale\n"
    )
    return agent_path


# ---------------------------------------------------------------------------
# Builder: project-level skills
# ---------------------------------------------------------------------------

def build_project_skill(project_root: Path, name: str) -> Path:
    """Build a project-level skill with realistic content."""
    skill_dir = project_root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: Project-specific skill for {name}\n"
        f"---\n"
        f"\n"
        f"# {name}\n"
        f"\n"
        f"This skill is specific to this project.\n"
        f"\n"
        f"## Usage\n"
        f"\n"
        f"Invoke with /{name} to activate.\n"
    )
    return skill_dir


# ---------------------------------------------------------------------------
# Builder: project-level agents
# ---------------------------------------------------------------------------

def build_project_agent(
    project_root: Path,
    name: str,
    *,
    tools: tuple[str, ...] = ("Read", "Glob", "Grep"),
) -> Path:
    """Build a project-level agent with realistic content."""
    agents_dir = project_root / ".claude" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    tools_yaml = "\n".join(f"  - {t}" for t in tools)
    agent_path = agents_dir / f"{name}.md"
    agent_path.write_text(
        f"---\n"
        f"name: {name}\n"
        f"description: Project agent for {name}\n"
        f"model: claude-sonnet-4-20250514\n"
        f"tools:\n"
        f"{tools_yaml}\n"
        f"---\n"
        f"\n"
        f"You are a project-specific agent for {name}.\n"
        f"\n"
        f"Follow the project conventions in AGENTS.md.\n"
    )
    return agent_path


# ---------------------------------------------------------------------------
# Builder: bridge.toml exclusion config
# ---------------------------------------------------------------------------

def build_bridge_toml(project_root: Path, content: str) -> Path:
    """Write a bridge.toml exclusion config."""
    config_path = project_root / ".codex" / "bridge.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content)
    return config_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def reconcile(project_root: Path, claude_home: Path, codex_home: Path) -> int:
    """Run the reconcile pipeline and return the exit code."""
    return cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
    ])


# ===========================================================================
# Scenario tests
# ===========================================================================


def test_rich_plugin_ecosystem(make_project, tmp_path: Path):
    """A plugin with multiple skills (scripts, references, sibling refs) and agents.

    Models a real review-orchestration plugin like pirategoat-tools.
    Verifies that all skills land in the global registry with correct content,
    sibling references are vendored, scripts preserve permissions, .DS_Store
    noise is filtered, and all agents appear in config.toml with correct tools.
    """
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")

    assert reconcile(project_root, claude_home, codex_home) == 0

    skills_root = codex_home / "skills"

    # -- code-review skill: scripts/ preserved with permissions --
    cr_dir = skills_root / "code-review"
    cr_skill = cr_dir / "SKILL.md"
    assert cr_skill.exists()
    assert "multi-agent code review" in cr_skill.read_text()
    cr_script = cr_dir / "scripts" / "run-review.py"
    assert cr_script.exists()
    assert cr_script.stat().st_mode & 0o111  # executable bit preserved

    # -- software-architecture skill: companion files + nested references --
    arch_dir = skills_root / "software-architecture"
    assert (arch_dir / "SKILL.md").exists()
    assert (arch_dir / "solid-principles.md").exists()
    assert "Single Responsibility" in (arch_dir / "solid-principles.md").read_text()
    assert (arch_dir / "references" / "patterns" / "strategy.md").exists()
    assert (arch_dir / "references" / "patterns" / "observer.md").exists()

    # -- accessible-frontend skill: sibling vendored into generated tree --
    a11y_dir = skills_root / "accessible-frontend"
    a11y_skill = a11y_dir / "SKILL.md"
    assert a11y_skill.exists()
    skill_content = a11y_skill.read_text()
    # References rewritten from ../shared-references/ to shared-references/
    assert "../shared-references/" not in skill_content
    assert "shared-references/" in skill_content
    # Sibling tree vendored
    assert (a11y_dir / "shared-references" / "aria-patterns.md").exists()
    assert "Combobox" in (a11y_dir / "shared-references" / "aria-patterns.md").read_text()

    # -- shared-references also translated as its own skill --
    sr_dir = skills_root / "shared-references"
    assert (sr_dir / "SKILL.md").exists()

    # -- .DS_Store noise filtered --
    all_files = list(skills_root.rglob("*"))
    ds_store_files = [f for f in all_files if f.name == ".DS_Store"]
    assert len(ds_store_files) == 0

    # -- SKILL.md frontmatter name rewritten for all skills --
    assert "name: code-review" in cr_skill.read_text()
    assert "name: software-architecture" in (
        arch_dir / "SKILL.md"
    ).read_text()

    # -- config.toml is no longer generated --
    assert not (project_root / ".codex" / "config.toml").exists()

    # -- All agents have .toml files in global agents dir --
    agents_dir = codex_home / "agents"
    agent_files = sorted(p.name for p in agents_dir.glob("*.toml"))
    assert len(agent_files) == 3

    # Verify agent names appear in .toml filenames
    assert any("security-reviewer" in f for f in agent_files)
    assert any("a11y-reviewer" in f for f in agent_files)
    assert any("performance-reviewer" in f for f in agent_files)

    # -- Security reviewer agent .toml has full content --
    sec_agent = [p for p in agents_dir.glob("*security*")][0]
    sec_content = sec_agent.read_text()
    assert "SQL injection" in sec_content
    assert "nonces" in sec_content


def test_power_user_full_stack(make_project, tmp_path: Path):
    """A power user with all source types: plugin, user skills/agents, project skills/agents, CLAUDE.md.

    Verifies the routing split, content fidelity, naming conventions,
    global instructions bridging, and that all sources coexist without conflict.
    """
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    # Marketplace plugin
    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")

    # User skills (simple + with references)
    build_user_skill_simple(claude_home, "a8c-url-shorthand")
    build_user_skill_with_references(claude_home, "write-like-a-pirategoat")

    # User agent
    build_user_agent(claude_home, "thinking-partner", tools=("Read", "Glob", "Grep", "Bash"))

    # Project skills
    build_project_skill(project_root, "run-tests")
    build_project_skill(project_root, "deploy-staging")

    # Project agents
    build_project_agent(project_root, "code-reviewer", tools=("Read", "Glob", "Grep", "Bash", "Write"))
    build_project_agent(project_root, "test-writer", tools=("Read", "Write", "Bash"))

    # User CLAUDE.md
    build_user_claude_md(claude_home)

    assert reconcile(project_root, claude_home, codex_home) == 0

    # -- Plugin skills → global registry (bare name) --
    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()
    assert (codex_home / "skills" / "software-architecture" / "SKILL.md").exists()

    # -- User skills → global registry (bare name) --
    user_url = codex_home / "skills" / "a8c-url-shorthand" / "SKILL.md"
    assert user_url.exists()
    user_write = codex_home / "skills" / "write-like-a-pirategoat"
    assert (user_write / "SKILL.md").exists()
    # References dir preserved
    assert (user_write / "references" / "examples.md").exists()
    assert (user_write / "references" / "guidelines.md").exists()
    assert "advanced usage" in (user_write / "references" / "examples.md").read_text()

    # -- Project skills → project-local (raw name, no prefix) --
    assert (project_root / ".codex" / "skills" / "run-tests" / "SKILL.md").exists()
    assert (project_root / ".codex" / "skills" / "deploy-staging" / "SKILL.md").exists()
    # NOT in global registry
    assert not (codex_home / "skills" / "run-tests").exists()
    assert not (codex_home / "skills" / "project-run-tests").exists()

    # -- config.toml is no longer generated --
    assert not (project_root / ".codex" / "config.toml").exists()

    # -- Plugin and user agents → global agents dir (.toml files) --
    global_agents_dir = codex_home / "agents"
    global_agent_files = sorted(p.name for p in global_agents_dir.glob("*.toml"))
    # Plugin agents (3) + user agent (1) = 4 global agents
    assert len(global_agent_files) == 4
    assert any("security-reviewer" in f for f in global_agent_files)
    assert any("thinking-partner" in f for f in global_agent_files)

    # -- Project agents → project-local .codex/agents/ (.toml files) --
    project_agents_dir = project_root / ".codex" / "agents"
    project_agent_files = sorted(p.name for p in project_agents_dir.glob("*.toml"))
    assert len(project_agent_files) == 2
    assert any("code-reviewer" in f for f in project_agent_files)
    assert any("test-writer" in f for f in project_agent_files)

    # -- User CLAUDE.md → ~/.codex/AGENTS.md --
    global_agents = codex_home / "AGENTS.md"
    assert global_agents.exists()
    content = global_agents.read_text()
    assert "Conventional Commits" in content
    assert "Automattic" in content

    # -- Project shim untouched --
    assert (project_root / "CLAUDE.md").read_text() == "@AGENTS.md\n"

    # -- Source inputs untouched --
    assert (claude_home / "CLAUDE.md").exists()
    assert (claude_home / "skills" / "a8c-url-shorthand" / "SKILL.md").exists()
    assert (project_root / ".claude" / "skills" / "run-tests" / "SKILL.md").exists()


def test_setup_evolves_over_time(make_project, tmp_path: Path):
    """Bridge handles evolving setup: add sources, update content, remove sources.

    Runs reconcile multiple times and verifies artifacts are correctly
    created, updated, and cleaned up at each step.
    """
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    # --- Phase 1: Plugin only ---
    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    assert reconcile(project_root, claude_home, codex_home) == 0

    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()
    # Plugin agents land in global agents dir
    assert any("security-reviewer" in p.name for p in (codex_home / "agents").glob("*.toml"))
    assert not (codex_home / "AGENTS.md").exists()  # no user CLAUDE.md yet

    # --- Phase 2: Add user skill + CLAUDE.md ---
    build_user_skill_simple(claude_home, "url-shorthand")
    build_user_claude_md(claude_home)
    assert reconcile(project_root, claude_home, codex_home) == 0

    assert (codex_home / "skills" / "url-shorthand" / "SKILL.md").exists()
    assert (codex_home / "AGENTS.md").exists()
    assert "Conventional Commits" in (codex_home / "AGENTS.md").read_text()
    # Plugin skills still present
    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()

    # --- Phase 3: Update CLAUDE.md content ---
    (claude_home / "CLAUDE.md").write_text(
        "# Updated Instructions\n\nAlways use TypeScript strict mode.\n"
    )
    assert reconcile(project_root, claude_home, codex_home) == 0

    updated_content = (codex_home / "AGENTS.md").read_text()
    assert "TypeScript strict mode" in updated_content
    assert "Conventional Commits" not in updated_content  # old content replaced

    # --- Phase 4: Add project agent ---
    build_project_agent(project_root, "reviewer", tools=("Read", "Grep"))
    assert reconcile(project_root, claude_home, codex_home) == 0

    # Project agent lands in project-local .codex/agents/
    project_agents_dir = project_root / ".codex" / "agents"
    assert any("reviewer" in p.name for p in project_agents_dir.glob("*.toml"))
    # Plugin agents still in global agents dir
    assert any("security-reviewer" in p.name for p in (codex_home / "agents").glob("*.toml"))

    # --- Phase 5: Plugin version bumps (old version removed, new installed) ---
    import shutil
    shutil.rmtree(cache_root / "vlad-plugins" / "review-tools" / "1.12.0")
    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.13.0")
    assert reconcile(project_root, claude_home, codex_home) == 0

    # Skills still present (same content, new version path)
    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()
    # Project agent survived plugin change
    assert any("reviewer" in p.name for p in (project_root / ".codex" / "agents").glob("*.toml"))


def test_two_projects_share_user_setup(make_project, tmp_path: Path):
    """Two projects share the same user-level skills and CLAUDE.md.

    The global skill registry correctly shares ownership. Project-local
    artifacts (config.toml, prompt files, project skills) are isolated.
    Removing one project's claim does not remove the shared skill.
    """
    project_a, _agents_a = make_project("project-a")
    project_b, _agents_b = make_project("project-b")
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    # Shared user setup
    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_skill_simple(claude_home, "url-shorthand")
    build_user_claude_md(claude_home)

    # Project A has its own project agent
    build_project_agent(project_a, "deployer", tools=("Bash",))

    # Project B has its own project skill
    build_project_skill(project_b, "lint-check")

    # Reconcile both projects
    assert reconcile(project_a, claude_home, codex_home) == 0
    assert reconcile(project_b, claude_home, codex_home) == 0

    # -- Global skills shared (single copy) --
    user_skill = codex_home / "skills" / "url-shorthand" / "SKILL.md"
    assert user_skill.exists()
    plugin_skill = codex_home / "skills" / "code-review" / "SKILL.md"
    assert plugin_skill.exists()

    # -- Global CLAUDE.md shared --
    assert (codex_home / "AGENTS.md").exists()

    # -- Project A has its agent, not project B's skill --
    project_a_agents = project_a / ".codex" / "agents"
    assert any("deployer" in p.name for p in project_a_agents.glob("*.toml"))
    assert not (project_a / ".codex" / "skills" / "lint-check").exists()

    # -- Project B has its skill, not project A's agent --
    project_b_agents = project_b / ".codex" / "agents"
    assert not project_b_agents.exists() or not any("deployer" in p.name for p in project_b_agents.glob("*.toml"))
    assert (project_b / ".codex" / "skills" / "lint-check" / "SKILL.md").exists()

    # -- Plugin agents are in the global agents dir (shared) --
    global_agents_dir = codex_home / "agents"
    assert any("security-reviewer" in p.name for p in global_agents_dir.glob("*.toml"))

    # -- Re-reconcile project A without the plugin → shared skill survives --
    import shutil
    shutil.rmtree(cache_root / "vlad-plugins")
    (cache_root / "vlad-plugins").mkdir(parents=True)  # empty marketplace
    assert reconcile(project_a, claude_home, codex_home) == 0

    # Plugin skills still exist because project B still claims them
    assert plugin_skill.exists()

    # User skill still exists — both projects still claim it
    assert user_skill.exists()


def test_selective_exclusion_via_config_and_cli(make_project, tmp_path: Path):
    """Exclude specific skills and agents via bridge.toml and CLI flags.

    Verifies:
    - Excluded plugin skill is not in global registry
    - Excluded plugin agent is not in config.toml or prompt files
    - Non-excluded sources are unaffected
    - CLI --exclude-plugin removes entire plugin
    """
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_skill_simple(claude_home, "url-shorthand")
    build_project_agent(project_root, "reviewer", tools=("Read", "Grep"))

    # bridge.toml: exclude one plugin skill and one plugin agent
    build_bridge_toml(project_root, (
        "[exclude]\n"
        'skills = ["vlad-plugins/review-tools/software-architecture"]\n'
        'agents = ["vlad-plugins/review-tools/performance-reviewer.md"]\n'
    ))

    assert reconcile(project_root, claude_home, codex_home) == 0

    # -- Excluded skill NOT in global registry --
    assert not (codex_home / "skills" / "software-architecture").exists()

    # -- Other skills still present --
    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()
    assert (codex_home / "skills" / "url-shorthand" / "SKILL.md").exists()

    # -- Excluded agent NOT in global or project agent dirs --
    global_agents_dir = codex_home / "agents"
    global_agent_names = [p.name for p in global_agents_dir.glob("*.toml")]
    assert not any("performance" in n for n in global_agent_names)

    # -- Other plugin/user agents still present in global agents dir --
    assert any("security-reviewer" in n for n in global_agent_names)
    assert any("a11y-reviewer" in n for n in global_agent_names)

    # -- Project agent still present in project-local agents dir --
    project_agents_dir = project_root / ".codex" / "agents"
    project_agent_names = [p.name for p in project_agents_dir.glob("*.toml")]
    assert any("reviewer" in n for n in project_agent_names)

    # Total non-excluded agents: security + a11y (global) + project reviewer (local)
    assert len(global_agent_names) == 2
    assert len(project_agent_names) == 1

    # --- Now test CLI --exclude-plugin overrides ---
    exit_code = cli.main([
        "reconcile",
        "--project", str(project_root),
        "--claude-home", str(claude_home),
        "--codex-home", str(codex_home),
        "--exclude-plugin", "vlad-plugins/review-tools",
    ])
    assert exit_code == 0

    # -- Plugin skills removed from global registry --
    assert not (codex_home / "skills" / "code-review").exists()
    assert not (codex_home / "skills" / "accessible-frontend").exists()

    # -- User skill and project agent still present --
    assert (codex_home / "skills" / "url-shorthand" / "SKILL.md").exists()
    project_agents_after = [p.name for p in (project_root / ".codex" / "agents").glob("*.toml")]
    assert any("reviewer" in n for n in project_agents_after)
    # Plugin agents removed from global agents dir
    global_agents_after = list((codex_home / "agents").glob("*.toml")) if (codex_home / "agents").exists() else []
    assert not any("security" in p.name for p in global_agents_after)


# ===========================================================================
# Clean and uninstall scenario tests
# ===========================================================================


def test_clean_undoes_reconcile(make_project, tmp_path: Path):
    """Reconcile a full setup, clean it, verify everything is removed.

    After clean:
    - All managed project files are gone (config.toml, prompts, CLAUDE.md, state file)
    - Global skills owned only by this project are deleted
    - AGENTS.md (hand-authored) survives
    - bridge.toml (hand-authored) survives
    - Re-running clean is a no-op
    """
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    # Full setup
    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_skill_simple(claude_home, "url-shorthand")
    build_user_claude_md(claude_home)
    build_project_agent(project_root, "reviewer", tools=("Read", "Grep"))
    build_project_skill(project_root, "run-tests")
    build_bridge_toml(project_root, '[exclude]\nplugins = []\n')

    assert reconcile(project_root, claude_home, codex_home) == 0

    # Verify artifacts exist
    assert (project_root / "CLAUDE.md").exists()
    assert _bridge_state_path(project_root, tmp_path).exists()
    assert (project_root / ".codex" / "skills" / "run-tests" / "SKILL.md").exists()
    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()
    assert (codex_home / "skills" / "url-shorthand" / "SKILL.md").exists()
    assert (codex_home / "AGENTS.md").exists()
    # Project agent .toml file exists
    project_agents_dir = project_root / ".codex" / "agents"
    assert len(list(project_agents_dir.glob("*.toml"))) > 0
    # Global agent .toml files exist (plugin agents)
    assert len(list((codex_home / "agents").glob("*.toml"))) > 0

    # Clean
    exit_code = cli.main([
        "clean",
        "--project", str(project_root),
    ])
    assert exit_code == 0

    # All managed project files gone
    assert not (project_root / "CLAUDE.md").exists()
    assert not _bridge_state_path(project_root, tmp_path).exists()
    assert not (project_root / ".codex" / "skills" / "run-tests").exists()
    # Project agent .toml files removed
    assert not project_agents_dir.exists() or len(list(project_agents_dir.glob("*.toml"))) == 0

    # Global skills removed (this project was the only owner)
    assert not (codex_home / "skills" / "code-review").exists()
    assert not (codex_home / "skills" / "url-shorthand").exists()

    # Hand-authored files survive
    assert (project_root / "AGENTS.md").exists()
    assert (project_root / ".codex" / "bridge.toml").exists()

    # Global AGENTS.md untouched (clean doesn't touch it)
    assert (codex_home / "AGENTS.md").exists()

    # Re-running clean is a no-op
    exit_code = cli.main([
        "clean",
        "--project", str(project_root),
    ])
    assert exit_code == 0


def test_clean_dry_run_previews_without_side_effects(make_project, tmp_path: Path):
    """clean --dry-run lists removals but does not delete anything."""
    project_root, _agents_md = make_project()
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_skill_simple(claude_home, "url-shorthand")

    assert reconcile(project_root, claude_home, codex_home) == 0

    exit_code = cli.main([
        "clean",
        "--project", str(project_root),
        "--dry-run",
    ])
    assert exit_code == 0

    # Everything still exists
    assert (project_root / "CLAUDE.md").exists()
    assert _bridge_state_path(project_root, tmp_path).exists()
    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()
    assert (codex_home / "skills" / "url-shorthand" / "SKILL.md").exists()


def test_uninstall_cleans_entire_machine(make_project, tmp_path: Path):
    """Set up two projects with shared skills, uninstall removes everything."""
    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()

    # Shared setup
    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_skill_simple(claude_home, "url-shorthand")
    build_user_claude_md(claude_home)

    # Project-specific sources
    build_project_agent(project_a, "deployer", tools=("Bash",))
    build_project_skill(project_b, "lint-check")

    assert reconcile(project_a, claude_home, codex_home) == 0
    assert reconcile(project_b, claude_home, codex_home) == 0

    # Plant a bridge LaunchAgent plist
    (la_dir / "com.openai.codex-bridge.project-a.abc123.plist").write_bytes(b"<plist/>")

    # Verify everything exists
    assert _bridge_state_path(project_a, tmp_path).exists()
    assert _bridge_state_path(project_b, tmp_path).exists()
    assert (codex_home / "skills" / "code-review" / "SKILL.md").exists()
    assert (codex_home / "skills" / "url-shorthand" / "SKILL.md").exists()
    assert (codex_home / "AGENTS.md").exists()

    # Uninstall
    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
        "--launchagents-dir", str(la_dir),
    ])
    assert exit_code == 0

    # Both projects cleaned
    assert not _bridge_state_path(project_a, tmp_path).exists()
    assert not (project_a / "CLAUDE.md").exists()
    assert not _bridge_state_path(project_b, tmp_path).exists()
    assert not (project_b / "CLAUDE.md").exists()

    # Global artifacts removed
    assert not (codex_home / "skills" / "code-review").exists()
    assert not (codex_home / "skills" / "url-shorthand").exists()
    assert not (codex_home / "AGENTS.md").exists()
    from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    assert not (bridge_home / GLOBAL_REGISTRY_FILENAME).exists()

    # LaunchAgent plist removed
    assert not (la_dir / "com.openai.codex-bridge.project-a.abc123.plist").exists()

    # AGENTS.md (hand-authored) survives on both projects
    assert (project_a / "AGENTS.md").exists()
    assert (project_b / "AGENTS.md").exists()


def test_uninstall_dry_run_json_structure(make_project, tmp_path: Path, capsys):
    """uninstall --dry-run --json produces valid structured JSON."""
    import json as json_mod

    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"
    la_dir = tmp_path / "LaunchAgents"
    la_dir.mkdir()

    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_claude_md(claude_home)

    assert reconcile(project_a, claude_home, codex_home) == 0
    assert reconcile(project_b, claude_home, codex_home) == 0
    capsys.readouterr()  # discard reconcile output

    (la_dir / "com.openai.codex-bridge.test.abc.plist").write_bytes(b"<plist/>")

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
        "--launchagents-dir", str(la_dir),
        "--dry-run",
        "--json",
    ])
    assert exit_code == 0

    captured = capsys.readouterr()
    data = json_mod.loads(captured.out)

    # Structure validation
    assert isinstance(data["projects"], list)
    assert len(data["projects"]) == 2
    for project in data["projects"]:
        assert "root" in project
        assert project["status"] in ("will_clean", "not_found", "no_state")
        assert "removals" in project

    assert isinstance(data["global"], dict)
    assert "skills" in data["global"]
    assert "agents_md" in data["global"]
    assert "registry" in data["global"]

    assert isinstance(data["launchagents"], list)
    assert len(data["launchagents"]) == 1
    assert "path" in data["launchagents"][0]
    assert "bootout_command" in data["launchagents"][0]


def test_uninstall_skips_vanished_project_cleans_rest(make_project, tmp_path: Path):
    """When a project root no longer exists, uninstall skips it and cleans the rest."""
    import shutil as shutil_mod

    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_skill_simple(claude_home, "url-shorthand")

    assert reconcile(project_a, claude_home, codex_home) == 0
    assert reconcile(project_b, claude_home, codex_home) == 0

    # Delete project A entirely
    shutil_mod.rmtree(project_a)
    assert not project_a.exists()

    exit_code = cli.main([
        "uninstall",
        "--codex-home", str(codex_home),
    ])
    assert exit_code == 0

    # Project B was cleaned
    assert not _bridge_state_path(project_b, tmp_path).exists()
    assert not (project_b / "CLAUDE.md").exists()

    # Global skills fully removed (even skills owned by the vanished project)
    assert not (codex_home / "skills" / "code-review").exists()
    assert not (codex_home / "skills" / "url-shorthand").exists()

    # Global registry removed
    from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    assert not (bridge_home / GLOBAL_REGISTRY_FILENAME).exists()


# ===========================================================================
# reconcile --all scenario tests
# ===========================================================================


def test_reconcile_all_reconciles_registered_projects(make_project, tmp_path: Path):
    """Two projects reconciled individually, then reconcile --all updates both."""
    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")
    build_user_skill_simple(claude_home, "url-shorthand")

    # Register both projects
    assert reconcile(project_a, claude_home, codex_home) == 0
    assert reconcile(project_b, claude_home, codex_home) == 0

    # Verify both in registry
    import json as json_mod
    from cc_codex_bridge.registry import GLOBAL_REGISTRY_FILENAME
    bridge_home = tmp_path / "home" / ".cc-codex-bridge"
    reg = json_mod.loads((bridge_home / GLOBAL_REGISTRY_FILENAME).read_text())
    assert str(project_a) in reg["projects"]
    assert str(project_b) in reg["projects"]

    # Run reconcile --all
    exit_code = cli.main(["reconcile", "--all", "--codex-home", str(codex_home)])
    assert exit_code == 0

    # Both projects have artifacts (state file confirms reconcile ran)
    assert _bridge_state_path(project_a, tmp_path).exists()
    assert _bridge_state_path(project_b, tmp_path).exists()


def test_reconcile_all_handles_missing_project(make_project, tmp_path: Path):
    """reconcile --all reports error for deleted project, reconciles the rest."""
    import shutil as shutil_mod

    project_a, _ = make_project("project-a")
    project_b, _ = make_project("project-b")
    claude_home = tmp_path / "claude-home"
    codex_home = tmp_path / "codex-home"
    cache_root = claude_home / "plugins" / "cache"

    build_plugin(cache_root, "vlad-plugins", "review-tools", "1.12.0")

    assert reconcile(project_a, claude_home, codex_home) == 0
    assert reconcile(project_b, claude_home, codex_home) == 0

    # Delete project A
    shutil_mod.rmtree(project_a)

    # reconcile --all should report error but exit 1 (partial failure)
    exit_code = cli.main(["reconcile", "--all", "--codex-home", str(codex_home)])
    assert exit_code == 1

    # Project B was still reconciled successfully
    assert _bridge_state_path(project_b, tmp_path).exists()

"""Scenario-driven e2e tests with realistic mock trees.

Each test models a real-world usage story: a user with a specific setup
runs the bridge and gets a working Codex environment. Builders create
mock trees that match production-observed structures.
"""

from __future__ import annotations

import os
from pathlib import Path

from cc_codex_bridge import cli


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
    cr_dir = skills_root / "vlad-plugins-review-tools-code-review"
    cr_skill = cr_dir / "SKILL.md"
    assert cr_skill.exists()
    assert "multi-agent code review" in cr_skill.read_text()
    cr_script = cr_dir / "scripts" / "run-review.py"
    assert cr_script.exists()
    assert cr_script.stat().st_mode & 0o111  # executable bit preserved

    # -- software-architecture skill: companion files + nested references --
    arch_dir = skills_root / "vlad-plugins-review-tools-software-architecture"
    assert (arch_dir / "SKILL.md").exists()
    assert (arch_dir / "solid-principles.md").exists()
    assert "Single Responsibility" in (arch_dir / "solid-principles.md").read_text()
    assert (arch_dir / "references" / "patterns" / "strategy.md").exists()
    assert (arch_dir / "references" / "patterns" / "observer.md").exists()

    # -- accessible-frontend skill: sibling vendored into generated tree --
    a11y_dir = skills_root / "vlad-plugins-review-tools-accessible-frontend"
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
    sr_dir = skills_root / "vlad-plugins-review-tools-shared-references"
    assert (sr_dir / "SKILL.md").exists()

    # -- .DS_Store noise filtered --
    all_files = list(skills_root.rglob("*"))
    ds_store_files = [f for f in all_files if f.name == ".DS_Store"]
    assert len(ds_store_files) == 0

    # -- SKILL.md frontmatter name rewritten for all skills --
    assert "name: vlad-plugins-review-tools-code-review" in cr_skill.read_text()
    assert "name: vlad-plugins-review-tools-software-architecture" in (
        arch_dir / "SKILL.md"
    ).read_text()

    # -- All agents in config.toml with correct tools --
    config = (project_root / ".codex" / "config.toml").read_text()
    assert "vlad-plugins_review-tools_security_reviewer" in config
    assert "vlad-plugins_review-tools_a11y_reviewer" in config
    assert "vlad-plugins_review-tools_performance_reviewer" in config

    # -- Prompt files exist for all agents --
    prompts = project_root / ".codex" / "prompts" / "agents"
    prompt_files = sorted(p.name for p in prompts.glob("*.md"))
    assert len(prompt_files) == 3

    # -- Security reviewer prompt has full content --
    sec_prompt = [p for p in prompts.glob("*security*")][0]
    sec_content = sec_prompt.read_text()
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

    # -- Plugin skills → global registry (marketplace-prefixed) --
    assert (codex_home / "skills" / "vlad-plugins-review-tools-code-review" / "SKILL.md").exists()
    assert (codex_home / "skills" / "vlad-plugins-review-tools-software-architecture" / "SKILL.md").exists()

    # -- User skills → global registry (user-prefixed) --
    user_url = codex_home / "skills" / "user-a8c-url-shorthand" / "SKILL.md"
    assert user_url.exists()
    user_write = codex_home / "skills" / "user-write-like-a-pirategoat"
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

    # -- All agents in config.toml --
    config = (project_root / ".codex" / "config.toml").read_text()
    # Plugin agents (marketplace-prefixed)
    assert "vlad-plugins_review-tools_security_reviewer" in config
    # User agent (scope-prefixed)
    assert "user_thinking_partner" in config
    # Project agents (scope-prefixed)
    assert "project_code_reviewer" in config
    assert "project_test_writer" in config

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
